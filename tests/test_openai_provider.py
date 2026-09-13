import asyncio
import json

import httpx
import pytest

from yucode.config import ProviderConfig
from yucode.prompting import ModelRequest, RuntimeMessage
from yucode.providers.base import (
    Cancellation, Message, ProviderError, StreamCancelled, ToolCallContent, ToolResultContent,
)
from yucode.providers.openai import OpenAIProvider
from yucode.tools.base import ToolCall, ToolDefinition, ToolResult


def config() -> ProviderConfig:
    return ProviderConfig("openai", "gpt-test", "https://example.test/v1", "test-key")


def model_request(messages, tools=(), instructions="", runtime=()):
    return ModelRequest(tuple(messages), instructions, tuple(runtime), tuple(tools), "test-cache-key")


def collect(provider, messages, cancellation=None, tools=(), instructions=None, runtime=()):
    async def scenario():
        return [
            event
            async for event in provider.stream(
                model_request(messages, tools, instructions or "", runtime), cancellation or Cancellation()
            )
        ]

    return asyncio.run(scenario())


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

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(config(), client)

    events = collect(provider, [Message("user", "你好")], instructions="自主完成任务")

    assert [event.content for event in events] == ["你", "好"]
    assert captured["url"] == "https://example.test/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["json"] == {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "你好"}],
        "stream": True,
        "store": False,
        "max_output_tokens": 4096,
        "instructions": "自主完成任务",
        "prompt_cache_key": "test-cache-key",
    }


def test_converts_sse_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.error\ndata: {"type":"response.error","error":{"message":"额度不足","code":"prompt_too_long"}}\n\n'
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="额度不足") as error:
        collect(provider, [Message("user", "你好")])
    assert error.value.code == "prompt_too_long"


def test_converts_http_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "认证失败"}})

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="认证失败"):
        collect(provider, [Message("user", "你好")])


def test_names_network_error_when_message_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("", request=request)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="ConnectTimeout"):
        collect(provider, [Message("user", "你好")])


def test_usage_is_emitted_from_completed_event() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":7}}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    events = collect(provider, [Message("user", "你好")])

    assert events[-1].usage is not None
    assert events[-1].usage.input_tokens == 12
    assert events[-1].usage.output_tokens == 7
    assert events[-1].usage.cache.available is False


def test_cancelled_stream_is_not_reported_as_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"片段"}\n\n'
        return httpx.Response(200, content=body)

    cancellation = Cancellation()
    cancellation.cancel()
    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(StreamCancelled):
        collect(provider, [Message("user", "你好")], cancellation)


def test_cancel_interrupts_connection_establishment() -> None:
    async def scenario() -> None:
        handler_started = asyncio.Event()

        async def handler(_: httpx.Request) -> httpx.Response:
            handler_started.set()
            await asyncio.sleep(10)
            return httpx.Response(200, content="")

        cancellation = Cancellation()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIProvider(config(), client)

        async def consume() -> None:
            async for _ in provider.stream(model_request([Message("user", "你好")]), cancellation):
                pass

        task = asyncio.create_task(consume())
        await handler_started.wait()
        cancellation.cancel()
        try:
            with pytest.raises(StreamCancelled):
                await asyncio.wait_for(task, timeout=0.5)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_accumulates_streamed_tool_arguments_and_sends_tool_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        body = (
            # 真实 Responses API 形状：call_id/name 只在 output_item.added 的 item 里下发，
            # arguments.done 事件只有 item_id 与 arguments。
            'event: response.output_item.added\n'
            'data: {"type":"response.output_item.added","item":{"id":"item-1","type":"function_call","status":"in_progress","arguments":"","call_id":"call-1","name":"read_file"},"output_index":1}\n\n'
            'event: response.function_call_arguments.delta\n'
            'data: {"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"{\\"path\\":\\"REA"}\n\n'
            'event: response.function_call_arguments.delta\n'
            'data: {"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"DME.md\\"}"}\n\n'
            'event: response.function_call_arguments.done\n'
            'data: {"type":"response.function_call_arguments.done","item_id":"item-1","arguments":"{\\"path\\":\\"README.md\\"}"}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    definition = ToolDefinition("read_file", "读取文件", {"type": "object", "properties": {}})
    events = collect(provider, [Message("user", "读 README")], tools=[definition])

    assert events[-1].tool_call == ToolCall("call-1", "read_file", {"path": "README.md"})
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["tools"][0]["name"] == "read_file"


def test_accepts_call_metadata_inline_in_done_event() -> None:
    """部分兼容实现直接在 done 事件里携带 call_id/name，应作为兜底被接受。"""

    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: response.function_call_arguments.delta\n'
            'data: {"type":"response.function_call_arguments.delta","item_id":"item-1","delta":"{}"}\n\n'
            'event: response.function_call_arguments.done\n'
            'data: {"type":"response.function_call_arguments.done","item_id":"item-1","call_id":"call-1","name":"read_file","arguments":"{}"}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    definition = ToolDefinition("read_file", "读取文件", {"type": "object", "properties": {}})
    events = collect(provider, [Message("user", "读 README")], tools=[definition])

    assert events[-1].tool_call == ToolCall("call-1", "read_file", {})


def test_rejects_tool_call_without_known_name() -> None:
    """added 事件没给 name、done 也没有兜底字段时，宁可失败也不猜测。"""

    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: response.function_call_arguments.done\n'
            'data: {"type":"response.function_call_arguments.done","item_id":"item-1","arguments":"{}"}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    definition = ToolDefinition("read_file", "读取文件", {"type": "object", "properties": {}})

    with pytest.raises(ProviderError, match="格式错误的工具调用"):
        collect(provider, [Message("user", "读 README")], tools=[definition])


def test_serializes_tool_call_and_result_in_responses_input() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content="")

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    call = ToolCall("call-1", "read_file", {"path": "README.md"})
    result = ToolResult("call-1", "read_file", True, "已读取", "内容")
    collect(provider, [Message("assistant", (ToolCallContent(call),)), Message("user", (ToolResultContent(result),))])

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["input"][0]["type"] == "function_call"
    assert payload["input"][1]["type"] == "function_call_output"


def test_sends_runtime_reminder_as_developer_message_and_reads_cache_usage() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        body = (
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":7,"input_tokens_details":{"cached_tokens":8,"cache_write_tokens":2}}}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    events = collect(
        provider,
        [Message("user", "你好")],
        instructions="稳定规则",
        runtime=(RuntimeMessage("<system-reminder>补充</system-reminder>"),),
    )

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["input"][0] == {"role": "developer", "content": "<system-reminder>补充</system-reminder>"}
    assert payload["input"][1] == {"role": "user", "content": "你好"}
    assert events[-1].usage is not None
    assert events[-1].usage.cache.read_input_tokens == 8
    assert events[-1].usage.cache.write_input_tokens == 2
