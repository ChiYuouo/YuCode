"""内存后台任务管理与通知回传。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from yucode.cancellation import Cancellation
from yucode.hooks.engine import HookEngine
from yucode.hooks.models import HookContext, HookEvent
from yucode.subagents.models import SubagentKind, TaskNotification, TaskOutcome, TaskSnapshot, TaskStatus
from yucode.subagents.trace import TraceRegistry

TaskWork = Callable[[Cancellation], Awaitable[TaskOutcome]]
NotificationListener = Callable[[TaskNotification], Awaitable[None]]


class TaskManager:
    def __init__(self, trace: TraceRegistry | None = None, hooks: HookEngine | None = None, execution_timeout_seconds: float | None = None) -> None:
        self._trace = trace or TraceRegistry(); self._hooks = hooks; self._execution_timeout = execution_timeout_seconds
        self._tasks: dict[str, TaskSnapshot] = {}; self._workers: dict[str, asyncio.Task[None]] = {}; self._cancellations: dict[str, Cancellation] = {}
        self._notifications: list[TaskNotification] = []; self._foreground: str | None = None
        self._notification_listener: NotificationListener | None = None

    def set_notification_listener(self, listener: NotificationListener | None) -> None:
        """设置界面回显入口；队列保留给主 Agent 后续请求使用。"""
        self._notification_listener = listener

    def start(self, kind: SubagentKind, definition_name: str | None, work: TaskWork, *, parent_task_id: str | None = None, background: bool = False) -> TaskSnapshot:
        task_id = uuid4().hex[:12]; now = datetime.now(UTC)
        status = TaskStatus.BACKGROUND if background else TaskStatus.PENDING
        snapshot = TaskSnapshot(task_id, parent_task_id, kind, definition_name, status, now)
        self._tasks[task_id] = snapshot; self._trace.register(snapshot)
        cancellation = Cancellation(); self._cancellations[task_id] = cancellation
        self._workers[task_id] = asyncio.create_task(self._run(task_id, work, cancellation))
        if not background:
            self._foreground = task_id
        return snapshot

    async def _run(self, task_id: str, work: TaskWork, cancellation: Cancellation) -> None:
        current = self._tasks[task_id]
        self._set(task_id, status=TaskStatus.BACKGROUND if current.status is TaskStatus.BACKGROUND else TaskStatus.RUNNING, started_at=datetime.now(UTC))
        await self._emit(HookEvent.TASK_START, task_id)
        try:
            if self._execution_timeout is None:
                outcome = await work(cancellation)
            else:
                outcome = await asyncio.wait_for(work(cancellation), self._execution_timeout)
        except TimeoutError:
            cancellation.cancel(); outcome = TaskOutcome(TaskStatus.TIMED_OUT, "后台任务已超时停止。", error="timeout")
        except Exception as error:
            outcome = TaskOutcome(TaskStatus.FAILED, f"后台任务异常：{error}", error=str(error))
        status = TaskStatus.CANCELLED if cancellation.is_cancelled and outcome.status is TaskStatus.RUNNING else outcome.status
        self._set(task_id, status=status, ended_at=datetime.now(UTC), summary=outcome.summary, error=outcome.error, usage=outcome.usage)
        await self._emit(HookEvent.TASK_STOP, task_id); await self._emit(HookEvent.TASK_COMPLETE, task_id)
        task = self._tasks[task_id]; notification = TaskNotification(task_id, task.status, task.summary or "任务已结束。", task.usage)
        self._notifications.append(notification)
        await self._emit(HookEvent.SEND_MESSAGE, task_id)
        if self._notification_listener is not None:
            try:
                await self._notification_listener(notification)
            except Exception:
                # UI 回显问题不能影响任务终止、通知入队或后续模型上下文。
                pass
        if self._foreground == task_id: self._foreground = None

    def _set(self, task_id: str, **changes) -> None:
        self._tasks[task_id] = replace(self._tasks[task_id], **changes); self._trace.update(self._tasks[task_id])

    async def _emit(self, event: HookEvent, task_id: str) -> None:
        if self._hooks is None: return
        task = self._tasks[task_id]
        await self._hooks.run_hooks(HookContext(event, task_id=task.id, parent_task_id=task.parent_task_id or "", task_status=task.status.value))

    async def wait_foreground(self, task_id: str, timeout_seconds: float = 120) -> TaskSnapshot:
        worker = self._workers[task_id]
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout_seconds)
        except TimeoutError:
            self.promote(task_id)
        return self._tasks[task_id]

    def promote(self, task_id: str | None = None) -> TaskSnapshot | None:
        target = task_id or self._foreground
        if target is None or target not in self._tasks or self._workers[target].done(): return None
        self._set(target, status=TaskStatus.BACKGROUND)
        if self._foreground == target: self._foreground = None
        return self._tasks[target]

    def cancel(self, task_id: str) -> TaskSnapshot | None:
        task = self._tasks.get(task_id)
        if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.TIMED_OUT}: return None
        self._cancellations[task_id].cancel()
        return task

    def list(self) -> tuple[TaskSnapshot, ...]: return tuple(self._tasks.values())
    def info(self, task_id: str) -> TaskSnapshot | None: return self._tasks.get(task_id)
    def aggregate_usage(self, task_id: str): return self._trace.aggregate_usage(task_id)
    def drain_notifications(self) -> tuple[TaskNotification, ...]:
        values = tuple(self._notifications); self._notifications.clear(); return values
