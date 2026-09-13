import asyncio

from yucode.cancellation import Cancellation
from yucode.subagents.models import AgentIsolation
from yucode.subagents.tool import AgentTool
from yucode.tools.base import ToolContext, ToolResult


class Service:
    def __init__(self) -> None:
        self.request = None

    async def delegate(self, request, call_id, approve=None):
        self.request = request
        return ToolResult(call_id, "Agent", True, "已启动。")


def test_agent_tool_passes_one_off_worktree_isolation(tmp_path) -> None:
    async def check() -> None:
        service = Service()
        result = await AgentTool(service).execute(
            {"subagent_type": "general-purpose", "prompt": "修改文件", "isolation": "worktree"},
            ToolContext(tmp_path), "call", Cancellation(),
        )
        assert result.success
        assert service.request.isolation is AgentIsolation.WORKTREE
    asyncio.run(check())


def test_agent_tool_rejects_unknown_isolation_before_delegating(tmp_path) -> None:
    async def check() -> None:
        service = Service()
        result = await AgentTool(service).execute(
            {"subagent_type": "general-purpose", "prompt": "修改文件", "isolation": "shared"},
            ToolContext(tmp_path), "call", Cancellation(),
        )
        assert not result.success
        assert service.request is None
        assert "isolation" in result.summary
    asyncio.run(check())
