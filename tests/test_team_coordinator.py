from yucode.config import TeamConfig
from yucode.teams.coordinator import active, allowed_names
from yucode.agent import Agent
from yucode.conversation import Conversation
from yucode.providers.base import StreamEvent
from yucode.cancellation import Cancellation
from yucode.tools.registry import ToolRegistry
from yucode.teams.backends import BackendSelector, InProcessBackend
from yucode.teams.service import TeamService
from yucode.teams.coordinator import COORDINATOR_NOTICE
import asyncio


def test_coordinator_requires_both_locks() -> None:
    assert not active(TeamConfig(coordinator_enabled=True), {})
    assert not active(TeamConfig(coordinator_enabled=False), {"YUCODE_COORDINATOR": "1"})
    assert active(TeamConfig(coordinator_enabled=True), {"YUCODE_COORDINATOR": "1"})


def test_coordinator_removes_direct_file_writes() -> None:
    assert allowed_names({"read_file", "write_file", "edit_file", "run_command"}, True) == {"read_file", "run_command"}


def test_agent_coordinator_view_really_hides_write_tools(tmp_path) -> None:
    class Provider:
        def __init__(self): self.request = None
        async def stream(self, request, cancellation):
            self.request = request
            yield StreamEvent("text", "ok")
    async def scenario():
        provider = Provider(); agent = Agent(provider, Conversation(), ToolRegistry(tmp_path), coordinator_mode=True)
        [item async for item in agent.run("检查", Cancellation())]
        names = {item.name for item in provider.request.tools}
        assert "write_file" not in names and "edit_file" not in names
        assert "read_file" in names and "run_command" in names
    asyncio.run(scenario())


def test_coordinator_keeps_team_tools_and_injects_four_stage_notice(tmp_path) -> None:
    class Provider:
        def __init__(self): self.request = None
        async def stream(self, request, cancellation):
            self.request = request
            yield StreamEvent("text", "ok")
    async def scenario():
        driver = InProcessBackend(); provider = Provider()
        teams = TeamService(TeamConfig(enabled=True, coordinator_enabled=True, backend_priority=("in_process",)),
                            tmp_path, BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
        agent = Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=teams,
                      coordinator_mode=True, runtime_notices=(COORDINATOR_NOTICE,))
        [item async for item in agent.run("协调任务", Cancellation())]
        names = {item.name for item in provider.request.tools}
        assert {"TeamSpawn", "TeamStop", "TeamMerge", "SendMessage"}.issubset(names)
        assert {"write_file", "edit_file"}.isdisjoint(names)
        notices = "\n".join(item.content for item in provider.request.runtime_messages)
        assert all(stage in notices for stage in ("Research", "Synthesis", "Implementation", "Verification"))
    asyncio.run(scenario())
