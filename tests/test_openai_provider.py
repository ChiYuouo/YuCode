import json

import httpx
import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.base import (
    Cancellation, Message, ProviderError, StreamCancelled, ToolCallContent, ToolResultContent,
)
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools.base import ToolCall, ToolDefinition, ToolResult


def config() -> ProviderConfig:
    return ProviderConfig("openai", "gpt-test", "https://example.test/v1", "test-key")


def test_streams_text_and_sends_responses_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        body = (
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"你"}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"好"}\n\n'
            'event: response.completed\n'
            'data: {"type":"response.completed"}\n\n'
        )
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(config(), client)

    events = list(provider.stream([Message("user", "你好")]))

    assert [event.content for event in events] == ["你", "好"]
    assert captured["url"] == "https://example.test/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["json"] == {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "你好"}],
        "stream": True,
        "store": False,
        "max_output_tokens": 4096,
    }


def test_converts_sse_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.error\ndata: {"type":"response.error","error":{"message":"额度不足"}}\n\n'
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="额度不足"):
        list(provider.stream([Message("user", "你好")]))


def test_converts_http_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "认证失败"}})

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="认证失败"):
        list(provider.stream([Message("user", "你好")]))


def test_usage_is_emitted_from_completed_event() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":7}}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    events = list(provider.stream([Message("user", "你好")]))

    assert events[-1].usage is not None
    assert events[-1].usage.input_tokens == 12
    assert events[-1].usage.output_tokens == 7


def test_cancelled_stream_is_not_reported_as_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"片段"}\n\n'
        return httpx.Response(200, content=body)

    cancellation = Cancellation()
    cancellation.cancel()
    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(StreamCancelled):
        list(provider.stream([Message("user", "你好")], cancellation))


def test_accumulates_streamed_tool_arguments_and_sends_tool_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        body = (
            'event: response.function_call_arguments.delta\n'
            'data: {"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"{\\"path\\":\\"REA"}\n\n'
            'event: response.function_call_arguments.delta\n'
            'data: {"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"DME.md\\"}"}\n\n'
            'event: response.function_call_arguments.done\n'
            'data: {"type":"response.function_call_arguments.done","item_id":"item-1","call_id":"call-1","name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    definition = ToolDefinition("read_file", "读取文件", {"type": "object", "properties": {}})
    events = list(provider.stream([Message("user", "读 README")], tools=[definition]))

    assert events[-1].tool_call == ToolCall("call-1", "read_file", {"path": "README.md"})
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["tools"][0]["name"] == "read_file"


def test_serializes_tool_call_and_result_in_responses_input() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content="")

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    call = ToolCall("call-1", "read_file", {"path": "README.md"})
    result = ToolResult("call-1", "read_file", True, "已读取", "内容")
    list(provider.stream([Message("assistant", (ToolCallContent(call),)), Message("user", (ToolResultContent(result),))]))

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["input"][0]["type"] == "function_call"
    assert payload["input"][1]["type"] == "function_call_output"
