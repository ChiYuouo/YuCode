"""本次运行内的多轮会话管理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mewcode.providers.base import (
    Cancellation,
    Message,
    Provider,
    ProviderError,
    StreamCancelled,
    StreamEvent,
    Usage,
)


@dataclass(frozen=True)
class TurnResult:
    """一轮生成结束后的状态，供终端层决定提示语。"""

    completed: bool
    interrupted: bool
    text: str
    error: str | None = None
    usage: Usage = Usage()


class Conversation:
    """仅在当前进程存活的消息历史。"""

    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self._messages: list[Message] = []

    @property
    def messages(self) -> tuple[Message, ...]:
        """返回不可修改的当前历史快照。"""
        return tuple(self._messages)

    def run_turn(
        self,
        user_text: str,
        on_event: Callable[[StreamEvent], None],
        cancellation: Cancellation | None = None,
    ) -> TurnResult:
        """执行一轮请求，并在任何可恢复结束状态下维护一致历史。"""
        self._messages.append(Message(role="user", content=user_text))
        text_parts: list[str] = []
        usage = Usage()

        try:
            stream = (
                self._provider.stream(self._messages, cancellation)
                if cancellation is not None
                else self._provider.stream(self._messages)
            )
            for event in stream:
                on_event(event)
                if event.kind == "text":
                    text_parts.append(event.content)
                elif event.kind == "usage" and event.usage is not None:
                    usage = event.usage
        except KeyboardInterrupt:
            text = "".join(text_parts)
            self._finish_incomplete_turn(text)
            return TurnResult(completed=False, interrupted=True, text=text, usage=usage)
        except StreamCancelled:
            text = "".join(text_parts)
            self._finish_incomplete_turn(text)
            return TurnResult(completed=False, interrupted=True, text=text, usage=usage)
        except ProviderError as error:
            text = "".join(text_parts)
            self._finish_incomplete_turn(text)
            return TurnResult(
                completed=False, interrupted=False, text=text, error=str(error), usage=usage
            )

        text = "".join(text_parts)
        if text:
            self._messages.append(Message(role="assistant", content=text))
        else:
            self._messages.pop()
        return TurnResult(completed=True, interrupted=False, text=text, usage=usage)

    def _finish_incomplete_turn(self, text: str) -> None:
        if text:
            self._messages.append(Message(role="assistant", content=text))
        else:
            self._messages.pop()
