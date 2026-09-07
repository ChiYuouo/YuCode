from collections.abc import Iterator, Sequence

from mewcode.conversation import Conversation
from mewcode.providers.base import Message, ProviderError, StreamEvent, Usage
from mewcode.tools.base import ToolCall
from mewcode.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self, events: list[StreamEvent] | None = None, error: Exception | None = None) -> None:
        self.events = events or []
        self.error = error
        self.requests: list[tuple[Message, ...]] = []

    def stream(self, messages: Sequence[Message]) -> Iterator[StreamEvent]:
        self.requests.append(tuple(messages))
        yield from self.events
        if self.error is not None:
            raise self.error


def test_multi_turn_keeps_complete_answer_for_next_turn() -> None:
    provider = FakeProvider([StreamEvent("text", "第一轮")])
    conversation = Conversation(provider)

    first = conversation.run_turn("你好", lambda _: None)
    provider.events = [StreamEvent("text", "第二轮")]
    second = conversation.run_turn("继续", lambda _: None)

    assert first.completed and second.completed
    assert provider.requests[1] == (
        Message("user", "你好"),
        Message("assistant", "第一轮"),
        Message("user", "继续"),
    )


def test_does_not_save_thinking_content() -> None:
    provider = FakeProvider([StreamEvent("thinking", "内部分析"), StreamEvent("text", "最终答案")])
    conversation = Conversation(provider)

    conversation.run_turn("问题", lambda _: None)

    assert conversation.messages == (
        Message("user", "问题"),
        Message("assistant", "最终答案"),
    )


def test_keeps_partial_text_when_provider_fails() -> None:
    provider = FakeProvider([StreamEvent("text", "部分")], ProviderError("网络断开"))
    conversation = Conversation(provider)

    result = conversation.run_turn("问题", lambda _: None)

    assert result.error == "网络断开"
    assert conversation.messages[-1] == Message("assistant", "部分")


def test_converts_unexpected_provider_failure_to_recoverable_turn() -> None:
    conversation = Conversation(FakeProvider(error=ValueError("意外格式")))

    result = conversation.run_turn("问题", lambda _: None)

    assert result.error == "请求处理异常：意外格式"
    assert conversation.messages == ()


def test_rolls_back_user_message_when_interrupted_before_text() -> None:
    provider = FakeProvider([StreamEvent("thinking", "分析")], KeyboardInterrupt())
    conversation = Conversation(provider)

    result = conversation.run_turn("问题", lambda _: None)

    assert result.interrupted is True
    assert conversation.messages == ()


def test_new_conversation_starts_without_previous_history() -> None:
    first = Conversation(FakeProvider([StreamEvent("text", "旧回答")]))
    first.run_turn("旧问题", lambda _: None)

    restarted = Conversation(FakeProvider())

    assert first.messages
    assert restarted.messages == ()


def test_keeps_usage_out_of_message_history() -> None:
    provider = FakeProvider(
        [StreamEvent("text", "回答"), StreamEvent("usage", usage=Usage(4, 3, 2))]
    )
    conversation = Conversation(provider)

    result = conversation.run_turn("问题", lambda _: None)

    assert result.usage == Usage(4, 3, 2)
    assert all("usage" not in message.content for message in conversation.messages)


def test_executes_one_tool_then_streams_final_answer(tmp_path) -> None:
    class ToolProvider:
        def __init__(self) -> None:
            self.requests = []
            self.responses = [
                [StreamEvent("tool_call", tool_call=ToolCall("call-1", "write_file", {"path": "a.txt", "content": "完成"}))],
                [StreamEvent("text", "文件已写入")],
            ]

        def stream(self, messages, cancellation=None, tools=()):
            self.requests.append((tuple(messages), tuple(tools)))
            yield from self.responses.pop(0)

    provider = ToolProvider()
    results = []
    conversation = Conversation(provider, ToolRegistry(tmp_path))

    turn = conversation.run_turn("创建文件", lambda _: None, on_tool_result=results.append)

    assert turn.completed and turn.text == "文件已写入"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "完成"
    assert results[0].success
    assert len(provider.requests) == 2
    assert any(block.__class__.__name__ == "ToolResultContent" for block in provider.requests[1][0][-1].blocks)


def test_executes_only_first_tool_and_rejects_second_phase_tools(tmp_path) -> None:
    class ToolProvider:
        def __init__(self) -> None:
            self.responses = [
                [
                    StreamEvent("tool_call", tool_call=ToolCall("one", "write_file", {"path": "one.txt", "content": "1"})),
                    StreamEvent("tool_call", tool_call=ToolCall("two", "write_file", {"path": "two.txt", "content": "2"})),
                ],
                [StreamEvent("tool_call", tool_call=ToolCall("three", "write_file", {"path": "three.txt", "content": "3"}))],
            ]

        def stream(self, messages, cancellation=None, tools=()):
            yield from self.responses.pop(0)

    results = []
    conversation = Conversation(ToolProvider(), ToolRegistry(tmp_path))
    conversation.run_turn("创建", lambda _: None, on_tool_result=results.append)

    assert (tmp_path / "one.txt").exists()
    assert not (tmp_path / "two.txt").exists()
    assert not (tmp_path / "three.txt").exists()
    assert [result.error_code for result in results] == ["tool_call_limit", None, "tool_call_limit"]
