"""OpenAI Responses API 的流式 Provider。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Any

import httpx

from yucode.config import ProviderConfig
from yucode.prompting import ModelRequest
from yucode.providers.base import (
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
from yucode.tools.base import ToolCall, ToolDefinition
from yucode.providers.sse import decode_sse


class OpenAIProvider:
    """把 OpenAI Responses SSE 事件转换为统一文本事件。"""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client

    async def stream(
        self,
        request: ModelRequest,
        cancellation: Cancellation,
    ) -> AsyncIterator[StreamEvent]:
        """发送完整历史，并逐段返回正式回答文本。"""
        client, owns_client = self._get_client()
        payload = {
            "model": self._config.model,
            "input": [
                *[{"role": "developer", "content": message.content} for message in request.runtime_messages],
                *_serialize_messages(request.history),
            ],
            "stream": True,
            "store": False,
            "max_output_tokens": 4096,
            "instructions": request.stable_instructions,
            "prompt_cache_key": request.prompt_cache_key,
        }
        if request.tools:
            payload["tools"] = [_serialize_tool(tool) for tool in request.tools]
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        watcher: asyncio.Task[None] | None = None
        response: httpx.Response | None = None
        try:
            if cancellation.is_cancelled:
                raise StreamCancelled()
            request = client.build_request(
                "POST",
                f"{self._config.base_url}/responses",
                headers=headers,
                json=payload,
            )
            response = await _send_with_cancellation(client, request, cancellation)
            await self._raise_for_status(response)
            watcher = asyncio.create_task(_close_on_cancel(response, cancellation))
            argument_parts: dict[str, str] = {}
            async for frame in decode_sse(response.aiter_lines()):
                if cancellation.is_cancelled:
                    raise StreamCancelled()
                event = self._decode_json(frame.data)
                event_type = event.get("type", frame.event)
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        yield StreamEvent(kind="text", content=delta)
                elif event_type == "response.function_call_arguments.delta":
                    item_id = event.get("item_id")
                    delta = event.get("delta")
                    if isinstance(item_id, str) and isinstance(delta, str):
                        argument_parts[item_id] = argument_parts.get(item_id, "") + delta
                elif event_type == "response.function_call_arguments.done":
                    item_id = event.get("item_id")
                    call_id = event.get("call_id")
                    name = event.get("name")
                    arguments = event.get("arguments")
                    if not isinstance(arguments, str) and isinstance(item_id, str):
                        arguments = argument_parts.get(item_id, "")
                    if not isinstance(call_id, str):
                        call_id = item_id
                    if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, str):
                        raise ProviderError("OpenAI 返回了格式错误的工具调用。")
                    yield StreamEvent(
                        kind="tool_call",
                        tool_call=ToolCall(call_id, name, _decode_arguments(arguments, "OpenAI")),
                    )
                elif event_type == "response.error":
                    message, code = _error_details(event)
                    raise ProviderError(f"OpenAI 流式请求失败：{message}", code)
                elif event_type == "response.completed":
                    usage = _usage_from_completed(event)
                    if usage is not None:
                        yield StreamEvent(kind="usage", usage=usage)
        except StreamCancelled:
            raise
        except ProviderError:
            raise
        except httpx.HTTPError as error:
            if cancellation.is_cancelled:
                raise StreamCancelled() from error
            raise ProviderError(f"OpenAI 网络请求失败：{_http_error_message(error)}") from error
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
    def _decode_json(data: str) -> dict[str, Any]:
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise ProviderError("OpenAI 返回了无法解析的流式数据。") from error
        if not isinstance(event, dict):
            raise ProviderError("OpenAI 返回了格式错误的流式数据。")
        return event

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        await response.aread()
        message, code = _response_error_details(response)
        raise ProviderError(f"OpenAI 请求失败（HTTP {response.status_code}）：{message}", code)


def _error_message(event: dict[str, Any]) -> str:
    return _error_details(event)[0]


def _error_details(event: dict[str, Any]) -> tuple[str, str | None]:
    error = event.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        code = error.get("code")
        return error["message"], code if isinstance(code, str) else None
    if isinstance(event.get("message"), str):
        code = event.get("code")
        return event["message"], code if isinstance(code, str) else None
    return "服务返回未知错误。", None


def _http_error_message(error: httpx.HTTPError) -> str:
    return str(error) or type(error).__name__


def _response_error_message(response: httpx.Response) -> str:
    return _response_error_details(response)[0]


def _response_error_details(response: httpx.Response) -> tuple[str, str | None]:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return "服务未返回可读错误信息。", None
    return _error_details(data) if isinstance(data, dict) else ("服务未返回可读错误信息。", None)


def _usage_from_completed(event: dict[str, Any]) -> Usage | None:
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("input_tokens_details")
    cache = CacheUsage()
    if isinstance(details, dict) and any(key in details for key in ("cached_tokens", "cache_write_tokens")):
        cache = CacheUsage(
            available=True,
            read_input_tokens=_int_value(details.get("cached_tokens")),
            write_input_tokens=_int_value(details.get("cache_write_tokens")),
        )
    return Usage(
        input_tokens=_int_value(usage.get("input_tokens")),
        output_tokens=_int_value(usage.get("output_tokens")),
        cache=cache,
    )


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _serialize_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }


def _serialize_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        text_parts: list[str] = []

        def flush_text() -> None:
            if text_parts:
                output.append({"role": message.role, "content": "".join(text_parts)})
                text_parts.clear()

        for block in message.blocks:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            elif isinstance(block, ToolCallContent):
                flush_text()
                output.append(
                    {
                        "type": "function_call",
                        "call_id": block.call.id,
                        "name": block.call.name,
                        "arguments": json.dumps(block.call.arguments, ensure_ascii=False),
                    }
                )
            elif isinstance(block, ToolResultContent):
                flush_text()
                output.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.result.call_id,
                        "output": block.result.for_model(),
                    }
                )
        flush_text()
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
