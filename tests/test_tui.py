import asyncio
from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from mewcode.agent import Agent, ToolCallStarted, ToolResultReady
from mewcode.config import ProviderConfig
from mewcode.conversation import Conversation
from mewcode.providers.base import ProviderError, StreamCancelled, StreamEvent, Usage
from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.registry import ToolRegistry
from mewcode.tui.app import ChatApp
from mewcode.tui.widgets import (
    AssistantMessage, ChatStatus, Composer, ErrorMessage, GenerationIndicator,
    ModeMenu, PendingToolActivity, ThinkingBox, ToolActivity, WelcomePanel,
)


class FakeProvider:
    def __init__(self) -> None:
        self.requests = []

    async def stream(self, messages, cancellation, tools=(), instructions=None):
        self.requests.append((tuple(messages), tuple(tool.name for tool in tools), instructions))
        yield StreamEvent("thinking", "分析过程")
        yield StreamEvent("text", "# 标题\n\n```python\nprint('hi')\n```")
        yield StreamEvent("usage", usage=Usage(input_tokens=3, output_tokens=5, thinking_tokens=2))


def app_for_test(provider=None, root: Path | None = None) -> ChatApp:
    provider = provider or FakeProvider()
    agent = Agent(provider, Conversation(), ToolRegistry(root or Path.cwd()))
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
            assert "MewCode" in str(welcome.render())
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
        async def stream(self, _messages, cancellation, tools=(), instructions=None):
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
        async def stream(self, _messages, cancellation, tools=(), instructions=None):
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
        async def stream(self, _messages, cancellation, tools=(), instructions=None):
            raise ProviderError("服务暂不可用")
            yield

    class BlockingProvider:
        async def stream(self, _messages, cancellation, tools=(), instructions=None):
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
            assert set(provider.requests[0][1]) == {"read_file", "find_files", "search_code"}

            prompt.text = "/d"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "模式:Do" in str(app.query_one(ChatStatus).render())

            for text in ("执行", "普通消息"):
                prompt.text = text
                await pilot.press("enter")
                await pilot.pause(0.2)
            assert len(provider.requests[1][1]) == 6
            assert len(provider.requests[2][0]) == 5
            assert "› 分析" in str(app.query(".user-message").first().render())

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
            assert "模式:Do" in str(app.query_one(ChatStatus).render())
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

        async def stream(self, _messages, cancellation, tools=(), instructions=None):
            for event in self.responses.pop(0):
                yield event

    async def check() -> None:
        app = app_for_test(ToolProvider(), tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "创建文件"
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

        async def stream(self, _messages, cancellation, tools=(), instructions=None):
            for event in self.responses.pop(0):
                yield event

    async def check() -> None:
        app = app_for_test(CommandProvider(), tmp_path)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "运行目录命令"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.screen.query_one("#reject-command")
            await pilot.click("#reject-command")
            await pilot.pause(0.3)
            assert app.query_one(ToolActivity)
            assert prompt.disabled is False

    asyncio.run(check())
