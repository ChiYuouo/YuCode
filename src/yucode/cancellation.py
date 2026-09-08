"""Provider 与工具共享的异步取消信号。"""

from __future__ import annotations

import asyncio


class Cancellation:
    """可从界面同步触发、由异步任务等待的幂等取消信号。"""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()
