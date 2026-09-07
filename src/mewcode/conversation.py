"""本次运行内的多轮会话与单工具调用管理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mewcode.providers.base import (
    Cancellation, Message, Provider, ProviderError, StreamCancelled, StreamEvent,
    TextContent, ToolCallContent, ToolResultContent, Usage,
)
from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.executor import ApprovalCallback, ToolExecutor
from mewcode.tools.registry import ToolRegistry


@dataclass(frozen=True)
class TurnResult:
    """一轮生成结束后的状态，供终端层决定提示语。"""

    completed: bool
    interrupted: bool
    text: str
    error: str | None = None
    usage: Usage = Usage()


@dataclass
class _CollectedResponse:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = Usage()


class Conversation:
    """仅在当前进程存活的消息历史与单工具状态机。"""

    def __init__(self, provider: Provider, registry: ToolRegistry | None = None) -> None:
        self._provider = provider
        self._messages: list[Message] = []
        self._registry = registry
        self._executor = ToolExecutor(registry) if registry is not None else None

    @property
    def messages(self) -> tuple[Message, ...]:
        """返回不可修改的当前历史快照。"""
        return tuple(self._messages)

    def run_turn(
        self,
        user_text: str,
        on_event: Callable[[StreamEvent], None],
        cancellation: Cancellation | None = None,
        on_tool_result: Callable[[ToolResult], None] | None = None,
        approve_command: ApprovalCallback | None = None,
    ) -> TurnResult:
        """执行一次文本或“单工具 + 最终回复”请求。"""
        self._messages.append(Message(role="user", content=user_text))
        first = _CollectedResponse()
        try:
            self._consume(first, on_event, cancellation)
        except (KeyboardInterrupt, StreamCancelled):
            self._finish_incomplete_turn(first.text)
            return TurnResult(False, True, first.text, usage=first.usage)
        except ProviderError as error:
            self._finish_incomplete_turn(first.text)
            return TurnResult(False, False, first.text, str(error), first.usage)
        except Exception as error:
            self._finish_incomplete_turn(first.text)
            return TurnResult(False, False, first.text, f"请求处理异常：{error}", first.usage)

        if not first.calls:
            if first.text:
                self._messages.append(Message(role="assistant", content=first.text))
            else:
                self._messages.pop()
            return TurnResult(True, False, first.text, usage=first.usage)

        self._append_assistant_response(first)
        first_call = first.calls[0]
        for extra in first.calls[1:]:
            self._record_result(_limit_result(extra, "同一轮首次响应只执行一个工具。"), on_tool_result)

        assert self._executor is not None
        self._record_result(self._executor.execute(first_call, approve_command), on_tool_result)

        final = _CollectedResponse()
        try:
            self._consume(final, on_event, cancellation)
        except (KeyboardInterrupt, StreamCancelled):
            self._finish_final_response(final)
            return TurnResult(False, True, final.text, usage=_add_usage(first.usage, final.usage))
        except ProviderError as error:
            self._finish_final_response(final)
            return TurnResult(False, False, final.text, str(error), _add_usage(first.usage, final.usage))
        except Exception as error:
            self._finish_final_response(final)
            return TurnResult(
                False, False, final.text, f"请求处理异常：{error}", _add_usage(first.usage, final.usage)
            )

        self._append_assistant_response(final)
        for call in final.calls:
            self._record_result(_limit_result(call, "本轮工具调用上限已达到，未执行。"), on_tool_result)
        return TurnResult(True, False, final.text, usage=_add_usage(first.usage, final.usage))

    def _consume(
        self, collected: _CollectedResponse, on_event: Callable[[StreamEvent], None], cancellation: Cancellation | None
    ) -> None:
        for event in self._stream(cancellation):
            on_event(event)
            if event.kind == "text":
                collected.text += event.content
            elif event.kind == "tool_call" and event.tool_call is not None:
                collected.calls.append(event.tool_call)
            elif event.kind == "usage" and event.usage is not None:
                collected.usage = event.usage

    def _stream(self, cancellation: Cancellation | None):
        if self._registry is None:
            return self._provider.stream(self._messages, cancellation) if cancellation else self._provider.stream(self._messages)
        return self._provider.stream(self._messages, cancellation, tools=self._registry.definitions)

    def _append_assistant_response(self, response: _CollectedResponse) -> None:
        blocks = []
        if response.text:
            blocks.append(TextContent(response.text))
        blocks.extend(ToolCallContent(call) for call in response.calls)
        if blocks:
            self._messages.append(Message("assistant", tuple(blocks)))

    def _record_result(self, result: ToolResult, callback: Callable[[ToolResult], None] | None) -> None:
        if self._messages and self._messages[-1].role == "user" and isinstance(self._messages[-1].content, tuple):
            existing = self._messages[-1].blocks
            if all(isinstance(block, ToolResultContent) for block in existing):
                self._messages[-1] = Message("user", (*existing, ToolResultContent(result)))
            else:
                self._messages.append(Message("user", (ToolResultContent(result),)))
        else:
            self._messages.append(Message("user", (ToolResultContent(result),)))
        if callback is not None:
            callback(result)

    def _finish_incomplete_turn(self, text: str) -> None:
        if text:
            self._messages.append(Message(role="assistant", content=text))
        else:
            self._messages.pop()

    def _finish_final_response(self, response: _CollectedResponse) -> None:
        if response.text or response.calls:
            self._append_assistant_response(response)


def _limit_result(call: ToolCall, summary: str) -> ToolResult:
    return ToolResult(call.id, call.name, False, summary, error_code="tool_call_limit")


def _add_usage(first: Usage, second: Usage) -> Usage:
    return Usage(first.input_tokens + second.input_tokens, first.output_tokens + second.output_tokens, first.thinking_tokens + second.thinking_tokens)
