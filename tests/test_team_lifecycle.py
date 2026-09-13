import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import pytest
import json

from yucode.agent import Agent
from yucode.config import TeamConfig, WorktreeConfig
from yucode.conversation import Conversation
from yucode.providers.base import StreamEvent
from yucode.tools.base import ToolCall
from yucode.teams.backends import BackendSelector, InProcessBackend
from yucode.teams.backends import BackendAvailability, BackendHandle
from yucode.teams.models import MemberState, TeamBackend
from yucode.teams.models import TeamTaskState
from yucode.teams.models import MessageKind
from yucode.teams.service import TeamService, TeamServiceError
from yucode.tools.registry import ToolRegistry
from yucode.worktrees.manager import WorktreeManager


class Provider:
    def __init__(self) -> None: self.requests = []
    async def stream(self, request, cancellation) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent("text", "README 主要介绍安装、配置和工具系统。")


class WritingProvider:
    def __init__(self) -> None: self.round = 0
    async def stream(self, request, cancellation) -> AsyncIterator[StreamEvent]:
        self.round += 1
        if self.round == 1:
            yield StreamEvent("tool_call", tool_call=ToolCall("write", "write_file", {"path": "alice.txt", "content": "done"}))
        elif self.round == 2:
            yield StreamEvent("tool_call", tool_call=ToolCall("read", "read_file", {"path": "alice.txt"}))
        else:
            yield StreamEvent("text", "文件已创建并验证。")


class Worktrees:
    def __init__(self, root: Path) -> None: self.root = root
    def create(self, slug: str, temporary: bool = False):
        path = self.root.joinpath(*slug.split("/")); path.mkdir(parents=True)
        subprocess.run(("git", "init", "-q"), cwd=path, check=True)
        subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=path, check=True)
        subprocess.run(("git", "config", "user.name", "Test"), cwd=path, check=True)
        return SimpleNamespace(path=path, slug=slug)


class TmuxDriver:
    backend = TeamBackend.TMUX
    def __init__(self): self.command = None
    def availability(self): return BackendAvailability(self.backend, True, "test")
    async def start(self, member, command): self.command = command; return BackendHandle(self.backend, "%1")
    async def wake(self, member): pass
    async def stop(self, member): pass


def test_in_process_member_runs_in_worktree_and_persists_transcript(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,), Worktrees(tmp_path / ".yucode" / "worktrees"), tmp_path / "teams")
        provider = Provider()
        Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=service)
        team = service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": True},))
        task = service.tasks.create(team, "读 README", "总结章节", assignee="alice")
        await service.spawn("demo", "alice", "读取 README.md 并总结", task_id=task.task_id)
        await asyncio.gather(*tuple(service._running.values()))
        loaded = service.get("demo"); member = loaded.members[0]
        assert member.state is MemberState.IDLE
        assert member.workspace_root != tmp_path
        assert member.transcript_id
        transcript = loaded.root / "members" / "alice" / "transcripts" / f"{member.transcript_id}.jsonl"
        assert transcript.is_file()
        assert "README" in transcript.read_text(encoding="utf-8")
        assert service.drain_notifications()
        assert service.tasks.get(loaded, task.task_id).state is TeamTaskState.COMPLETED
        await service.spawn("demo", "alice", "继续补充")
        await asyncio.gather(*tuple(service._running.values()))
        assert len(provider.requests) == 2
        assert any("README 主要介绍" in str(message.blocks) for message in provider.requests[1].history)
    asyncio.run(scenario())


def test_approval_member_cannot_write_until_lead_approves(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend(); provider = Provider()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,), Worktrees(tmp_path / ".yucode" / "worktrees"), tmp_path / "teams")
        Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=service)
        service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True, "requires_approval": True},))
        await service.spawn("demo", "alice", "修改 README")
        await asyncio.gather(*tuple(service._running.values()))
        first_names = {item.name for item in provider.requests[0].tools}
        assert {"write_file", "edit_file", "run_command"}.isdisjoint(first_names)
        assert "SendMessage" in first_names
        member = service.get("demo").members[0]
        with pytest.raises(TeamServiceError, match="计划请求"):
            await service.send("demo", "alice", ("lead",), "计划", MessageKind.PLAN_REQUEST,
                               protocol={"request_id": "wrong"})
        with pytest.raises(TeamServiceError, match="不匹配"):
            await service.send("demo", "lead", ("alice",), "错误批准", MessageKind.PLAN_DECISION,
                               protocol={"request_id": "expired", "decision": "approved"})
        await service.send("demo", "alice", ("lead",), "这是实施计划", MessageKind.PLAN_REQUEST,
                           protocol={"request_id": member.approval_request_id})
        await service.send("demo", "lead", ("alice",), "批准", MessageKind.PLAN_DECISION,
                           protocol={"request_id": member.approval_request_id, "decision": "approved"})
        await asyncio.gather(*tuple(service._running.values()))
        second_names = {item.name for item in provider.requests[1].tools}
        assert "write_file" in second_names and "run_command" in second_names
    asyncio.run(scenario())


