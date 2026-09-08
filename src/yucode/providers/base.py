"""Provider 层共享的数据结构与接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Literal, Protocol

from yucode.cancellation import Cancellation
from yucode.tools.base import ToolCall, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from yucode.prompting import ModelRequest


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
class CacheUsage:
    """供应商返回的提示缓存用量；不可用不等同于零。"""

    available: bool = False
    read_input_tokens: int = 0
    write_input_tokens: int = 0


@dataclass(frozen=True)
class Usage:
    """供应商在单轮结束时返回的实际 Token 与缓存用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache: CacheUsage = field(default_factory=CacheUsage)


class ProviderError(RuntimeError):
    """供应商请求或响应解析失败。"""


class StreamCancelled(RuntimeError):
    """用户主动停止当前生成。"""


class Provider(Protocol):
    """任何可流式生成文本的模型后端。"""

    async def stream(
        self,
        request: "ModelRequest",
        cancellation: Cancellation,
    ) -> AsyncIterator[StreamEvent]:
        """使用结构化请求生成统一流事件。"""
