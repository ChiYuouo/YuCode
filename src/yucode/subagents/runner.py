"""将独立 Agent 跑到底并收敛为任务结果。"""

from __future__ import annotations

from yucode.agent import Agent, AgentFinished, StopReason, ToolResultReady
from yucode.cancellation import Cancellation
from yucode.permissions import ApprovalCallback
from yucode.subagents.models import TaskOutcome, TaskStatus


class RunToCompletion:
    async def run(self, agent: Agent, prompt: str, cancellation: Cancellation, approve: ApprovalCallback | None = None) -> TaskOutcome:
        effects: list[str] = []
        async for event in agent.run(prompt, cancellation, approve):
            if isinstance(event, ToolResultReady):
                effects.append(f"{event.result.name}{'成功' if event.result.success else '失败'}")
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