def test_rejected_plan_clears_pending_work_without_granting_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend(); provider = Provider()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,),
                              Worktrees(tmp_path / ".yucode" / "worktrees"), tmp_path / "teams")
        Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=service)
        service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True, "requires_approval": True},))
        await service.spawn("demo", "alice", "修改 README")
        await asyncio.gather(*tuple(service._running.values()))
        request_id = service.get("demo").members[0].approval_request_id
        await service.send("demo", "alice", ("lead",), "这是实施计划", MessageKind.PLAN_REQUEST,
                           protocol={"request_id": request_id})
        await service.send("demo", "lead", ("alice",), "驳回", MessageKind.PLAN_DECISION,
                           protocol={"request_id": request_id, "decision": "rejected"})
        member = service.get("demo").members[0]
        assert member.pending_prompt is None and member.approved_request_id is None
        assert {"write_file", "edit_file", "run_command"}.isdisjoint(
            {item.name for item in provider.requests[0].tools}
        )

    asyncio.run(scenario())


def test_corrupt_transcript_marks_member_failed_and_keeps_message_unread(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend(); provider = Provider()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
        Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=service)
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        await service.spawn("demo", "alice", "第一次")
        await asyncio.gather(*tuple(service._running.values()))
        assert {"write_file", "edit_file", "run_command"}.isdisjoint(
            {item.name for item in provider.requests[0].tools}
        )
        member = service.get("demo").members[0]
        transcript = service.get("demo").root / "members" / "alice" / "transcripts" / f"{member.transcript_id}.jsonl"
        transcript.write_text("{broken", encoding="utf-8")

        with pytest.raises(TeamServiceError, match="恢复"):
            await service.send("demo", "lead", ("alice",), "继续处理")
        failed = service.get("demo").members[0]
        assert failed.state is MemberState.FAILED
        assert [item.body for item in service.mailbox.unread_for(service.get("demo"), failed)] == ["继续处理"]
        assert "已保留" in service.drain_notifications()[-1]

    asyncio.run(scenario())


def test_delete_team_stops_created_member_automatically(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
        service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
        await service.delete_team("demo")
        assert not (tmp_path / "teams" / "demo").exists()

    asyncio.run(scenario())


def test_tampered_member_workspace_is_rejected(tmp_path: Path) -> None:
    driver = InProcessBackend()
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
    team = service.create("demo", "lead", ({"name": "alice", "role": "reader", "writable": False},))
    path = team.root / "team.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["members"][0]["workspace_root"] = str(tmp_path.parent)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TeamServiceError, match="工作目录"):
        service.get("demo")


def test_tampered_writable_without_worktree_is_rejected(tmp_path: Path) -> None:
    driver = InProcessBackend()
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), storage_root=tmp_path / "teams")
    team = service.create("demo", "lead", ({"name": "alice", "role": "reader"},))
    path = team.root / "team.json"; raw = json.loads(path.read_text(encoding="utf-8"))
    raw["members"][0]["writable"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TeamServiceError, match="writable"):
        service.get("demo")


def test_team_roster_worktrees_and_mailboxes_survive_service_restart_and_stay_isolated(tmp_path: Path) -> None:
    driver = InProcessBackend(); worktrees = Worktrees(tmp_path / ".yucode" / "worktrees")
    config = TeamConfig(enabled=True, backend_priority=("in_process",))
    first = TeamService(config, tmp_path, BackendSelector((driver,)), (driver,), worktrees, tmp_path / "teams")
    team = first.create("demo", "lead", (
        {"name": "alice", "role": "dev", "writable": True},
        {"name": "bob", "role": "reader"},
    ))
    other = first.create("other", "lead", ({"name": "bob", "role": "reader", "writable": False},))
    first.mailbox.send(team, "lead", ("bob",), "仅属于 demo")

    restarted = TeamService(config, tmp_path, BackendSelector((driver,)), (driver,), worktrees, tmp_path / "teams")
    loaded = restarted.get("demo")
    assert loaded.members[0].workspace_root != loaded.members[1].workspace_root == tmp_path.resolve()
    assert loaded.members[0].writable and not loaded.members[1].writable
    assert loaded.members[1].role == "reader"
    other_bob = restarted.get("other").members[0]
    assert restarted.mailbox.unread_for(other, other_bob) == ()
    assert loaded.root != other.root


