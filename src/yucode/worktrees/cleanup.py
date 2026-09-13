"""TUI 事件循环内的低频 Worktree 清理。"""

from __future__ import annotations

import asyncio

from yucode.worktrees.manager import WorktreeManager


class WorktreeCleanupService:
    def __init__(self, manager: WorktreeManager, interval_seconds: float) -> None:
        self._manager = manager; self._interval = interval_seconds; self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None: self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None: return
        self._task.cancel()
        try: await self._task
        except asyncio.CancelledError: pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try: self._manager.cleanup_stale()
            except Exception: pass
            await asyncio.sleep(self._interval)
