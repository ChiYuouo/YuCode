"""Anthropic Messages API 的流式 Provider。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from mewcode.config import ProviderConfig
from mewcode.providers.base import (
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

    def __init__(self, config: ProviderConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client

    def stream(
        self,
        messages: Sequence[Message],
        cancellation: Cancellation | None = None,
        tools: Sequence[ToolDefinition] = (),
    ) -> Iterator[StreamEvent]:
        """发送完整历史，并逐段返回 Claude 的可见输出。"""
        client, owns_client = self._get_client()
        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": 4096,
            "stream": True,
            "messages": _serialize_messages(messages),
        }
        if tools:
            payload["tools"] = [_serialize_tool(tool) for tool in tools]
        if self._config.thinking_enabled:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        input_tokens = 0
        try:
            with client.stream(
                "POST",
                f"{self._config.base_url}/v1/messages",
                headers=headers,
                json=payload,
            ) as response:
                self._raise_for_status(response)
                if cancellation is not None:
                    cancellation.attach_close(response.close)
                tool_blocks: dict[int, dict[str, Any]] = {}
                for frame in decode_sse(response.iter_lines()):
                    if cancellation is not None and cancellation.is_cancelled:
                        raise StreamCancelled()
                    event = self._decode_json(frame.data)
                    event_type = event.get("type", frame.event)
                    if event_type == "message_start":
                        input_tokens = _input_tokens(event)
                    elif event_type == "content_block_start":
                        index = event.get("index")
                        block = event.get("content_block")
                        if isinstance(index, int) and isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name")
                            call_id = block.get("id")
                            if not isinstance(name, str) or not isinstance(call_id, str):
                                raise ProviderError("Claude 返回了格式错误的工具调用。")
                            tool_blocks[index] = {"id": call_id, "name": name, "parts": []}
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
                                yield from self._convert_delta(delta)
                    elif event_type == "content_block_stop":
                        index = event.get("index")
                        block = tool_blocks.pop(index, None) if isinstance(index, int) else None
                        if block is not None:
                            arguments = "".join(block["parts"])
                            yield StreamEvent(
                                kind="tool_call",
                                tool_call=ToolCall(
                                    block["id"], block["name"], _decode_arguments(arguments, "Claude")
                                ),
                            )
                    elif event_type == "error":
                        raise ProviderError(f"Claude 流式请求失败：{_error_message(event)}")
                    elif event_type == "message_delta":
                        usage = _usage_from_delta(event, input_tokens)
                        if usage is not None:
                            yield StreamEvent(kind="usage", usage=usage)
        except StreamCancelled:
            raise
        except ProviderError:
            raise
        except httpx.HTTPError as error:
            if cancellation is not None and cancellation.is_cancelled:
                raise StreamCancelled() from error
            raise ProviderError(f"Claude 网络请求失败：{error}") from error
        finally:
            if cancellation is not None:
                cancellation.detach_close()
            if owns_client:
                client.close()

    def _get_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=30.0)
        return httpx.Client(timeout=timeout), True

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
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
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


def _response_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return "服务未返回可读错误信息。"
    return _error_message(data) if isinstance(data, dict) else "服务未返回可读错误信息。"


def _input_tokens(event: dict[str, Any]) -> int:
    message = event.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    return _int_value(usage.get("input_tokens")) if isinstance(usage, dict) else 0


def _usage_from_delta(event: dict[str, Any], input_tokens: int) -> Usage | None:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("output_tokens_details")
    thinking = _int_value(details.get("thinking_tokens")) if isinstance(details, dict) else 0
    return Usage(
        input_tokens=input_tokens,
        output_tokens=_int_value(usage.get("output_tokens")),
        thinking_tokens=thinking,
    )


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _serialize_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }


def _serialize_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
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
    return output


def _decode_arguments(value: str, provider: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProviderError(f"{provider} 返回了无法解析的工具参数。") from error
    if not isinstance(decoded, dict):
        raise ProviderError(f"{provider} 返回的工具参数必须是对象。")
    return decoded
