"""将独立 Agent 跑到底并收敛为任务结果。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from yucode.agent import Agent, AgentFinished, ProgressPhase, ProgressUpdated, StopReason, ToolResultReady
from yucode.cancellation import Cancellation
from yucode.permissions import ApprovalCallback
from yucode.subagents.models import TaskOutcome, TaskStatus

# 进度回调：参数为(进度文本, 已完成的工具结果或 None)。
ProgressCallback = Callable[[str, "object | None"], Awaitable[None]]


class RunToCompletion:
    async def run(
        self,
        agent: Agent,
        prompt: str,
        cancellation: Cancellation,
        approve: ApprovalCallback | None = None,
        on_event: ProgressCallback | None = None,
    ) -> TaskOutcome:
        effects: list[str] = []
        iteration = 0
        async for event in agent.run(prompt, cancellation, approve):
            if isinstance(event, ToolResultReady):
                effects.append(f"{event.result.name}{'成功' if event.result.success else '失败'}")
                if on_event is not None:
                    text = f"第 {event.iteration} 轮 · 最近工具：{event.result.name}{'成功' if event.result.success else '失败'}"
                    await on_event(text, event.result)
            elif isinstance(event, ProgressUpdated):
                iteration = event.iteration
                if on_event is not None and event.phase is not ProgressPhase.MODEL:
                    await on_event(f"第 {iteration} 轮 · {event.detail}", None)
            elif isinstance(event, AgentFinished):
                status = _status(event.reason)
                text = event.text or event.error or "没有返回文本。"
                detail = f"子 Agent {('已完成' if status is TaskStatus.COMPLETED else '已结束')}。\n主要结果：{text}\n工具：{'、'.join(effects) or '未调用'}"
                return TaskOutcome(status, detail, event.usage, event.error)
        return TaskOutcome(TaskStatus.FAILED, "子 Agent 未返回完成事件。", error="missing_finished_event")


def _status(reason: StopReason) -> TaskStatus:
    if reason is StopReason.COMPLETED:
        return TaskStatus.COMPLETED
    if reason is StopReason.CANCELLED:
        return TaskStatus.CANCELLED
    return TaskStatus.FAILED
