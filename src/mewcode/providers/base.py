"""Provider 层共享的数据结构与接口。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from typing import Callable, Iterator, Literal, Protocol, Sequence

from mewcode.tools.base import ToolCall, ToolDefinition, ToolResult


@dataclass(frozen=True)
class TextContent:
    """会话中的一段普通文本。"""

    text: str


@dataclass(frozen=True)
class ToolCallContent:
    """保存到历史中的模型工具调用。"""

    call: ToolCall


@dataclass(frozen=True)
class ToolResultContent:
    """保存到历史中的工具执行结果。"""

    result: ToolResult


ContentBlock = TextContent | ToolCallContent | ToolResultContent


@dataclass(frozen=True)
class Message:
    """一条可发送给模型的消息，兼容既有纯文本构造方式。"""

    role: Literal["user", "assistant"]
    content: str | tuple[ContentBlock, ...]

    @property
    def blocks(self) -> tuple[ContentBlock, ...]:
        if isinstance(self.content, str):
            return (TextContent(self.content),)
        return self.content


@dataclass(frozen=True)
class StreamEvent:
    """供应商无关的流式输出片段。"""

    kind: Literal["thinking", "text", "usage", "tool_call"]
    content: str = ""
    usage: "Usage | None" = None
    tool_call: ToolCall | None = None


@dataclass(frozen=True)
class Usage:
    """供应商在单轮结束时返回的实际 Token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0


class ProviderError(RuntimeError):
    """供应商请求或响应解析失败。"""


class StreamCancelled(RuntimeError):
    """用户主动停止当前生成。"""


class Cancellation:
    """可从 UI 线程安全取消阻塞 HTTP 流的控制器。"""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._close: Callable[[], None] | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def attach_close(self, callback: Callable[[], None]) -> None:
        with self._lock:
            cancelled = self._event.is_set()
            if not cancelled:
                self._close = callback
        if cancelled:
            callback()

    def detach_close(self) -> None:
        with self._lock:
            self._close = None

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            callback = self._close
            self._close = None
        if callback is not None:
            callback()


class Provider(Protocol):
    """任何可流式生成文本的模型后端。"""

    def stream(
        self,
        messages: Sequence[Message],
        cancellation: Cancellation | None = None,
        tools: Sequence[ToolDefinition] = (),
    ) -> Iterator[StreamEvent]:
        """使用完整历史生成统一流事件。"""
