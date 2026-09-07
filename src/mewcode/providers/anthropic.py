"""Anthropic Messages API 的流式 Provider。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import suppress
from typing import Any

import httpx

from mewcode.config import ProviderConfig
from mewcode.prompting import ModelRequest, RuntimeMessage
from mewcode.providers.base import (
    CacheUsage,
    Cancellation,
    Message,
    ProviderError,
    StreamCancelled,
    StreamEvent,
    TextContent,
    ToolCallContent,
    ToolResultContent,
    Usage,
)
from mewcode.tools.base import ToolCall, ToolDefinition
from mewcode.providers.sse import decode_sse


class AnthropicProvider:
    """把 Claude Messages SSE 事件转换为文本和思考事件。"""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client

    async def stream(
        self,
        request: ModelRequest,
        cancellation: Cancellation,
    ) -> AsyncIterator[StreamEvent]:
        """发送完整历史，并逐段返回 Claude 的可见输出。"""
        client, owns_client = self._get_client()
        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": 4096,
            "stream": True,
            "messages": _serialize_messages(request.history, request.runtime_messages),
            "system": _serialize_system(request.stable_instructions),
        }
        if request.tools:
            payload["tools"] = _serialize_tools(request.tools)
        if self._config.thinking_enabled:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        input_usage = Usage()
        watcher: asyncio.Task[None] | None = None
        response: httpx.Response | None = None
        try:
            if cancellation.is_cancelled:
                raise StreamCancelled()
            request = client.build_request(
                "POST",
                f"{self._config.base_url}/v1/messages",
                headers=headers,
                json=payload,
            )
            response = await _send_with_cancellation(client, request, cancellation)
            await self._raise_for_status(response)
            watcher = asyncio.create_task(_close_on_cancel(response, cancellation))
            tool_blocks: dict[int, dict[str, Any]] = {}
            async for frame in decode_sse(response.aiter_lines()):
                if cancellation.is_cancelled:
                    raise StreamCancelled()
                event = self._decode_json(frame.data)
                event_type = event.get("type", frame.event)
                if event_type == "message_start":
                    input_usage = _input_usage(event)
                elif event_type == "content_block_start":
                    index = event.get("index")
                    block = event.get("content_block")
                    if isinstance(index, int) and isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name")
                        call_id = block.get("id")
                        if not isinstance(name, str) or not isinstance(call_id, str):
                            raise ProviderError("Claude 返回了格式错误的工具调用。")
                        initial_input = block.get("input")
                        tool_blocks[index] = {
                            "id": call_id,
                            "name": name,
                            "parts": [],
                            "initial_input": initial_input if isinstance(initial_input, dict) else None,
                        }
                elif event_type == "content_block_delta":
                    delta = event.get("delta")
                    if isinstance(delta, dict):
                        if delta.get("type") == "input_json_delta":
                            index = event.get("index")
                            partial = delta.get("partial_json")
                            block = tool_blocks.get(index) if isinstance(index, int) else None
                            if block is not None and isinstance(partial, str):
                                block["parts"].append(partial)
                        else:
                            for converted in self._convert_delta(delta):
                                yield converted
                elif event_type == "content_block_stop":
                    index = event.get("index")
                    block = tool_blocks.pop(index, None) if isinstance(index, int) else None
                    if block is not None:
                        arguments = "".join(block["parts"])
                        parsed_arguments = (
                            _decode_arguments(arguments, "Claude")
                            if arguments
                            else block["initial_input"]
                        )
                        if parsed_arguments is None:
                            raise ProviderError("Claude 返回了无法解析的工具参数。")
                        yield StreamEvent(
                            kind="tool_call",
                            tool_call=ToolCall(
                                block["id"], block["name"], parsed_arguments
                            ),
                        )
                elif event_type == "error":
                    raise ProviderError(f"Claude 流式请求失败：{_error_message(event)}")
                elif event_type == "message_delta":
                    usage = _usage_from_delta(event, input_usage)
                    if usage is not None:
                        yield StreamEvent(kind="usage", usage=usage)
        except StreamCancelled:
            raise
        except ProviderError:
            raise
        except httpx.HTTPError as error:
            if cancellation.is_cancelled:
                raise StreamCancelled() from error
            raise ProviderError(f"Claude 网络请求失败：{_http_error_message(error)}") from error
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            if response is not None:
                await response.aclose()
            if owns_client:
                await client.aclose()

    def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0)
        return httpx.AsyncClient(timeout=timeout), True

    @staticmethod
    def _convert_delta(delta: dict[str, Any]) -> Iterator[StreamEvent]:
        if delta.get("type") == "thinking_delta":
            content = delta.get("thinking")
            if isinstance(content, str) and content:
                yield StreamEvent(kind="thinking", content=content)
        elif delta.get("type") == "text_delta":
            content = delta.get("text")
            if isinstance(content, str) and content:
                yield StreamEvent(kind="text", content=content)

    @staticmethod
    def _decode_json(data: str) -> dict[str, Any]:
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise ProviderError("Claude 返回了无法解析的流式数据。") from error
        if not isinstance(event, dict):
            raise ProviderError("Claude 返回了格式错误的流式数据。")
        return event

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        await response.aread()
        raise ProviderError(
            f"Claude 请求失败（HTTP {response.status_code}）：{_response_error_message(response)}"
        )


def _error_message(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(event.get("message"), str):
        return event["message"]
    return "服务返回未知错误。"


def _http_error_message(error: httpx.HTTPError) -> str:
    return str(error) or type(error).__name__


def _response_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return "服务未返回可读错误信息。"
    return _error_message(data) if isinstance(data, dict) else "服务未返回可读错误信息。"


def _input_usage(event: dict[str, Any]) -> Usage:
    message = event.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return Usage()
    return Usage(
        input_tokens=_int_value(usage.get("input_tokens")),
        cache=_cache_usage(usage),
    )


def _usage_from_delta(event: dict[str, Any], input_usage: Usage) -> Usage | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_tokens_details")
    thinking = _int_value(details.get("thinking_tokens")) if isinstance(details, dict) else 0
    cache = _cache_usage(usage)
    if not cache.available:
        cache = input_usage.cache
    return Usage(
        input_tokens=input_usage.input_tokens,
        output_tokens=_int_value(usage.get("output_tokens")),
        thinking_tokens=thinking,
        cache=cache,
    )


def _cache_usage(usage: dict[str, Any]) -> CacheUsage:
    keys = ("cache_read_input_tokens", "cache_creation_input_tokens")
    if not any(key in usage for key in keys):
        return CacheUsage()
    return CacheUsage(
        available=True,
        read_input_tokens=_int_value(usage.get("cache_read_input_tokens")),
        write_input_tokens=_int_value(usage.get("cache_creation_input_tokens")),
    )


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _serialize_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }


def _serialize_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """在稳定工具前缀末端声明 Claude 的短期缓存断点。"""
    output = [_serialize_tool(tool) for tool in tools]
    if output:
        output[-1]["cache_control"] = {"type": "ephemeral"}
    return output


def _serialize_system(instructions: str) -> list[dict[str, Any]]:
    """稳定系统提示独立形成可缓存前缀。"""
    return [{
        "type": "text",
        "text": instructions,
        "cache_control": {"type": "ephemeral"},
    }]


def _serialize_messages(
    messages: Sequence[Message], runtime_messages: Sequence[RuntimeMessage] = ()
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message.content, str):
            output.append({"role": message.role, "content": message.content})
            continue
        blocks: list[dict[str, Any]] = []
        for block in message.blocks:
            if isinstance(block, TextContent):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolCallContent):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.call.id,
                        "name": block.call.name,
                        "input": dict(block.call.arguments),
                    }
                )
            elif isinstance(block, ToolResultContent):
                result = block.result
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.for_model(),
                        "is_error": not result.success,
                    }
                )
        output.append({"role": message.role, "content": blocks})
    if not runtime_messages:
        return output

    reminder = "\n\n".join(message.content for message in runtime_messages)
    reminder_block = {"type": "text", "text": reminder}
    if output and output[0].get("role") == "user":
        content = output[0]["content"]
        if isinstance(content, str):
            output[0]["content"] = [reminder_block, {"type": "text", "text": content}]
        elif isinstance(content, list):
            output[0]["content"] = [reminder_block, *content]
    else:
        output.insert(0, {"role": "user", "content": [reminder_block]})
    return output


def _decode_arguments(value: str, provider: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProviderError(f"{provider} 返回了无法解析的工具参数。") from error
    if not isinstance(decoded, dict):
        raise ProviderError(f"{provider} 返回的工具参数必须是对象。")
    return decoded


async def _close_on_cancel(response: httpx.Response, cancellation: Cancellation) -> None:
    await cancellation.wait()
    await response.aclose()


async def _send_with_cancellation(
    client: httpx.AsyncClient,
    request: httpx.Request,
    cancellation: Cancellation,
) -> httpx.Response:
    """让连接建立阶段也能被用户取消。"""
    send_task = asyncio.create_task(client.send(request, stream=True))
    cancel_task = asyncio.create_task(cancellation.wait())
    done, _ = await asyncio.wait(
        {send_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cancel_task in done and cancellation.is_cancelled:
        if not send_task.done():
            send_task.cancel()
        try:
            response = await send_task
        except asyncio.CancelledError:
            pass
        else:
            await response.aclose()
        raise StreamCancelled()
    cancel_task.cancel()
    with suppress(asyncio.CancelledError):
        await cancel_task
    return await send_task
