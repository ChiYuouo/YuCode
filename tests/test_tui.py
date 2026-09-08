import asyncio
from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from yucode.agent import Agent, ToolCallStarted, ToolResultReady
from yucode.config import ProviderConfig
from yucode.conversation import Conversation
from yucode.permissions import ApprovalChoice, PermissionManager, PermissionMode
from yucode.providers.base import CacheUsage, ProviderError, StreamCancelled, StreamEvent, Usage
from yucode.tools.base import ToolCall, ToolResult
from yucode.tools.registry import ToolRegistry
from yucode.tui.app import ChatApp
from yucode.tui.widgets import (
    AssistantMessage, ChatStatus, Composer, ErrorMessage, GenerationIndicator,
    InlinePermissionCard, ModeMenu, PendingToolActivity, ThinkingBox, ToolActivity, WelcomePanel,
)


class FakeProvider:
    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request, cancellation):
        self.requests.append(request)
        yield StreamEvent("thinking", "分析过程")
        yield StreamEvent("text", "# 标题\n\n```python\nprint('hi')\n```")
        yield StreamEvent("usage", usage=Usage(input_tokens=3, output_tokens=5, thinking_tokens=2))


def app_for_test(provider=None, root: Path | None = None, permission_mode=PermissionMode.DEFAULT) -> ChatApp:
    provider = provider or FakeProvider()
    registry = ToolRegistry(root or Path.cwd())
    agent = Agent(provider, Conversation(), registry, permissions=PermissionManager(registry.context.root, permission_mode))
    return ChatApp(
        agent,
        ProviderConfig("anthropic", "claude-test", "https://example.test", "key", True),
    )


def test_tui_layout_widget() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            assert app.query_one(ChatStatus)
            assert app.query_one("#chat-view", VerticalScroll)
            assert app.query_one("#prompt", Composer)
            welcome = app.query_one(WelcomePanel)
            rendered = str(welcome.render())
            assert "██████╗" in rendered
            assert "( o.o )" not in rendered
            assert "claude-test" in str(welcome.render())
            await pilot.pause()

    asyncio.run(check())


def test_tui_enter_sends_streams_and_updates_tokens() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "测试消息"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(app.query(WelcomePanel)) == 0
            assert "› 测试消息" in str(app.query_one(".user-message").render())
            assert "M:2" in str(app.query_one(ChatStatus).render())
            assert "I:3 O:5" in str(app.query_one(ChatStatus).render())
            assert "缓存数据不可用" in str(app.query_one(ChatStatus).render())
            assert composer.disabled is False

    asyncio.run(check())


def test_tui_shift_enter_and_thinking_layout() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "第一行"
            composer.cursor_location = composer.document.end
            await pilot.press("shift+enter")
            composer.insert("第二行")
            await pilot.press("enter")
            await pilot.pause(0.2)
            thinking = app.query_one(ThinkingBox)
            assert thinking.display is True and thinking.collapsed is True
            assistant = app.query_one(AssistantMessage)
            assert thinking.region.y < assistant.query_one("#assistant-content", Markdown).region.y
            await pilot.click(thinking._title, offset=(2, 0))
            await pilot.pause(0.1)
            assert thinking.collapsed is False

    asyncio.run(check())


def test_tui_streaming_follows_latest_content() -> None:
    class LongProvider:
        async def stream(self, _request, cancellation):
            yield StreamEvent("text", "第一段内容\n" * 20)
            await asyncio.sleep(0.2)
            yield StreamEvent("text", "第二段内容\n" * 20)

    async def check() -> None:
        app = app_for_test(LongProvider())
        async with app.run_test(size=(80, 12)) as pilot:
            composer = app.query_one(Composer)
            composer.text = "输出长内容"
            await pilot.press("enter")
            await pilot.pause(0.1)
            chat = app.query_one("#chat-view", VerticalScroll)
            assert chat.max_scroll_y > 0
            chat.scroll_home(animate=False)
            await pilot.pause(0.3)
            assert chat.scroll_y == chat.max_scroll_y

    asyncio.run(check())


def test_tui_thinking_update_does_not_scroll_but_text_does() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            chat = app.query_one("#chat-view", VerticalScroll)
            assistant = AssistantMessage()
            await chat.mount(assistant)
            await pilot.pause()
            app._assistant_message = assistant
            scroll_calls = []
            app._scroll_to_latest = lambda target: scroll_calls.append(target)

            await app._append_thinking("分析过程")
            assert scroll_calls == []
            await app._append_text("正式回答")
            assert scroll_calls == [chat]

    asyncio.run(check())


