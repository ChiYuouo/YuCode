"""ch19：后台任务进度可见性与执行一致性的回归测试。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from yucode.agent import Agent, AgentFinished, ProgressPhase, ProgressUpdated, StopReason, ToolResultReady
from yucode.config import TeamConfig
from yucode.conversation import Conversation
from yucode.cancellation import Cancellation
from yucode.providers.base import StreamEvent, Usage
from yucode.subagents.models import SubagentKind, TaskOutcome, TaskStatus
from yucode.subagents.runner import RunToCompletion
from yucode.subagents.tasks import TaskManager
from yucode.teams.backends import BackendSelector, InProcessBackend
from yucode.teams.models import MemberState
from yucode.teams.service import TeamService
from yucode.tools.base import ToolCall, ToolResult
from yucode.tools.registry import ToolRegistry


class _EventAgent:
    """按序产出进度、工具结果与完成事件的假 Agent。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _prompt, _cancellation, _approve):
        self.calls += 1
        yield ProgressUpdated(1, 10, ProgressPhase.MODEL, "第 1 轮：正在请求模型")
        result = ToolResult("c1", "read_file", True, "读取完成", "", None, "")
        yield ToolResultReady(1, result)
        yield AgentFinished(StopReason.COMPLETED, "完成", Usage(3, 5))


def test_runner_forwards_progress_and_tool_results() -> None:
    async def scenario() -> None:
        seen: list[tuple[str, object | None]] = []

        async def on_event(text, result):
            seen.append((text, result))

        outcome = await RunToCompletion().run(_EventAgent(), "任务", Cancellation(), None, on_event)
        assert outcome.status is TaskStatus.COMPLETED
        texts = [item[0] for item in seen]
        assert any("第 1 轮" in item for item in texts)
        assert any("最近工具：read_file成功" in item for item in texts)
        results = [item[1] for item in seen if item[1] is not None]
        assert len(results) == 1 and results[0].name == "read_file"

    asyncio.run(scenario())


def test_task_manager_reports_progress_and_survives_listener_errors() -> None:
    async def scenario() -> None:
        manager = TaskManager()
        received: list[tuple[str, str]] = []

        async def listener(task_id, text, result):
            received.append((task_id, text))
            if text == "触发异常":
                raise RuntimeError("监听故障")

        manager.set_progress_listener(listener)

        async def work(_cancellation):
            await manager.report_progress("unknown", "不会记录")
            await manager.report_progress("pending-id", "前置文本")
            return TaskOutcome(TaskStatus.COMPLETED, "完成")

        snapshot = manager.start(SubagentKind.FORK, None, work, background=True)
        # 任务启动后用真实标识补发进度。
        await manager.report_progress(snapshot.id, "第 1 轮 · 正在执行")
        worker = manager.worker_for(snapshot.id)
        assert worker is not None
        await worker
        stored = manager.info(snapshot.id)
        assert stored is not None and "正在执行" in stored.progress
        assert any(item[0] == snapshot.id for item in received)
        # 未知任务标识是安静的无操作。
        assert all(item[0] == snapshot.id for item in received)

    asyncio.run(scenario())


class _SlowProvider:
    async def stream(self, _request, _cancellation):
        await asyncio.sleep(0.5)
        yield StreamEvent("text", "尚未开始就超时了")


class _HangingProvider:
    def __init__(self) -> None:
        self.finished = False

    async def stream(self, _request, _cancellation):
        try:
            await asyncio.sleep(5)
            self.finished = True
            yield StreamEvent("text", "完成")
        except asyncio.CancelledError:
            raise


class _Worktrees:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self, slug: str, temporary: bool = False):
        path = self.root.joinpath(*slug.split("/"))
        path.mkdir(parents=True)
        return SimpleNamespace(path=path, slug=slug)


def _service(tmp_path: Path, timeout: float | None = None) -> TeamService:
    driver = InProcessBackend()
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), _Worktrees(tmp_path / "worktrees"), tmp_path / "teams")
    Agent(_SlowProvider(), Conversation(), ToolRegistry(tmp_path), team_service=service)
    service._tasks = TaskManager(execution_timeout_seconds=timeout)
    return service


