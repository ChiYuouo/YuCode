import json

import httpx
import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import (
    Cancellation, Message, ProviderError, StreamCancelled, ToolCallContent, ToolResultContent,
)
from mewcode.tools.base import ToolCall, ToolDefinition, ToolResult


def config(thinking_enabled: bool = False) -> ProviderConfig:
    return ProviderConfig(
        "anthropic", "claude-test", "https://example.test", "test-key", thinking_enabled
    )


def test_streams_text_and_thinking_in_order() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        body = (
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"分析"}}\n\n'
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"答案"}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(
        config(thinking_enabled=True), httpx.Client(transport=httpx.MockTransport(handler))
    )

    events = list(provider.stream([Message("user", "问题")]))

    assert [(event.kind, event.content) for event in events] == [
        ("thinking", "分析"),
        ("text", "答案"),
    ]
    assert captured["url"] == "https://example.test/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["json"]["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_omits_thinking_when_disabled() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content="")

    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    assert list(provider.stream([Message("user", "问题")])) == []
    assert "thinking" not in captured["json"]


def test_converts_sse_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: error\ndata: {"type":"error","error":{"message":"认证失败"}}\n\n'
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="认证失败"):
        list(provider.stream([Message("user", "问题")]))


def test_converts_http_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "无效密钥"}})

    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="无效密钥"):
        list(provider.stream([Message("user", "问题")]))


def test_usage_includes_thinking_tokens() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: message_start\n'
            'data: {"type":"message_start","message":{"usage":{"input_tokens":14}}}\n\n'
            'event: message_delta\n'
            'data: {"type":"message_delta","usage":{"output_tokens":9,"output_tokens_details":{"thinking_tokens":5}}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(config(True), httpx.Client(transport=httpx.MockTransport(handler)))

    events = list(provider.stream([Message("user", "问题")]))

    assert events[-1].usage is not None
    assert events[-1].usage.input_tokens == 14
    assert events[-1].usage.output_tokens == 9
    assert events[-1].usage.thinking_tokens == 5


def test_cancelled_stream_is_not_reported_as_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"片段"}}\n\n'
        return httpx.Response(200, content=body)

    cancellation = Cancellation()
    cancellation.cancel()
    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(StreamCancelled):
        list(provider.stream([Message("user", "问题")], cancellation))


def test_accumulates_streamed_tool_arguments_and_sends_tool_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        body = (
            'event: content_block_start\n'
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool-1","name":"read_file","input":{}}}\n\n'
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":\\"REA"}}\n\n'
            'event: content_block_delta\n'
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"DME.md\\"}"}}\n\n'
            'event: content_block_stop\n'
            'data: {"type":"content_block_stop","index":0}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    definition = ToolDefinition("read_file", "读取文件", {"type": "object", "properties": {}})
    events = list(provider.stream([Message("user", "读 README")], tools=[definition]))

    assert events[-1].tool_call == ToolCall("tool-1", "read_file", {"path": "README.md"})
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["tools"][0]["input_schema"]["type"] == "object"


def test_serializes_tool_use_and_result_content_blocks() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content="")

    provider = AnthropicProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    call = ToolCall("tool-1", "read_file", {"path": "README.md"})
    result = ToolResult("tool-1", "read_file", False, "拒绝", error_code="user_rejected")
    list(provider.stream([Message("assistant", (ToolCallContent(call),)), Message("user", (ToolResultContent(result),))]))

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["messages"][0]["content"][0]["type"] == "tool_use"
    assert payload["messages"][1]["content"][0]["is_error"] is True
