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
    # fork 没有 LoadSkill 工具（系统工具只挂在主 Agent 上），SOP 必须随任务直接下发，
    # 并显式声明隔离边界，防止子 Agent 试图再套娃派遣。
    prompt = (
        "你本身就是在隔离对话中运行的子 Agent，不要再派遣新的子 Agent，"
        "也不要通过命令行启动另一个 YuCode；你没有 LoadSkill 工具，"
        f"Skill「{active.definition.name}」的完整指令已直接附在下面，请严格按其执行。\n\n"
        f"===== Skill「{active.definition.name}」指令开始 =====\n"
        f"{active.rendered_sop}\n"
        f"===== Skill 指令结束 =====\n\n"
        f"用户参数：{arguments}"
    )
    result = await service.delegate(SubagentRequest(SubagentKind.FORK, prompt), f"skill-{active.definition.name}", approve)
    return ForkResult(result.summary, not result.success and result.error_code == "cancelled")
