"""Agent Team 工具注入和端到端调用测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from yucode.agent import Agent
from yucode.cancellation import Cancellation
from yucode.config import TeamConfig
from yucode.conversation import Conversation
from yucode.teams.backends import BackendSelector, InProcessBackend
from yucode.teams.service import TeamService
from yucode.teams.tools import TaskCreateTool, TaskGetTool, TaskUpdateTool, TeamCreateTool
from yucode.subagents.factory import SubagentFactory
from yucode.subagents.policy import ToolPolicy
from yucode.tools.base import ToolContext
from yucode.tools.registry import ToolRegistry
from yucode.permissions import PermissionManager
from yucode.tools.base import ToolCall


class Provider:
    async def stream(self, *_args):
        if False:
            yield None


def service(tmp_path: Path) -> TeamService:
    driver = InProcessBackend()
    return TeamService(
        TeamConfig(enabled=True, backend_priority=("in_process",)),
        tmp_path,
        BackendSelector((driver,)),
        (driver,),
        storage_root=tmp_path / "teams",
    )


def test_team_tools_only_appear_on_team_lead(tmp_path: Path) -> None:
    normal = Agent(Provider(), Conversation(), ToolRegistry(tmp_path))
    normal_names = {item.name for item in normal._skill_view()[0].definitions}
    assert "TeamCreate" not in normal_names and "SendMessage" not in normal_names

    lead = Agent(Provider(), Conversation(), ToolRegistry(tmp_path), team_service=service(tmp_path))
    lead_names = {item.name for item in lead._skill_view()[0].definitions}
    assert {
        "TeamCreate", "TeamDelete", "TeamSpawn", "TeamStop", "TeamMerge",
        "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "SendMessage",
    }.issubset(lead_names)
    fork = SubagentFactory(ToolPolicy()).create_fork(lead)
    fork_names = {item.name for item in fork._skill_view()[0].definitions}
    assert "TaskCreate" not in fork_names and "SendMessage" not in fork_names
    request = PermissionManager(tmp_path).request_for(ToolCall(
        "call", "TeamSpawn", {"team": "demo", "member": "alice", "prompt": "读 README"}
    ))
    assert "demo/alice" in request.summary and "参数无效" not in request.summary


def test_team_and_task_tools_persist_dependency_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        teams = service(tmp_path)
        context = ToolContext(tmp_path)
        token = Cancellation()
        created = await TeamCreateTool(teams).execute(
            {"name": "demo", "members": [{"name": "alice", "role": "reader"}]},
            context, "create", token,
        )
        assert created.success and (tmp_path / "teams" / "demo" / "team.json").is_file()
        member = teams.get("demo").members[0]
        assert not member.writable and member.worktree_slug is None and member.workspace_root == tmp_path.resolve()
        assert "只读" in created.content and "Worktree 未创建" in created.content
        assert "in_process：可用" in created.content and "团队目录" in created.content

        first = await TaskCreateTool(teams).execute(
            {"team": "demo", "title": "调研", "detail": "阅读 README", "assignee": "alice"},
            context, "first", token,
        )
        second = await TaskCreateTool(teams).execute(
            {"team": "demo", "title": "总结", "detail": "整理章节", "blocked_by": [first.content]},
            context, "second", token,
        )
        detail = await TaskGetTool(teams).execute(
            {"team": "demo", "task_id": second.content}, context, "get", token,
        )
        assert detail.success and "状态：blocked" in detail.content and first.content in detail.content

        started = await TaskUpdateTool(teams).execute(
            {"team": "demo", "task_id": first.content, "state": "in_progress"},
            context, "start", token,
        )
        assert started.success
        updated = await TaskUpdateTool(teams).execute(
            {"team": "demo", "task_id": first.content, "state": "completed"},
            context, "update", token,
        )
        assert updated.success
        ready = await TaskGetTool(teams).execute(
            {"team": "demo", "task_id": second.content}, context, "ready", token,
        )
        assert "状态：ready" in ready.content and "is_ready：True" in ready.content

    asyncio.run(scenario())