def test_tui_activity_frames_thinking_and_cancel_stop_together() -> None:
    class ThinkingProvider:
        async def stream(self, _request, cancellation):
            yield StreamEvent("thinking", "正在分析")
            await cancellation.wait()
            raise StreamCancelled()
            yield

    async def check() -> None:
        app = app_for_test(ThinkingProvider())
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "分析任务"
            await pilot.press("enter")
            await pilot.pause(0.2)
            thinking = app.query_one(ThinkingBox)
            first_title = str(thinking.title)
            app._advance_activity_frame()
            app._advance_activity_frame()
            assert thinking.display is True and thinking.collapsed is True
            assert str(thinking.title) != first_title
            assert app._activity_timer is not None

            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert prompt.disabled is False
            assert app._activity_timer is None
            assert thinking.title == "已思考"

    asyncio.run(check())


def test_tui_pending_tool_is_replaced_by_static_result() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            app._generating = True
            app._start_activity_clock()
            call = ToolCall("tool-1", "read_file", {"path": "README.md"})
            await app._show_tool_pending(ToolCallStarted(1, call))
            pending = app.query_one(PendingToolActivity)
            before = str(pending.render())
            app._advance_activity_frame()
            app._advance_activity_frame()
            assert "正在执行工具 read_file" in str(pending.render())
            assert str(pending.render()) != before

            result = ToolResult("tool-1", "read_file", True, "已读取", "内容", "README.md")
            await app._show_tool_result(ToolResultReady(1, result))
            assert "tool-pending" not in pending.classes
            assert "已读取" in str(pending.render())
            app._generating = False
            app._stop_activity_clock()
            await pilot.pause()

    asyncio.run(check())


def test_tui_error_and_cancel_restore_input() -> None:
    class ErrorProvider:
        async def stream(self, _request, cancellation):
            raise ProviderError("服务暂不可用")
            yield

    class BlockingProvider:
        async def stream(self, _request, cancellation):
            await cancellation.wait()
            raise StreamCancelled()
            yield

    async def check_error() -> None:
        app = app_for_test(ErrorProvider())
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "问题"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert prompt.disabled is False
            assert "服务暂不可用" in str(app.query_one(ErrorMessage).render())

    async def check_cancel() -> None:
        app = app_for_test(BlockingProvider())
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "问题"
            await pilot.press("enter")
            assert prompt.disabled is True
            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert prompt.disabled is False

    asyncio.run(check_error())
    asyncio.run(check_cancel())