def test_delete_cleans_safe_worktree_but_preserves_unmerged_changes(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q", "-b", "master"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=tmp_path, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "base"), cwd=tmp_path, check=True)
    driver = InProcessBackend(); manager = WorktreeManager(tmp_path)
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), manager, tmp_path / "teams")

    safe = service.create("safe", "lead", ({"name": "alice", "role": "dev", "writable": True},))
    safe_path = safe.members[0].workspace_root
    service.delete("safe")
    assert not safe_path.exists() and not safe.root.exists()

    unsafe = service.create("unsafe", "lead", ({"name": "bob", "role": "dev", "writable": True},))
    unsafe_path = unsafe.members[0].workspace_root
    (unsafe_path / "unmerged.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(TeamServiceError, match="不能安全清理"):
        service.delete("unsafe")
    assert unsafe_path.is_dir() and unsafe.root.is_dir()


def test_delete_removes_orphaned_team_metadata_after_worktree_and_branch_are_gone(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q", "-b", "master"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=tmp_path, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "base"), cwd=tmp_path, check=True)
    driver = InProcessBackend(); manager = WorktreeManager(tmp_path)
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), manager, tmp_path / "teams")
    team = service.create("orphan", "lead", ({"name": "alice", "role": "dev", "writable": True},))
    member = team.members[0]
    assert manager.remove(member.worktree_slug, force=True, delete_branch=True).removed
    assert not member.workspace_root.exists()
    service.delete("orphan")
    assert not team.root.exists()


def test_external_backend_launches_controlled_member_entry(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = TmuxDriver()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("tmux",)), tmp_path,
                              BackendSelector((driver,)), (driver,), Worktrees(tmp_path / ".yucode" / "worktrees"), tmp_path / "teams")
        service.create("demo", "lead", ({"name": "alice", "role": "reader"},))
        team = await service.spawn("demo", "alice", "读 README")
        assert team.members[0].state is MemberState.RUNNING
        assert team.members[0].backend_handle == "%1"
        assert driver.command[2:5] == ("yucode.cli", "--team-member", "demo")
    asyncio.run(scenario())


def test_delegated_writable_member_can_edit_without_background_permission_ui(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver = InProcessBackend(); provider = WritingProvider()
        service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                              BackendSelector((driver,)), (driver,),
                              Worktrees(tmp_path / ".yucode" / "worktrees"), tmp_path / "teams")
        Agent(provider, Conversation(), ToolRegistry(tmp_path), team_service=service)
        service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True},))
        await service.spawn("demo", "alice", "创建 alice.txt 并写入 done")
        await asyncio.gather(*tuple(service._running.values()))
        member = service.get("demo").members[0]
        assert (member.workspace_root / "alice.txt").read_text(encoding="utf-8") == "done"
        assert member.state is MemberState.IDLE
        assert subprocess.run(("git", "status", "--porcelain"), cwd=member.workspace_root,
                              capture_output=True, text=True, check=True).stdout == ""

    asyncio.run(scenario())


def _repository_with_gitignore(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q", "-b", "master"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=tmp_path, check=True)
    subprocess.run(("git", "config", "user.name", "Test"), cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".yucode\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("base", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=tmp_path, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "base"), cwd=tmp_path, check=True)


def test_member_worktree_scaffolding_never_becomes_a_member_commit(tmp_path: Path) -> None:
    _repository_with_gitignore(tmp_path)
    (tmp_path / ".env.local").write_text("SECRET=1", encoding="utf-8")
    driver = InProcessBackend()
    manager = WorktreeManager(tmp_path, WorktreeConfig(worktreeinclude=(".env.local",)))
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), manager, tmp_path / "teams")
    team = service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True},))
    member = team.members[0]
    assert (member.workspace_root / ".env.local").is_file(), "初始化应复制 worktreeinclude"
    assert service._merger.commit_member("alice", member.workspace_root) is None, "初始化产物不应成为成员提交"
    branch = f"yucode/worktree/{member.worktree_slug}"
    assert service._merger.safe_to_cleanup(member.workspace_root, branch)[0], "只有初始化产物时应视为干净"
    service.delete("demo")
    assert not team.root.exists()


def test_delete_succeeds_after_worktree_removed_and_branch_still_exists(tmp_path: Path) -> None:
    _repository_with_gitignore(tmp_path)
    driver = InProcessBackend()
    manager = WorktreeManager(tmp_path)
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), manager, tmp_path / "teams")
    team = service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True},))
    member = team.members[0]
    assert manager.remove(member.worktree_slug).removed, "用户先执行 /worktree remove"
    assert not member.workspace_root.exists()
    service.delete("demo")
    assert not team.root.exists()


def test_delete_reports_actionable_reason_for_deleted_member_worktree(tmp_path: Path) -> None:
    _repository_with_gitignore(tmp_path)
    driver = InProcessBackend()
    manager = WorktreeManager(tmp_path)
    service = TeamService(TeamConfig(enabled=True, backend_priority=("in_process",)), tmp_path,
                          BackendSelector((driver,)), (driver,), manager, tmp_path / "teams")
    team = service.create("demo", "lead", ({"name": "alice", "role": "dev", "writable": True},))
    member = team.members[0]
    (member.workspace_root / "alice.txt").write_text("done", encoding="utf-8")
    assert service._merger.commit_member("alice", member.workspace_root), "成员真实改动应产生提交"
    shutil.rmtree(member.workspace_root)
    with pytest.raises(TeamServiceError) as failure:
        service.delete("demo")
    message = str(failure.value)
    assert "已被外部删除" in message and "丢弃分支" in message
    assert "WinError" not in message and "目录名称无效" not in message
    assert team.root.is_dir(), "无法安全清理时必须保留团队元数据"
