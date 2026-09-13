"""Team 斜杠命令测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from yucode.agent import Agent
from yucode.commands import CommandDispatcher, build_builtin_registry, parse_input
from yucode.commands.models import CommandContext
from yucode.config import TeamConfig
from yucode.conversation import Conversation
from yucode.teams.backends import BackendSelector, InProcessBackend
from yucode.teams.service import TeamService
from yucode.tools.registry import ToolRegistry


class Provider:
    async def stream(self, *_args):
        if False:
            yield None


@dataclass
class UI:
    messages: list[tuple[str, bool]] = field(default_factory=list)

    async def show_message(self, text: str, *, error: bool = False) -> None:
        self.messages.append((text, error))


def test_team_list_info_kill_and_delete_are_local(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend()
        teams = TeamService(
            TeamConfig(enabled=True, backend_priority=("in_process",)),
            tmp_path,
            BackendSelector((driver,)),
            (driver,),
            storage_root=tmp_path / "teams",
        )
        agent = Agent(Provider(), Conversation(), ToolRegistry(tmp_path), team_service=teams)
        agent.team_service = teams
        teams.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        ui = UI()
        dispatcher = CommandDispatcher(CommandContext(build_builtin_registry(), ui, agent))

        await dispatcher.dispatch(parse_input("/team list"))
        assert "demo：1 名成员" in ui.messages[-1][0]
        await dispatcher.dispatch(parse_input("/team info demo"))
        assert "alice · reader · created · in_process" in ui.messages[-1][0]
        assert "只读 · Worktree 未创建" in ui.messages[-1][0]
        await dispatcher.dispatch(parse_input("/team kill demo alice"))
        assert teams.get("demo").members[0].state.value == "stopped"
        await dispatcher.dispatch(parse_input("/team delete demo"))
        assert not (tmp_path / "teams" / "demo").exists()
        assert agent.conversation.messages == ()

    asyncio.run(scenario())


def test_team_command_errors_are_local_and_actionable(tmp_path: Path) -> None:
    async def scenario() -> None:
        agent = Agent(Provider(), Conversation(), ToolRegistry(tmp_path))
        ui = UI(); dispatcher = CommandDispatcher(CommandContext(build_builtin_registry(), ui, agent))
        await dispatcher.dispatch(parse_input("/team list"))
        assert ui.messages[-1][1] and "未启用" in ui.messages[-1][0]

        driver = InProcessBackend()
        teams = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                            BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
        agent.team_service = teams
        await dispatcher.dispatch(parse_input("/team info"))
        assert ui.messages[-1][1] and "用法" in ui.messages[-1][0]
        await dispatcher.dispatch(parse_input("/team info missing"))
        assert ui.messages[-1][1] and "找不到团队" in ui.messages[-1][0]
        assert agent.conversation.messages == ()

    asyncio.run(scenario())