def test_tui_mode_menu_switches_session_mode_and_shares_history(tmp_path: Path) -> None:
    provider = FakeProvider()

    async def check() -> None:
        app = app_for_test(provider, tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "/"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert prompt.text == ""
            assert "模式:Plan" in str(app.query_one(ChatStatus).render())

            prompt.text = "分析"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert {tool.name for tool in provider.requests[0].tools} == {"read_file", "find_files", "search_code"}

            prompt.text = "/d"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "模式:Default" in str(app.query_one(ChatStatus).render())

            for text in ("执行", "普通消息"):
                prompt.text = text
                await pilot.press("enter")
                await pilot.pause(0.2)
            assert len(provider.requests[1].tools) == 6
            assert len(provider.requests[2].history) == 5
            assert "› 分析" in str(app.query(".user-message").first().render())

    asyncio.run(check())


def test_tui_shift_tab_cycles_permission_modes_and_updates_status(tmp_path: Path) -> None:
    async def check() -> None:
        app = app_for_test(root=tmp_path)
        async with app.run_test() as pilot:
            status = app.query_one(ChatStatus)
            assert "模式:Default" in str(status.render())
            for expected in ("AcceptEdits", "Plan", "BypassPermissions", "Default"):
                await pilot.press("shift+tab")
                await pilot.pause(0.05)
                assert f"模式:{expected}" in str(status.render())

    asyncio.run(check())


def test_tui_do_restores_the_previous_do_permission_mode(tmp_path: Path) -> None:
    async def check() -> None:
        app = app_for_test(root=tmp_path)
        async with app.run_test() as pilot:
            app._set_permission_mode(PermissionMode.ACCEPT_EDITS)
            app._activate_mode("plan")
            assert app._agent.permissions.mode is PermissionMode.PLAN
            app._activate_mode("do")
            assert app._agent.permissions.mode is PermissionMode.ACCEPT_EDITS

            app._set_permission_mode(PermissionMode.BYPASS_PERMISSIONS)
            app._activate_mode("plan")
            app._activate_mode("do")
            assert app._agent.permissions.mode is PermissionMode.BYPASS_PERMISSIONS
            await pilot.pause()

    asyncio.run(check())


def test_tui_mode_menu_filters_and_supports_escape_and_mouse(tmp_path: Path) -> None:
    async def check() -> None:
        app = app_for_test(root=tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            menu = app.query_one(ModeMenu)

            prompt.text = "/p"
            await pilot.pause(0.1)
            assert menu.display is True and menu.option_count == 1
            await pilot.press("escape")
            assert prompt.text == "" and menu.display is False

            prompt.text = "/"
            await pilot.pause(0.1)
            assert menu.option_count == 2
            await pilot.click(menu, offset=(2, 1))
            await pilot.pause(0.1)
            assert "模式:Plan" in str(app.query_one(ChatStatus).render())
            assert prompt.text == ""

    asyncio.run(check())


def test_tui_mode_menu_supports_keyboard_navigation(tmp_path: Path) -> None:
    async def check() -> None:
        app = app_for_test(root=tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "/"
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "模式:Default" in str(app.query_one(ChatStatus).render())
            assert prompt.text == ""

    asyncio.run(check())


def test_tui_slash_text_is_not_sent_to_model(tmp_path: Path) -> None:
    provider = FakeProvider()

    async def check() -> None:
        app = app_for_test(provider, tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "/plan 分析"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "模式:Plan" in str(app.query_one(ChatStatus).render())
            assert provider.requests == []

    asyncio.run(check())


def test_tui_shows_tool_summary_and_final_answer(tmp_path: Path) -> None:
    class ToolProvider:
        def __init__(self) -> None:
            self.responses = [
                [StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": "answer.txt", "content": "ok"}))],
                [StreamEvent("text", "已完成写入")],
            ]

        async def stream(self, _request, cancellation):
            for event in self.responses.pop(0):
                yield event

    async def check() -> None:
        app = app_for_test(ToolProvider(), tmp_path, PermissionMode.ACCEPT_EDITS)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "写一个数字 1 到 answer.txt"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "ok"
            assert "write_file" in str(app.query_one(ToolActivity).render())
            assert prompt.disabled is False

    asyncio.run(check())


def test_tui_command_rejection_returns_to_chat(tmp_path: Path) -> None:
    class CommandProvider:
        def __init__(self) -> None:
            self.responses = [
                [StreamEvent("tool_call", tool_call=ToolCall("1", "run_command", {"command": "Get-Location"}))],
                [StreamEvent("text", "命令未执行")],
            ]

        async def stream(self, _request, cancellation):
            for event in self.responses.pop(0):
                yield event

    async def check() -> None:
        app = app_for_test(CommandProvider(), tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "运行目录命令"
            await pilot.press("enter")
            await pilot.pause(0.1)
            card = app.query_one(InlinePermissionCard)
            assert "操作尚未执行" in str(card.render())
            assert "1. 仅本次允许" in str(card.render())
            assert "4. 拒绝" in str(card.render())
            await pilot.press("4")
            await pilot.pause(0.3)
            assert app.query_one(ToolActivity)
            assert "权限确认结果" in str(card.render())
            assert "用户拒绝该调用" in str(card.render())
            assert prompt.disabled is False

    asyncio.run(check())


def test_inline_permission_card_supports_arrow_enter_and_escape() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            request = PermissionManager(Path.cwd()).request_for(ToolCall("call", "write_file", {"path": "a.txt"}))
            card = InlinePermissionCard(request)
            await app.query_one("#chat-view", VerticalScroll).mount(card)
            assert card.handle_key("down") is None
            assert card.handle_key("enter") is ApprovalChoice.SESSION
            assert card.handle_key("escape") is ApprovalChoice.REJECT
            await pilot.pause()

    asyncio.run(check())


def test_tui_shows_cache_usage_when_provider_reports_it() -> None:
    class CacheProvider:
        async def stream(self, _request, _cancellation):
            yield StreamEvent("text", "完成")
            yield StreamEvent("usage", usage=Usage(3, 1, cache=CacheUsage(True, 7, 2)))

    async def check() -> None:
        app = app_for_test(CacheProvider())
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "测试缓存"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert "缓存读 7 / 写 2" in str(app.query_one(ChatStatus).render())

    asyncio.run(check())
