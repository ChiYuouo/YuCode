"""暴露给 Team Lead 与成员的协作工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import asyncio

from yucode.cancellation import Cancellation
from yucode.teams.models import MessageKind, TeamBackend, TeamTaskState
from yucode.teams.service import TeamService, TeamServiceError
from yucode.tools.base import ToolContext, ToolDefinition, ToolResult, ToolSafety


class _TeamTool:
    safety = ToolSafety.SIDE_EFFECT
    def __init__(self, service: TeamService, team_name: str | None = None, sender: str = "lead") -> None:
        self._service = service; self._team_name = team_name; self._sender = sender
    def _team(self, arguments: Mapping[str, Any]) -> str:
        value = self._team_name or arguments.get("team")
        if not isinstance(value, str) or not value.strip():
            raise TeamServiceError("team 必须是非空字符串。")
        return value.strip()
    @staticmethod
    def _result(call_id: str, name: str, action):
        try:
            return action()
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, name, False, str(error), error_code="team_error")


class TeamCreateTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TeamCreate", "创建长期 Agent Team 并登记成员花名册。读取、搜索、解释、审查和总结任务应保持 writable=false；只有明确修改文件时才设为 true。", {"type": "object", "properties": {
            "name": {"type": "string"}, "members": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "role": {"type": "string"},
                "writable": {"type": "boolean", "default": False, "description": "默认 false。只读、搜索、审查、解释和总结任务不得设为 true；仅修改文件时启用。"},
                "requires_approval": {"type": "boolean"}, "backend": {"type": "string", "enum": [item.value for item in TeamBackend]},
            }, "required": ["name", "role"], "additionalProperties": False}}}, "required": ["name", "members"], "additionalProperties": False})
    async def execute(self, arguments, context: ToolContext, call_id: str, cancellation: Cancellation):
        try:
            name = arguments.get("name"); members = arguments.get("members")
            if not isinstance(name, str) or not isinstance(members, list):
                raise TeamServiceError("name 必须是字符串，members 必须是列表。")
            # Worktree 创建会调用 Git，不能阻塞终端事件循环和工具状态刷新。
            team = await asyncio.to_thread(self._service.create, name, self._sender, tuple(members))
            roster = "\n".join(
                f"{item.name}：{'可写' if item.writable else '只读'}，{item.backend.value}，目录 {item.workspace_root}，"
                f"Worktree {item.worktree_slug or '未创建'}，审批 {'是' if item.requires_approval else '否'}"
                for item in team.members
            )
            diagnostics = "\n".join(
                f"{item.backend.value}：{'可用' if item.available else '不可用'}（{item.reason}）"
                for item in self._service.backend_diagnostics()
            )
            return ToolResult(call_id, "TeamCreate", True, f"已创建团队 {team.name}，成员数：{len(team.members)}。",
                              content=f"团队目录：{team.root}\n{roster}\n后端检测：\n{diagnostics}")
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "TeamCreate", False, str(error), error_code="team_error")


class TeamDeleteTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TeamDelete", "删除已停止成员的团队元数据。", {"type": "object", "properties": {"team": {"type": "string"}}, "required": ["team"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        try:
            await self._service.delete_team(self._team(arguments))
            return ToolResult(call_id, "TeamDelete", True, "团队已删除。")
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "TeamDelete", False, str(error), error_code="team_error")


class TeamSpawnTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TeamSpawn", "启动已登记的 Team 成员执行一项明确任务。", {"type": "object", "properties": {"team": {"type": "string"}, "member": {"type": "string"}, "prompt": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["team", "member", "prompt"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        try:
            team = self._team(arguments); member = arguments.get("member"); prompt = arguments.get("prompt")
            if not isinstance(member, str) or not isinstance(prompt, str):
                raise TeamServiceError("member 和 prompt 必须是字符串。")
            task_id = arguments.get("task_id")
            if task_id is not None and not isinstance(task_id, str): raise TeamServiceError("task_id 必须是字符串。")
            await self._service.spawn(team, member, prompt, task_id=task_id)
            return ToolResult(call_id, "TeamSpawn", True, f"成员 {member} 已在后台启动。")
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "TeamSpawn", False, str(error), error_code="team_error")


class TeamStopTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TeamStop", "停止一个 Team 成员。", {"type": "object", "properties": {"team": {"type": "string"}, "member": {"type": "string"}}, "required": ["team", "member"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        try:
            member = arguments.get("member")
            if not isinstance(member, str): raise TeamServiceError("member 必须是字符串。")
            await self._service.stop(self._team(arguments), member)
            return ToolResult(call_id, "TeamStop", True, f"成员 {member} 已停止。")
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "TeamStop", False, str(error), error_code="team_error")


class TeamMergeTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TeamMerge", "将已完成成员的 Worktree 分支收敛到 Lead 分支。", {"type": "object", "properties": {"team": {"type": "string"}}, "required": ["team"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        try:
            result = await asyncio.to_thread(self._service.merge, self._team(arguments))
            return ToolResult(call_id, "TeamMerge", result.success, "团队收敛完成。" if result.success else "团队收敛遇到冲突，已回滚当前合并。", content=result.summary, error_code=None if result.success else "merge_conflict")
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "TeamMerge", False, str(error), error_code="team_error")


class TaskCreateTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TaskCreate", "创建团队共享任务；blocked_by 表示必须先完成的任务。", {"type": "object", "properties": {"team": {"type": "string"}, "title": {"type": "string"}, "detail": {"type": "string"}, "assignee": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "detail"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        def action():
            deps = arguments.get("blocked_by", [])
            if not isinstance(deps, list) or not all(isinstance(item, str) for item in deps): raise TeamServiceError("blocked_by 必须是字符串列表。")
            task = self._service.tasks.create(self._service.get(self._team(arguments)), arguments.get("title"), arguments.get("detail"), assignee=arguments.get("assignee"), dependencies=tuple(deps))
            return ToolResult(call_id, "TaskCreate", True, f"已创建任务：{task.title}。", content=task.task_id)
        return self._result(call_id, "TaskCreate", action)


class TaskListTool(_TeamTool):
    safety = ToolSafety.READ_ONLY
    @property
    def definition(self):
        return ToolDefinition("TaskList", "列出团队共享任务及状态。", {"type": "object", "properties": {"team": {"type": "string"}}, "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        def action():
            tasks = self._service.tasks.list(self._service.get(self._team(arguments)))
            content = "\n".join(f"{item.task_id} {item.state.value} is_ready={str(item.state is TeamTaskState.READY).lower()} blocked_by={list(item.dependencies)} blocks={list(self._service.tasks.blocks(self._service.get(self._team(arguments)), item.task_id))} {item.title}" for item in tasks) or "暂无任务。"
            return ToolResult(call_id, "TaskList", True, "已列出团队任务。", content=content)
        return self._result(call_id, "TaskList", action)


class TaskGetTool(_TeamTool):
    safety = ToolSafety.READ_ONLY
    @property
    def definition(self):
        return ToolDefinition("TaskGet", "查看一个团队共享任务。", {"type": "object", "properties": {"team": {"type": "string"}, "task_id": {"type": "string"}}, "required": ["task_id"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        def action():
            task_id = arguments.get("task_id")
            if not isinstance(task_id, str): raise TeamServiceError("task_id 必须是字符串。")
            task = self._service.tasks.get(self._service.get(self._team(arguments)), task_id)
            blocks = self._service.tasks.blocks(self._service.get(self._team(arguments)), task.task_id)
            return ToolResult(call_id, "TaskGet", True, f"任务：{task.title}", content=f"状态：{task.state.value}\nis_ready：{task.state is TeamTaskState.READY}\nblocked_by：{list(task.dependencies)}\nblocks：{list(blocks)}\n负责人：{task.assignee or '未分配'}\n{task.detail}")
        return self._result(call_id, "TaskGet", action)


class TaskUpdateTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("TaskUpdate", "更新团队任务状态、负责人或 blocked_by。", {"type": "object", "properties": {"team": {"type": "string"}, "task_id": {"type": "string"}, "state": {"type": "string", "enum": [item.value for item in TeamTaskState]}, "assignee": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}}, "required": ["task_id"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        def action():
            task_id = arguments.get("task_id"); deps = arguments.get("blocked_by")
            if not isinstance(task_id, str): raise TeamServiceError("task_id 必须是字符串。")
            if deps is not None and (not isinstance(deps, list) or not all(isinstance(item, str) for item in deps)): raise TeamServiceError("blocked_by 必须是字符串列表。")
            state_value = arguments.get("state")
            task = self._service.tasks.update(self._service.get(self._team(arguments)), task_id,
                state=TeamTaskState(state_value) if state_value is not None else None,
                assignee=arguments.get("assignee"), dependencies=tuple(deps) if deps is not None else None)
            return ToolResult(call_id, "TaskUpdate", True, f"任务状态已更新为 {task.state.value}。")
        return self._result(call_id, "TaskUpdate", action)


class SendMessageTool(_TeamTool):
    @property
    def definition(self):
        return ToolDefinition("SendMessage", "向团队成员发送单播、广播或计划审批消息。", {"type": "object", "properties": {"team": {"type": "string"}, "recipients": {"type": "array", "items": {"type": "string"}}, "body": {"type": "string"}, "kind": {"type": "string", "enum": [item.value for item in MessageKind]}, "protocol": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["recipients", "body"], "additionalProperties": False})
    async def execute(self, arguments, context, call_id, cancellation):
        try:
            recipients = arguments.get("recipients"); body = arguments.get("body")
            if not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients) or not isinstance(body, str):
                raise TeamServiceError("recipients 必须是字符串列表，body 必须是字符串。")
            kind = MessageKind(arguments.get("kind", "text"))
            protocol = arguments.get("protocol", {})
            if not isinstance(protocol, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in protocol.items()):
                raise TeamServiceError("protocol 必须是字符串键值对象。")
            ids = await self._service.send(self._team(arguments), self._sender, tuple(recipients), body, kind, protocol=protocol)
            return ToolResult(call_id, "SendMessage", True, f"消息已写入 {len(ids)} 个邮箱。", content="\n".join(ids))
        except (TeamServiceError, ValueError) as error:
            return ToolResult(call_id, "SendMessage", False, str(error), error_code="team_error")
