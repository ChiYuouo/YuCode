import asyncio
import time
from collections.abc import Iterator, Sequence

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from mewcode.config import ProviderConfig
from mewcode.providers.base import (
    Cancellation,
    Message,
    ProviderError,
    StreamCancelled,
    StreamEvent,
    Usage,
)
from mewcode.tools.base import ToolCall
from mewcode.tools.registry import ToolRegistry
from mewcode.tui.app import ChatApp
from mewcode.tui.widgets import (
    AssistantMessage,
    ChatStatus,
    Composer,
    ErrorMessage,
    GenerationIndicator,
    ThinkingBox,
    ToolActivity,
    WelcomePanel,
)


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[Message, ...]] = []

    def stream(
        self, messages: Sequence[Message], cancellation: Cancellation | None = None
    ) -> Iterator[StreamEvent]:
        self.requests.append(tuple(messages))
        yield StreamEvent("thinking", "分析过程")
        yield StreamEvent("text", "# 标题\n\n```python\nprint('hi')\n```")
        yield StreamEvent("usage", usage=Usage(input_tokens=3, output_tokens=5, thinking_tokens=2))


def app_for_test(provider: FakeProvider | None = None) -> ChatApp:
    return ChatApp(
        provider or FakeProvider(),
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
            assert "试着这样问" not in str(welcome.render())
            await pilot.pause()

    asyncio.run(check())


def test_tui_enter_sends_and_switches_from_welcome_to_chat() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "测试消息"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(app.query(WelcomePanel)) == 0
            assert composer.text == ""
            assert "› 测试消息" in str(app.query_one(".user-message").render())
            assert "M:2" in str(app.query_one(ChatStatus).render())
            assert "I:3 O:5" in str(app.query_one(ChatStatus).render())

    asyncio.run(check())


def test_tui_shift_enter_inserts_newline_and_thinking_can_expand() -> None:
    async def check() -> None:
        app = app_for_test()
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "第一行"
            composer.cursor_location = composer.document.end
            await pilot.press("shift+enter")
            assert composer.text == "第一行\n"
            composer.insert("第二行")
            await pilot.press("enter")
            await pilot.pause(0.2)
            thinking = app.query_one(ThinkingBox)
            assert thinking.collapsed is True
            assert thinking.display is True
            thinking.collapsed = False
            assert thinking.collapsed is False
            assistant = app.query_one(AssistantMessage)
            markdown = assistant.query_one(Markdown)
            assert markdown.region.y < thinking.region.y

    asyncio.run(check())


def test_tui_streaming_always_follows_latest_content() -> None:
    class LongStreamingProvider:
        def stream(
            self, _: Sequence[Message], cancellation: Cancellation | None = None
        ) -> Iterator[StreamEvent]:
            yield StreamEvent("text", "第一段内容\n" * 20)
            time.sleep(0.2)
            yield StreamEvent("text", "第二段内容\n" * 20)
            yield StreamEvent("usage", usage=Usage(input_tokens=1, output_tokens=40))

    async def check() -> None:
        app = ChatApp(
            LongStreamingProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
        )
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


def test_tui_shows_thinking_indicator_before_first_response() -> None:
    class DelayedProvider:
        def stream(
            self, _: Sequence[Message], cancellation: Cancellation | None = None
        ) -> Iterator[StreamEvent]:
            time.sleep(0.2)
            yield StreamEvent("text", "回复已到达")

    async def check() -> None:
        app = ChatApp(
            DelayedProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
        )
        async with app.run_test() as pilot:
            composer = app.query_one(Composer)
            composer.text = "请回答"
            await pilot.press("enter")
            await pilot.pause(0.05)
            indicator = app.query_one(GenerationIndicator)
            assert indicator.display is True
            assert "正在思考" in str(indicator.render())
            await pilot.pause(0.3)
            assert indicator.display is False

    asyncio.run(check())


def test_tui_error_restores_input() -> None:
    class ErrorProvider:
        def stream(
            self, _: Sequence[Message], cancellation: Cancellation | None = None
        ) -> Iterator[StreamEvent]:
            raise ProviderError("服务暂不可用")
            yield  # pragma: no cover

    async def check() -> None:
        app = ChatApp(
            ErrorProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
        )
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "问题"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert prompt.disabled is False
            assert "服务暂不可用" in str(app.query_one(ErrorMessage).render())

    asyncio.run(check())


def test_tui_cancel_restores_input() -> None:
    class BlockingProvider:
        def stream(
            self, _: Sequence[Message], cancellation: Cancellation | None = None
        ) -> Iterator[StreamEvent]:
            assert cancellation is not None
            while not cancellation.is_cancelled:
                time.sleep(0.01)
            raise StreamCancelled()
            yield  # pragma: no cover

    async def check() -> None:
        app = ChatApp(
            BlockingProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
        )
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "问题"
            await pilot.press("enter")
            assert prompt.disabled is True
            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert prompt.disabled is False

    asyncio.run(check())


def test_tui_multi_turn_sends_history() -> None:
    provider = FakeProvider()

    async def check() -> None:
        app = app_for_test(provider)
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "第一问"
            await pilot.press("enter")
            await pilot.pause(0.2)
            prompt.text = "第二问"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert provider.requests[1][0] == Message("user", "第一问")
            assert provider.requests[1][1].role == "assistant"

    asyncio.run(check())


def test_tui_shows_tool_summary_and_final_answer(tmp_path) -> None:
    class ToolProvider:
        def __init__(self) -> None:
            self.responses = [
                [StreamEvent("tool_call", tool_call=ToolCall("call-1", "write_file", {"path": "answer.txt", "content": "ok"}))],
                [StreamEvent("text", "已完成写入")],
            ]

        def stream(self, _messages, cancellation=None, tools=()):
            assert len(tools) == 6
            yield from self.responses.pop(0)

    async def check() -> None:
        app = ChatApp(
            ToolProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
            ToolRegistry(tmp_path),
        )
        async with app.run_test() as pilot:
            prompt = app.query_one(Composer)
            prompt.text = "创建文件"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "ok"
            activity = app.query_one(ToolActivity)
            assert "write_file" in str(activity.render())
            assert prompt.disabled is False

    asyncio.run(check())


def test_tui_command_rejection_returns_to_chat(tmp_path) -> None:
    class CommandProvider:
        def __init__(self) -> None:
            self.responses = [
                [StreamEvent("tool_call", tool_call=ToolCall("call-1", "run_command", {"command": "Get-Location"}))],
                [StreamEvent("text", "命令未执行")],
            ]

        def stream(self, _messages, cancellation=None, tools=()):
            yield from self.responses.pop(0)

    async def check() -> None:
        app = ChatApp(
            CommandProvider(),
            ProviderConfig("openai", "gpt-test", "https://example.test", "key"),
            ToolRegistry(tmp_path),
        )
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
