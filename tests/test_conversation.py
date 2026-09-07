from mewcode.conversation import Conversation
from mewcode.providers.base import TextContent, ToolCallContent, ToolResultContent
from mewcode.tools.base import ToolCall, ToolResult


def test_stores_complete_assistant_response_and_results_in_order() -> None:
    conversation = Conversation()
    calls = [ToolCall("1", "read_file", {"path": "a"}), ToolCall("2", "read_file", {"path": "b"})]
    results = [ToolResult("1", "read_file", True, "a"), ToolResult("2", "read_file", False, "b")]
    conversation.append_user("读取")
    conversation.append_assistant("开始", calls)
    conversation.append_tool_results(results)
    assert isinstance(conversation.messages[1].blocks[0], TextContent)
    assert [block.call.id for block in conversation.messages[1].blocks[1:] if isinstance(block, ToolCallContent)] == ["1", "2"]
    assert [block.result.call_id for block in conversation.messages[2].blocks if isinstance(block, ToolResultContent)] == ["1", "2"]


def test_partial_text_is_kept_without_thinking_or_usage() -> None:
    conversation = Conversation()
    conversation.append_user("问题")
    conversation.append_partial_assistant("部分")
    assert conversation.messages[-1].content == "部分"


def test_new_user_text_joins_unanswered_tool_results() -> None:
    conversation = Conversation()
    conversation.append_assistant("", [ToolCall("1", "read_file", {"path": "a"})])
    conversation.append_tool_results([ToolResult("1", "read_file", False, "失败")])
    conversation.append_user("继续")
    blocks = conversation.messages[-1].blocks
    assert isinstance(blocks[0], ToolResultContent)
    assert isinstance(blocks[1], TextContent) and blocks[1].text == "继续"


def test_discards_only_last_plain_user() -> None:
    conversation = Conversation()
    conversation.append_user("问题")
    conversation.discard_last_plain_user()
    assert conversation.messages == ()