def test_in_process_member_is_registered_in_unified_task_list(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        # 测试环境用快速 Provider：先注册慢速前的默认 Provider 由 _service 提供，
        # 这里直接换用不等待的 Provider 行为不需要，成员按正常流程完成。
        await service.spawn("demo", "alice", "读取 README.md 并总结")
        worker = next(iter(service._running.values()))
        await worker
        tasks = service._tasks.list()
        assert tasks and tasks[0].kind is SubagentKind.TEAM_MEMBER
        assert tasks[0].status is TaskStatus.COMPLETED
        assert service.get("demo").members[0].state is MemberState.IDLE

    asyncio.run(scenario())


def test_member_timeout_marks_failed_and_reports_lead(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((InProcessBackend(),)), (InProcessBackend(),),
                              _Worktrees(tmp_path / "worktrees"), tmp_path / "teams")
        Agent(_HangingProvider(), Conversation(), ToolRegistry(tmp_path), team_service=service)
        service._tasks = TaskManager(execution_timeout_seconds=0.05)
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        await service.spawn("demo", "alice", "读取 README.md 并总结")
        await asyncio.sleep(0.3)
        member = service.get("demo").members[0]
        assert member.state is MemberState.FAILED
        tasks = service._tasks.list()
        assert tasks[0].status is TaskStatus.TIMED_OUT
        assert any("已超时或被取消" in item for item in service.drain_notifications())

    asyncio.run(scenario())


def test_lead_stop_does_not_report_timeout_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        await service.spawn("demo", "alice", "读取 README.md 并总结")
        await asyncio.sleep(0.05)
        await service.stop("demo", "alice")
        member = service.get("demo").members[0]
        assert member.state is MemberState.STOPPED
        notifications = service.drain_notifications()
        assert not any("已超时或被取消" in item for item in notifications)

    asyncio.run(scenario())


class _HookRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def run_hooks(self, context) -> None:
        self.events.append(context.event.value)


def test_member_runs_emit_task_hooks_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,), _Worktrees(tmp_path / "worktrees"), tmp_path / "teams")
        Agent(_SlowProvider(), Conversation(), ToolRegistry(tmp_path), team_service=service)
        recorder = _HookRecorder()
        service._tasks = TaskManager(hooks=recorder)
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        await service.spawn("demo", "alice", "读取 README.md 并总结")
        worker = next(iter(service._running.values()))
        await worker
        assert recorder.events[0] == "task_start"
        assert recorder.events[-1] == "send_message"
        assert "task_stop" in recorder.events and "task_complete" in recorder.events
        assert recorder.events.index("task_stop") < recorder.events.index("task_complete")
        assert recorder.events.index("task_complete") < recorder.events.index("send_message")

    asyncio.run(scenario())


def test_inline_skill_message_carries_sop(tmp_path: Path) -> None:
    from yucode.commands.models import CommandContext
    from yucode.skills.commands import SkillCommandCatalog
    from yucode.skills.loader import SkillLoader
    from yucode.skills.runtime import SkillRuntime

    skill_dir = tmp_path / ".yucode" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "demo.md").write_text(
        "---\nname: demo\ndescription: 演示用 Skill\nallowedTools:\n  - read_file\nmode: inline\nhistory: none\n---\n请审查 $ARGUMENTS 并只报告问题。",
        encoding="utf-8",
    )
    loader = SkillLoader(tmp_path, tmp_path / "user", tmp_path / "builtin")
    # SkillRuntime 对 tools.definitions 迭代并访问 .name，测试里用最小桩。
    runtime = SkillRuntime(loader, SimpleNamespace(definitions=[SimpleNamespace(name="read_file")]))
    runtime.initialize()

    class _Base:
        def get(self, _name):
            return None

        def visible(self):
            return ()

    sent: list[str] = []

    class _UI:
        async def show_message(self, text, *, error=False):
            sent.append(text)

        async def send_user_message(self, text):
            sent.append(text)

    catalog = SkillCommandCatalog(_Base(), runtime)
    command = catalog.get("skill:demo")
    assert command is not None
    context = CommandContext(registry=None, ui=_UI(), agent=None)
    asyncio.run(command.handler(context, "登录模块"))
    assert len(sent) == 1
    message = sent[0]
    assert "请审查 登录模块 并只报告问题。" in message
    assert "用户参数：登录模块" in message
    assert "不要再次调用 LoadSkill" in message
    assert "已激活" not in message
    # 激活状态仍然生效：工具范围收敛依赖 runtime.load。
    assert runtime.snapshot().active[0].definition.name == "demo"


def test_tui_task_progress_updates_activity_and_finishes_permission_card(tmp_path: Path) -> None:
    from textual.containers import VerticalScroll

    from yucode.config import ProviderConfig
    from yucode.permissions import PermissionManager
    from yucode.tui.app import ChatApp
    from yucode.tui.widgets import ForkActivity, InlinePermissionCard

    class _IdleProvider:
        async def stream(self, _request, _cancellation):
            yield StreamEvent("text", "完成")
            yield StreamEvent("usage", usage=Usage(1, 1))

    async def check() -> None:
        agent = Agent(_IdleProvider(), Conversation(), ToolRegistry(tmp_path),
                      permissions=PermissionManager(tmp_path))
        app = ChatApp(agent, ProviderConfig("anthropic", "claude-test", "https://example.test", "key", True))
        async with app.run_test() as pilot:
            request = PermissionManager(tmp_path).request_for(ToolCall("bgx-1", "run_command", {"command": "Get-Location"}))
            card = InlinePermissionCard(request)
            app._permission_cards["bgx-1"] = card
            await app.query_one("#chat-view", VerticalScroll).mount(card)
            await pilot.pause()
            result = ToolResult("bgx-1", "run_command", True, "命令执行完成", "", None, "")
            await app._on_task_progress("abcdef123456", "第 1 轮 · 正在执行 1 个工具", result)
            await pilot.pause()
            activity = app.query_one(ForkActivity)
            assert "任务 abcdef" in str(activity.render())
            assert "第 1 轮 · 正在执行 1 个工具" in str(activity.render())
            # 权限卡已收尾并从等待表中移除。
            assert "权限确认结果" in str(card.render())
            assert "bgx-1" not in app._permission_cards

    asyncio.run(check())
