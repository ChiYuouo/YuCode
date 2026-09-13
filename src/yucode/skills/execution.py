"""Fork 模式 Skill 统一委派给子 Agent 服务。"""

from __future__ import annotations

from dataclasses import dataclass

from yucode.agent import Agent
from yucode.permissions import ApprovalCallback
from yucode.skills.models import ActiveSkill
from yucode.subagents.models import SubagentKind, SubagentRequest


@dataclass(frozen=True)
class ForkResult:
    summary: str
    cancelled: bool = False


async def run_fork(parent: Agent, active: ActiveSkill, arguments: str, approve: ApprovalCallback | None = None, progress=None) -> ForkResult:
    """保留旧调用面，但不再创建独立的旧式 Agent Loop。"""
    service = getattr(parent, "_subagents", None)
    if service is None:
        return ForkResult("当前没有可用的子 Agent 服务，无法执行 Fork Skill。")
    prompt = f"请在隔离任务中执行 Skill「{active.definition.name}」。用户参数：{arguments}"
    result = await service.delegate(SubagentRequest(SubagentKind.FORK, prompt), f"skill-{active.definition.name}", approve)
    return ForkResult(result.summary, not result.success and result.error_code == "cancelled")
