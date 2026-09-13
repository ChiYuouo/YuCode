"""统一定义式与 Fork 式子 Agent 的调度服务。"""

from __future__ import annotations

from yucode.agent import Agent
from yucode.cancellation import Cancellation
from yucode.permissions import ApprovalCallback
from yucode.subagents.factory import SubagentFactory
from yucode.subagents.loader import AgentDefinitionLoader
from dataclasses import replace
from uuid import uuid4
from yucode.subagents.models import AgentIsolation, SubagentKind, SubagentRequest, TaskNotification
from yucode.worktrees.manager import WorktreeManager
from yucode.subagents.runner import RunToCompletion
from yucode.subagents.tasks import TaskManager
from yucode.tools.base import ToolResult


class _ProgressSink:
    """把子 Agent 执行事件转发给 TaskManager；任务标识在 start 后才可知。"""

    def __init__(self, tasks: TaskManager) -> None:
        self._tasks = tasks; self._task_id: str | None = None

    def bind(self, task_id: str) -> None:
        self._task_id = task_id

    async def emit(self, text: str, result=None) -> None:
        if self._task_id is not None:
            await self._tasks.report_progress(self._task_id, text, result)


class SubagentService:
    def __init__(self, loader: AgentDefinitionLoader, factory: SubagentFactory, tasks: TaskManager, runner: RunToCompletion | None = None, worktrees: WorktreeManager | None = None) -> None:
        self._loader = loader; self._factory = factory; self._tasks = tasks; self._runner = runner or RunToCompletion(); self._parent: Agent | None = None
        self._worktrees = worktrees

    def bind_parent(self, parent: Agent) -> None:
        self._parent = parent

    async def delegate(self, request: SubagentRequest, call_id: str, approve: ApprovalCallback | None = None) -> ToolResult:
        parent = self._parent
        if parent is None:
            return ToolResult(call_id, "Agent", False, "子 Agent 服务尚未绑定主 Agent。", error_code="subagent_unavailable")
        definition = None
        if request.kind is SubagentKind.DEFINITION:
            definition = self._loader.discover().get(request.definition_name or "")
            if definition is None:
                return ToolResult(call_id, "Agent", False, f"找不到 Agent 定义：{request.definition_name}。", error_code="unknown_subagent")
        background = request.run_in_background or request.kind is SubagentKind.FORK
        worktree = None
        effective_isolation = request.isolation if request.isolation is not None else definition.isolation if definition is not None else AgentIsolation.NONE
        if definition is not None and effective_isolation is AgentIsolation.WORKTREE:
            if self._worktrees is None:
                return ToolResult(call_id, "Agent", False, "当前没有可用的 Worktree 服务。", error_code="worktree_unavailable")
            try:
                worktree = self._worktrees.create(f"agent/{definition.name}-{uuid4().hex[:10]}", temporary=True)
            except Exception as error:
                return ToolResult(call_id, "Agent", False, f"无法创建隔离 Worktree：{error}", error_code="worktree_create_failed")
        notice = None if worktree is None else f"<worktree_notice>\n你正在隔离 Worktree 中执行。目录：{worktree.path}\n分支：{worktree.branch}\n所有项目文件与命令操作必须使用该目录。\n</worktree_notice>"
        child = self._factory.create_fork(parent, background=True) if request.kind is SubagentKind.FORK else self._factory.create_definition(parent, definition, background=background, workspace_root=worktree.path if worktree else None, notice=notice)
        sink = _ProgressSink(self._tasks)

        async def work(cancellation: Cancellation):
            outcome = await self._runner.run(child, request.prompt, cancellation, approve, on_event=sink.emit)
            if worktree is not None:
                try:
                    cleanup = self._worktrees.remove(worktree.slug, automatic=True)
                    if not cleanup.removed:
                        outcome = replace(outcome, summary=f"{outcome.summary}\n\nWorktree 已保留：{worktree.path}\n分支：{worktree.branch}\n原因：{cleanup.reason}")
                except Exception as error:
                    outcome = replace(outcome, summary=f"{outcome.summary}\n\nWorktree 已保留：{worktree.path}\n分支：{worktree.branch}\n收尾失败：{error}")
            return outcome

        task = self._tasks.start(request.kind, definition.name if definition else None, work, background=background)
        sink.bind(task.id)
        if background:
            return ToolResult(call_id, "Agent", True, f"子 Agent 已在后台启动，任务标识：{task.id}。", content=task.id)
        finished = await self._tasks.wait_foreground(task.id)
        if finished.status.name in {"PENDING", "RUNNING", "BACKGROUND"}:
            return ToolResult(call_id, "Agent", True, f"子 Agent 已转入后台，任务标识：{task.id}。", content=task.id)
        return ToolResult(call_id, "Agent", finished.status.name == "COMPLETED", finished.summary or "任务已结束。", error_code=finished.error)

    def drain_notifications(self) -> tuple[TaskNotification, ...]: return self._tasks.drain_notifications()
    def set_notification_listener(self, listener) -> None: self._tasks.set_notification_listener(listener)
    def promote_foreground(self): return self._tasks.promote()
    @property
    def tasks(self) -> TaskManager: return self._tasks
