"""当前进程内、供应商无关的会话历史。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from yucode.providers.base import Message, TextContent, ToolCallContent, ToolResultContent
from yucode.tools.base import ToolCall, ToolResult


@dataclass(frozen=True)
class ConversationEvent:
    """一次原始追加操作，供持久化层记录。"""

    kind: Literal["user", "assistant", "partial_assistant", "tool_results"]
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()


ConversationRecorder = Callable[[ConversationEvent], None]


class Conversation:
    """只维护消息历史，不负责模型调用或工具执行。"""

    def __init__(self, recorder: ConversationRecorder | None = None) -> None:
        self._messages: list[Message] = []
        self._recorder = recorder

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def replace_messages(self, messages: Sequence[Message]) -> None:
        """原子替换完整历史，供上下文压缩在准备完成后提交。"""
        self._messages = list(messages)

    def replace_for_recovery(self, messages: Sequence[Message]) -> None:
        """恢复历史但不回写会话存档。"""
        self._messages = list(messages)

    def append_user(self, text: str) -> None:
        """追加用户正文；必要时接在尚未获回复的工具结果后。"""
        if self._messages and self._messages[-1].role == "user" and not isinstance(self._messages[-1].content, str):
            blocks = self._messages[-1].blocks
            if all(isinstance(block, ToolResultContent) for block in blocks):
                self._messages[-1] = Message("user", (*blocks, TextContent(text)))
                self._record(ConversationEvent("user", text))
                return
        self._messages.append(Message("user", text))
        self._record(ConversationEvent("user", text))

    def append_assistant(self, text: str, calls: Sequence[ToolCall] = ()) -> None:
        blocks = []
        if text:
            blocks.append(TextContent(text))
        blocks.extend(ToolCallContent(call) for call in calls)
        if blocks:
            self._messages.append(Message("assistant", tuple(blocks)))
            self._record(ConversationEvent("assistant", text, tuple(calls)))

    def append_partial_assistant(self, text: str) -> None:
        if text:
            self._messages.append(Message("assistant", text))
            self._record(ConversationEvent("partial_assistant", text))

    def append_tool_results(self, results: Sequence[ToolResult]) -> None:
        if results:
            self._messages.append(
                Message("user", tuple(ToolResultContent(result) for result in results))
            )
            self._record(ConversationEvent("tool_results", results=tuple(results)))

    def discard_last_plain_user(self) -> None:
        """取消首轮空响应时恢复到请求前的历史。"""
        if self._messages and self._messages[-1].role == "user" and isinstance(self._messages[-1].content, str):
            self._messages.pop()

    def _record(self, event: ConversationEvent) -> None:
        if self._recorder is not None:
            self._recorder(event)
