"""隔离模式的最小 Agent 运行器与摘要回流。"""

from __future__ import annotations

from dataclasses import dataclass

from collections.abc import Awaitable, Callable

from yucode.agent import Agent, AgentFinished, ProgressUpdated, StopReason, ToolResultReady
from yucode.cancellation import Cancellation
from yucode.conversation import Conversation
from yucode.permissions import ApprovalCallback
from yucode.prompting import SystemPromptBuilder
from yucode.skills.models import ActiveSkill


@dataclass(frozen=True)
class ForkResult:
    summary: str
    cancelled: bool = False


async def run_fork(
    parent: Agent, active: ActiveSkill, arguments: str, approve: ApprovalCallback | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> ForkResult:
    source = parent.conversation.messages
    scope = active.definition.history_scope
    if scope.kind == "none":
        history = ()
    elif scope.kind == "all":
        history = source
    else:
        history = source[-scope.turns * 2:]
    child_conversation = Conversation()
    child_conversation.replace_for_recovery(history)
    base = parent._prompt_builder
    prompt = SystemPromptBuilder(
        getattr(base, "_custom_instructions", ""),
        active_skills=(active.rendered_sop,),
        long_term_memory=getattr(base, "_long_term_memory", ""),
    )
    child = Agent(parent._provider, child_conversation, parent._registry, parent._max_iterations, prompt_builder=prompt, permissions=parent.permissions)
    text = f"请在隔离任务中执行 Skill「{active.definition.name}」。用户参数：{arguments}"
    final = ""
    effects: list[str] = []
    async for event in child.run(text, Cancellation(), approve):
        if isinstance(event, ProgressUpdated) and progress is not None:
            await progress(f"Skill「{active.definition.name}」：{event.detail}")
        elif isinstance(event, ToolResultReady):
            effects.append(f"{event.result.name}{'成功' if event.result.success else '失败'}")
        elif isinstance(event, AgentFinished):
            final = event.text or event.error or "没有返回文本。"
            cancelled = event.reason is StopReason.CANCELLED
            state = "已取消" if cancelled else ("已完成" if event.reason is StopReason.COMPLETED else "失败")
            summary = f"Skill「{active.definition.name}」独立执行{state}。\n主要结果：{final}\n工具：{'、'.join(effects) or '未调用'}\n下一步：根据以上结果继续。"
            return ForkResult(summary, cancelled)
    return ForkResult(f"Skill「{active.definition.name}」独立执行失败：未收到完成事件。")
