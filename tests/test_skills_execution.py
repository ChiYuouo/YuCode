"""Fork Skill 委派提示的边界声明回归测试。"""

import asyncio
from types import SimpleNamespace

from yucode.skills.execution import run_fork
from yucode.tools.base import ToolResult


def test_fork_prompt_declares_isolation_boundary_and_carries_sop() -> None:
    """fork 任务提示必须声明"自己就是隔离任务"，并直接携带 SOP（fork 没有 LoadSkill 工具）。"""
    captured: dict = {}

    class FakeSubagents:
        async def delegate(self, request, call_id, approve=None):
            captured["request"] = request
            captured["call_id"] = call_id
            return ToolResult(call_id, "Agent", True, "子 Agent 已在后台启动，任务标识：x。", content="x")

    parent = SimpleNamespace(_subagents=FakeSubagents())
    active = SimpleNamespace(definition=SimpleNamespace(name="review"), rendered_sop="审查工作区改动，只报告不修复。")

    result = asyncio.run(run_fork(parent, active, "--scope tui"))

    prompt = captured["request"].prompt
    assert "review" in prompt
    assert "不要再派遣新的子 Agent" in prompt
    assert "不要通过命令行启动另一个 YuCode" in prompt
    assert "你没有 LoadSkill 工具" in prompt
    # SOP 必须随任务下发，子 Agent 不应再自行寻找 Skill 定义。
    assert "审查工作区改动，只报告不修复。" in prompt
    assert "用户参数：--scope tui" in prompt
    # 旧文案会诱导子 Agent 以为需要再创建隔离任务，不允许回归。
    assert "请在隔离任务中执行" not in prompt
    assert result.summary == "子 Agent 已在后台启动，任务标识：x。"
    assert result.cancelled is False
