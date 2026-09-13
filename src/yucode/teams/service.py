"""Team 领域总入口。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
import asyncio
from contextlib import suppress
import base64
import sys

from yucode.config import TeamConfig
from yucode.teams.backends import BackendSelector, TeamBackendDriver, TeamBackendError
from yucode.teams.identity import team_storage_root, validate_name
from yucode.teams.mailbox import MailboxStore
from yucode.teams.models import AgentTeam, MemberState, MessageKind, TeamBackend, TeamMember, TeamTaskState
from yucode.teams.repository import TeamRepository
from yucode.teams.tasks import TeamTaskStore
from yucode.worktrees.manager import WorktreeManager
from yucode.teams.merge import TeamMergeResult, TeamMergeService
from yucode.subagents.factory import SubagentFactory
from yucode.subagents.runner import RunToCompletion
from yucode.cancellation import Cancellation
from yucode.sessions import SessionManager
from yucode.conversation import Conversation
from yucode.teams.identity import resolve_member_root
from yucode.worktrees.slug import parse_worktree_slug, resolve_worktree_path
from yucode.permissions import ApprovalChoice


class TeamServiceError(ValueError):
    pass


class TeamService:
    def __init__(self, config: TeamConfig, workspace_root: Path, selector: BackendSelector,
                 drivers: tuple[TeamBackendDriver, ...], worktrees: WorktreeManager | None = None,
                 storage_root: Path | None = None) -> None:
        self.config = config; self._workspace = workspace_root.resolve(); self._selector = selector
        self._drivers = {item.backend: item for item in drivers}; self._worktrees = worktrees
        self.repository = TeamRepository(storage_root if storage_root is not None else team_storage_root(self._workspace))
        self.tasks = TeamTaskStore(); self.mailbox = MailboxStore(config.mailbox_lock_timeout_seconds)
        self._merger = TeamMergeService(self._workspace, scaffolding=getattr(worktrees, "scaffolding_paths", ()))
        self._parent = None; self._factory: SubagentFactory | None = None; self._running: dict[str, asyncio.Task[None]] = {}
        self._notifications: list[str] = []
        self._notification_listener = None

    def set_notification_listener(self, listener) -> None:
        self._notification_listener = listener

    def bind_parent(self, parent, factory: SubagentFactory) -> None:
        """绑定 Lead；只有 Lead 可启动具备独立 Conversation 的成员。"""
        self._parent = parent; self._factory = factory

    def list(self) -> tuple[AgentTeam, ...]:
        return self.repository.list()

    def get(self, name: str) -> AgentTeam:
        team = self.repository.load(name)
        for member in team.members:
            if member.writable != (member.worktree_slug is not None):
                raise TeamServiceError(
                    f"成员 {member.name} 的 writable 与 Worktree 登记不一致，已拒绝使用。"
                )
            try:
                expected = self._workspace if member.worktree_slug is None else resolve_worktree_path(
                    self._workspace, parse_worktree_slug(member.worktree_slug)
                )
            except Exception as error:
                raise TeamServiceError(f"成员 {member.name} 的 Worktree 登记无效：{error}") from error
            if member.workspace_root.resolve() != expected.resolve():
                raise TeamServiceError(f"成员 {member.name} 的工作目录与受控登记不一致，已拒绝使用。")
        return team

    def backend_diagnostics(self):
        """返回配置优先级中每个后端的实时探测结果，供工具和命令透明展示。"""
        return self._selector.inspect(self.config.backend_priority)

    def create(self, name: str, lead_id: str, members: tuple[dict, ...]) -> AgentTeam:
        if not self.config.enabled:
            raise TeamServiceError("当前配置未启用 Agent Team。")
        validate_name(name, "团队名称"); validate_name(lead_id, "负责人")
        if not members:
            raise TeamServiceError("团队至少需要一名成员。")
        built: list[TeamMember] = []
        seen: set[str] = set()
        try:
            for raw in members:
                member = self._member(name, raw)
                if member.name in seen:
                    raise TeamServiceError("团队成员名称不能重复。")
                seen.add(member.name); built.append(member)
        except Exception:
            if self._worktrees is not None:
                for member in built:
                    if member.worktree_slug:
                        # 回滚清理失败不能覆盖真正的创建失败原因。
                        with suppress(Exception):
                            self._worktrees.remove(member.worktree_slug, automatic=True)
            raise
        now = datetime.now(UTC)
        provisional = AgentTeam(1, name, lead_id, Path(), tuple(built), now, now)
        saved = self.repository.create(provisional)
        return saved

    def _member(self, team_name: str, raw: dict) -> TeamMember:
        if not isinstance(raw, dict):
            raise TeamServiceError("成员花名册必须由对象组成。")
        name = validate_name(raw.get("name"), "成员名称")
        role = raw.get("role", "general")
        if not isinstance(role, str) or not role.strip():
            raise TeamServiceError("成员角色必须是非空字符串。")
        writable = raw.get("writable", False); approval = raw.get("requires_approval", False)
        if not isinstance(writable, bool) or not isinstance(approval, bool):
            raise TeamServiceError("writable 和 requires_approval 必须是布尔值。")
        if approval and not writable:
            raise TeamServiceError("只读成员不需要写入审批；requires_approval 仅适用于 writable=true 的成员。")
        requested = raw.get("backend")
        try:
            driver = self._selector.select(self.config.backend_priority, TeamBackend(requested) if requested is not None else None)
        except (ValueError, TeamBackendError) as error:
            raise TeamServiceError(str(error)) from error
        workspace = self._workspace; slug = None
        if writable:
            if self._worktrees is None:
                raise TeamServiceError("当前没有可用的 Worktree 服务，不能创建可写成员。")
            try:
                record = self._worktrees.create(f"team/{team_name}-{name}-{uuid4().hex[:8]}", temporary=True)
            except Exception as error:
                raise TeamServiceError(f"无法创建成员 Worktree：{error}") from error
            workspace = record.path; slug = record.slug
        return TeamMember(name, uuid4().hex, role.strip(), workspace, driver.backend, approval,
                          worktree_slug=slug, writable=writable)

    async def send(self, team_name: str, sender: str, recipients: tuple[str, ...], body: str,
                   kind: MessageKind = MessageKind.TEXT, summary: str | None = None,
                   protocol: dict[str, str] | None = None) -> tuple[str, ...]:
        team = self.get(team_name)
        resume_after: list[tuple[str, str, str | None]] = []
        submitted_member: TeamMember | None = None
        decisions: list[tuple[TeamMember, str, str]] = []
        if kind is MessageKind.PLAN_REQUEST:
            request_id = (protocol or {}).get("request_id")
            member = next((item for item in team.members if item.name == sender), None)
            if member is None or not member.requires_approval or request_id != member.approval_request_id or recipients != (team.lead_id,):
                raise TeamServiceError("计划请求必须由待审批成员发送给 Lead，并携带当前 request_id。")
            submitted_member = member
        if kind is MessageKind.PLAN_DECISION:
            request_id = (protocol or {}).get("request_id"); decision = (protocol or {}).get("decision")
            if sender != team.lead_id or decision not in {"approved", "rejected"} or not request_id:
                raise TeamServiceError("计划审批消息必须由 Lead 发送，并包含 request_id 与 approved/rejected 决定。")
            for recipient in recipients:
                member = next((item for item in team.members if item.name == recipient), None)
                if (member is None or member.approval_request_id != request_id
                        or member.plan_submitted_request_id != request_id):
                    raise TeamServiceError("计划审批请求与目标成员当前请求不匹配。")
                decisions.append((member, request_id, decision))
        written = self.mailbox.send(team, sender, recipients, body, kind=kind, summary=summary, protocol=protocol)
        if submitted_member is not None:
            team = self.update_member(team, replace(submitted_member, plan_submitted_request_id=(protocol or {})["request_id"]))
        for member, request_id, decision in decisions:
            if decision == "approved" and member.pending_prompt:
                team = self.update_member(team, replace(member, approved_request_id=request_id))
                resume_after.append((member.name, member.pending_prompt, member.pending_task_id))
            elif decision == "rejected":
                team = self.update_member(team, replace(member, pending_prompt=None, pending_task_id=None,
                                                        approval_request_id=None, approved_request_id=None,
                                                        plan_submitted_request_id=None))
        wake_for_work: list[tuple[str, str]] = []
        for message in written:
            target = next((item for item in team.members if item.name == message.recipients[0]), None)
            if target is None:
                continue
            if kind in {MessageKind.TEXT, MessageKind.REMINDER, MessageKind.TASK_UPDATE} and target.state in {MemberState.CREATED, MemberState.IDLE, MemberState.STOPPED}:
                wake_for_work.append((target.name, body))
            try:
                await self._drivers[target.backend].wake(target)
            except Exception:
                # 消息已持久化，成员下次启动仍会读取，不能将投递伪装成失败。
                pass
        for member_name, pending_prompt, pending_task_id in resume_after:
            await self.spawn(team_name, member_name, pending_prompt, task_id=pending_task_id)
        for member_name, message_prompt in wake_for_work:
            await self.spawn(team_name, member_name, message_prompt)
        return tuple(item.message_id for item in written)

    def update_member(self, team: AgentTeam, member: TeamMember) -> AgentTeam:
        return self.repository.update_member(team.name, member, datetime.now(UTC))

    async def stop(self, team_name: str, member_name: str) -> AgentTeam:
        team = self.get(team_name)
        member = next((item for item in team.members if item.name == member_name), None)
        if member is None:
            raise TeamServiceError(f"找不到成员：{member_name}。")
        await self.send(team.name, team.lead_id, (member.name,), "Lead 已请求停止当前工作。", MessageKind.SHUTDOWN)
        worker = self._running.pop(member.agent_id, None)
        if worker is not None and not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        if member.backend is not TeamBackend.IN_PROCESS:
            await self._drivers[member.backend].stop(member)
        return self.update_member(team, replace(member, state=MemberState.STOPPED))

    async def spawn(self, team_name: str, member_name: str, prompt: str, *, local_process: bool = False,
                    task_id: str | None = None) -> AgentTeam:
        team = self.get(team_name)
        member = next((item for item in team.members if item.name == member_name), None)
        if member is None:
            raise TeamServiceError(f"找不到成员：{member_name}。")
        if not isinstance(prompt, str) or not prompt.strip():
            raise TeamServiceError("成员任务必须是非空字符串。")
        if task_id is not None:
            task = self.tasks.get(team, task_id)
            if task.assignee not in {None, member_name}:
                raise TeamServiceError("任务已分配给其他成员。")
            if task.state is TeamTaskState.READY:
                self.tasks.update(team, task_id, state=TeamTaskState.IN_PROGRESS, assignee=member_name)
            elif task.state is not TeamTaskState.IN_PROGRESS:
                raise TeamServiceError(f"任务当前不可执行：{task.state.value}。")
        if member.state is MemberState.RUNNING and not local_process:
            raise TeamServiceError(f"成员 {member.name} 正在运行。")
        if member.backend is not TeamBackend.IN_PROCESS and not local_process:
            encoded = base64.urlsafe_b64encode(prompt.encode("utf-8")).decode("ascii")
            command = (sys.executable, "-m", "yucode.cli", "--team-member", team_name, member_name, encoded, task_id or "-")
            driver = self._drivers[member.backend]
            handle = await driver.start(member, command)
            return self.update_member(team, replace(member, state=MemberState.RUNNING, backend_handle=handle.value))
        if self._parent is None or self._factory is None:
            raise TeamServiceError("Team 服务尚未绑定 Lead，不能启动成员。")
        write_allowed = member.writable and (
            not member.requires_approval or (
                member.approval_request_id is not None and member.approved_request_id == member.approval_request_id
            )
        )
        effective_prompt = prompt
        if member.requires_approval and not write_allowed:
            request_id = uuid4().hex
            member = replace(member, state=MemberState.RUNNING, approval_request_id=request_id,
                             approved_request_id=None, pending_prompt=prompt, pending_task_id=task_id,
                             plan_submitted_request_id=None)
            effective_prompt = (f"请先调研并制定计划，不得修改文件。完成后调用 SendMessage，kind=plan_request，"
                                f"protocol.request_id={request_id}，收件人为 {team.lead_id}。原任务：\n{prompt}")
        else:
            member = replace(member, state=MemberState.RUNNING, pending_prompt=None, pending_task_id=None)
        running = self.update_member(team, member)
        transcript_dir = resolve_member_root(team.root, member.name) / "transcripts"
        sessions = SessionManager(member.workspace_root, directory=transcript_dir)
        conversation = Conversation(sessions.record_event)
        if member.transcript_id:
            try:
                recovered = sessions.recover(member.transcript_id)
                sessions.activate_session(member.transcript_id)
                conversation.replace_for_recovery(recovered.messages)
            except Exception as error:
                latest = self.get(team_name)
                current = next(item for item in latest.members if item.name == member_name)
                self.update_member(latest, replace(current, state=MemberState.FAILED))
                notice = f"成员 {member_name} 的 transcript 恢复失败，原文件和未读消息已保留：{error}"
                self._notifications.append(notice)
                raise TeamServiceError(f"无法恢复成员 transcript：{error}") from error
        else:
            transcript_id = sessions.create_session()
            running = self.update_member(running, replace(next(item for item in running.members if item.name == member_name), transcript_id=transcript_id))
            member = next(item for item in running.members if item.name == member_name)
        # 只有上下文恢复成功后才确认邮件，恢复失败时保留未读状态以便修复后重试。
        unread = self.mailbox.unread_for(running, member)
        if unread:
            mailbox_context = "\n\n".join(f"来自 {item.sender} 的 {item.kind.value} 消息：\n{item.body}" for item in unread)
            effective_prompt = f"{effective_prompt}\n\n<incoming-messages>\n{mailbox_context}\n</incoming-messages>"
            self.mailbox.mark_read(running, member, tuple(item.message_id for item in unread))
        from yucode.teams.tools import TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool, SendMessageTool
        member_tools = (TaskCreateTool(self, team_name, member.name), TaskGetTool(self, team_name, member.name),
                        TaskListTool(self, team_name, member.name), TaskUpdateTool(self, team_name, member.name),
                        SendMessageTool(self, team_name, member.name))
        child = self._factory.create_team_member(self._parent, conversation, member.workspace_root, member_tools, member.role, write_allowed=write_allowed)

        async def approve_delegated_work(_request):
            # TeamSpawn（以及需要时的匹配 PLAN_DECISION）已明确授权这一轮委派。
            # PermissionManager 仍会先执行危险命令、目录边界、规则拒绝和工作流检查。
            return ApprovalChoice.ONCE

        async def work() -> None:
            try:
                outcome = await RunToCompletion().run(child, effective_prompt, Cancellation(), approve_delegated_work)
                latest = self.get(team_name)
                current = next(item for item in latest.members if item.name == member_name)
                state = MemberState.IDLE if outcome.status.value == "completed" else MemberState.FAILED
                if state is MemberState.IDLE and current.worktree_slug is not None and write_allowed:
                    try:
                        commit = await asyncio.to_thread(self._merger.commit_member, member_name, current.workspace_root)
                        if commit:
                            outcome = replace(outcome, summary=f"{outcome.summary}\n\n成员改动已提交：{commit}")
                    except Exception as error:
                        state = MemberState.FAILED
                        outcome = replace(outcome, summary=f"{outcome.summary}\n\n成员改动提交失败：{error}")
                if task_id is not None and (not member.requires_approval or write_allowed):
                    self.tasks.update(latest, task_id, state=TeamTaskState.COMPLETED if state is MemberState.IDLE else TeamTaskState.READY)
                self.update_member(latest, replace(current, state=state))
                await self.send(team_name, member_name, (), outcome.summary, MessageKind.BROADCAST, "成员任务已结束。")
                self._notifications.append(f"成员 {member_name} 已{('完成' if state is MemberState.IDLE else '失败')}：\n{outcome.summary}")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                latest = self.get(team_name)
                current = next(item for item in latest.members if item.name == member_name)
                self.update_member(latest, replace(current, state=MemberState.FAILED))
                if task_id is not None:
                    with suppress(Exception):
                        self.tasks.update(latest, task_id, state=TeamTaskState.READY)
                summary = f"成员 {member_name} 运行失败：{error}"
                with suppress(Exception):
                    await self.send(team_name, member_name, (), summary, MessageKind.BROADCAST, "成员运行失败。")
                self._notifications.append(summary)
            finally:
                self._running.pop(member.agent_id, None)

        self._running[member.agent_id] = asyncio.create_task(work())
        return running

    async def wait_member(self, agent_id: str) -> None:
        worker = self._running.get(agent_id)
        if worker is not None:
            await worker

    def drain_notifications(self) -> tuple[str, ...]:
        items = tuple(self._notifications); self._notifications.clear(); return items

    def poll_lead_notifications(self) -> tuple[str, ...]:
        """从磁盘读取 Lead 邮箱，因此独立 pane 的结果也能被主进程看到。"""
        result: list[str] = []
        for team in self.list():
            lead = TeamMember(team.lead_id, team.lead_id, "lead", self._workspace, TeamBackend.IN_PROCESS)
            unread = self.mailbox.unread_for(team, lead)
            if not unread:
                continue
            result.extend(f"团队 {team.name} · 来自 {item.sender}：\n{item.body}" for item in unread)
            self.mailbox.mark_read(team, lead, tuple(item.message_id for item in unread))
        return tuple(result)

    def delete(self, name: str) -> None:
        team = self.get(name)
        active = [item.name for item in team.members if item.state in {MemberState.RUNNING, MemberState.IDLE}]
        if active:
            raise TeamServiceError(f"团队仍有活动成员：{'、'.join(active)}。请先停止成员。")
        retained: list[str] = []
        if self._worktrees is not None:
            for member in team.members:
                if member.worktree_slug is None:
                    continue
                branch = f"yucode/worktree/{member.worktree_slug}"
                # 用户可能已经用 /worktree remove 清理了目录。只有目录与分支都
                # 不存在时才删除孤立元数据；任一资源还在都按原有安全检查处理。
                if self._merger.resource_is_absent(member.workspace_root, branch):
                    continue
                try:
                    safe, reason = self._merger.safe_to_cleanup(member.workspace_root, branch)
                except Exception as error:
                    safe, reason = False, f"无法检查成员资源：{error}"
                if not safe:
                    retained.append(f"{member.name}：{reason}")
                    continue
                if not member.workspace_root.is_dir():
                    # 目录已被外部清理，只剩已合并的分支，没有可移除的 Worktree 资源。
                    continue
                result = self._worktrees.remove(member.worktree_slug, force=True)
                if not result.removed:
                    retained.append(f"{member.name}：{result.reason}")
        if retained:
            raise TeamServiceError("存在不能安全清理的成员资源：" + "；".join(retained))
        self.repository.delete(name)

    async def delete_team(self, name: str) -> None:
        """停止尚未结束的成员，然后执行保守的资源检查与团队删除。"""
        team = self.get(name)
        for member in team.members:
            if member.state not in {MemberState.STOPPED, MemberState.FAILED}:
                await self.stop(name, member.name)
        await asyncio.to_thread(self.delete, name)

    def merge(self, name: str) -> TeamMergeResult:
        team = self.get(name)
        tasks = self.tasks.list(team)
        assignment = {item.task_id: item.assignee for item in tasks}
        before: dict[str, set[str]] = {item.name: set() for item in team.members}
        for task in tasks:
            if task.assignee not in before:
                continue
            for dependency in task.dependencies:
                owner = assignment.get(dependency)
                if owner in before and owner != task.assignee:
                    before[task.assignee].add(owner)
        ordered: list[TeamMember] = []
        remaining = list(team.members)
        while remaining:
            ready = next((item for item in remaining if before[item.name].issubset({done.name for done in ordered})), remaining[0])
            ordered.append(ready); remaining.remove(ready)
        result = self._merger.merge(replace(team, members=tuple(ordered)))
        for item in result.members:
            if item.status in {"已合并", "已包含"}:
                current = next(member for member in self.get(name).members if member.name == item.member)
                self.update_member(self.get(name), replace(current, merged=True))
        self._notifications.append(f"团队 {name} 收敛结果：\n{result.summary}")
        return result
