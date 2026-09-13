from datetime import UTC, datetime
from pathlib import Path
import subprocess

from yucode.teams.merge import TeamMergeService
from yucode.teams.models import AgentTeam, MemberState, TeamBackend, TeamMember


def git(root: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-b", "master"); git(root, "config", "user.email", "test@example.com"); git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("base", encoding="utf-8"); git(root, "add", "."); git(root, "commit", "-m", "base")
    return root


def test_merges_idle_member_branch(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "master"); git(tmp_path, "config", "user.email", "test@example.com"); git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("base", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "yucode/worktree/team/demo-alice")
    git(tmp_path, "switch", "yucode/worktree/team/demo-alice")
    (tmp_path / "alice.txt").write_text("done", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "alice")
    git(tmp_path, "switch", "master")
    now = datetime.now(UTC)
    member = TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS, state=MemberState.IDLE, worktree_slug="team/demo-alice", writable=True)
    team = AgentTeam(1, "demo", "lead", tmp_path / "meta", (member,), now, now)
    result = TeamMergeService(tmp_path).merge(team)
    assert result.success
    assert (tmp_path / "alice.txt").read_text(encoding="utf-8") == "done"


def test_conflict_aborts_current_merge(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "master"); git(tmp_path, "config", "user.email", "test@example.com"); git(tmp_path, "config", "user.name", "Test")
    target = tmp_path / "same.txt"; target.write_text("base\n", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "yucode/worktree/team/demo-alice")
    git(tmp_path, "switch", "yucode/worktree/team/demo-alice"); target.write_text("alice\n", encoding="utf-8"); git(tmp_path, "commit", "-am", "alice")
    git(tmp_path, "switch", "master"); target.write_text("lead\n", encoding="utf-8"); git(tmp_path, "commit", "-am", "lead")
    now = datetime.now(UTC)
    member = TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS, state=MemberState.IDLE, worktree_slug="team/demo-alice", writable=True)
    result = TeamMergeService(tmp_path).merge(AgentTeam(1, "demo", "lead", tmp_path / "meta", (member,), now, now))
    assert not result.success
    assert git(tmp_path, "status", "--porcelain") == ""
    assert target.read_text(encoding="utf-8") == "lead\n"


def test_append_only_conflict_is_resolved_deterministically(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "master"); git(tmp_path, "config", "user.email", "test@example.com"); git(tmp_path, "config", "user.name", "Test")
    target = tmp_path / "notes.txt"; target.write_text("base\n", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "yucode/worktree/team/demo-alice")
    git(tmp_path, "switch", "yucode/worktree/team/demo-alice"); target.write_text("base\nalice\n", encoding="utf-8"); git(tmp_path, "commit", "-am", "alice")
    git(tmp_path, "switch", "master"); target.write_text("base\nlead\n", encoding="utf-8"); git(tmp_path, "commit", "-am", "lead")
    now = datetime.now(UTC)
    member = TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS, state=MemberState.IDLE, worktree_slug="team/demo-alice", writable=True)
    result = TeamMergeService(tmp_path).merge(AgentTeam(1, "demo", "lead", tmp_path / "meta", (member,), now, now))
    assert result.success
    assert target.read_text(encoding="utf-8") == "base\nlead\nalice\n"


def test_merges_two_completed_member_branches(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "master"); git(tmp_path, "config", "user.email", "test@example.com"); git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("base", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "yucode/worktree/team/demo-alice"); git(tmp_path, "branch", "yucode/worktree/team/demo-bob")
    git(tmp_path, "switch", "yucode/worktree/team/demo-alice")
    (tmp_path / "alice.txt").write_text("alice", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "alice")
    git(tmp_path, "switch", "yucode/worktree/team/demo-bob")
    (tmp_path / "bob.txt").write_text("bob", encoding="utf-8"); git(tmp_path, "add", "."); git(tmp_path, "commit", "-m", "bob")
    git(tmp_path, "switch", "master")
    now = datetime.now(UTC)
    members = (
        TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS, state=MemberState.IDLE, worktree_slug="team/demo-alice", writable=True),
        TeamMember("bob", "b1", "dev", tmp_path, TeamBackend.IN_PROCESS, state=MemberState.IDLE, worktree_slug="team/demo-bob", writable=True),
    )
    result = TeamMergeService(tmp_path).merge(AgentTeam(1, "demo", "lead", tmp_path / "meta", members, now, now))
    assert result.success and [item.member for item in result.members] == ["alice", "bob"]
    assert (tmp_path / "alice.txt").is_file() and (tmp_path / "bob.txt").is_file()
    assert git(tmp_path, "status", "--porcelain") == ""


def test_merge_converges_failed_member_and_skips_discarded_branch(tmp_path: Path) -> None:
    root = repository(tmp_path)
    git(root, "branch", "yucode/worktree/team/demo-alice")
    git(root, "switch", "yucode/worktree/team/demo-alice")
    (root / "alice.txt").write_text("alice", encoding="utf-8"); git(root, "add", "."); git(root, "commit", "-m", "alice")
    git(root, "switch", "master")
    now = datetime.now(UTC)
    members = (
        TeamMember("alice", "a1", "dev", root, TeamBackend.IN_PROCESS, state=MemberState.FAILED, worktree_slug="team/demo-alice", writable=True),
        TeamMember("bob", "b1", "dev", root, TeamBackend.IN_PROCESS, state=MemberState.STOPPED, worktree_slug="team/demo-bob", writable=True),
    )
    result = TeamMergeService(root).merge(AgentTeam(1, "demo", "lead", root / "meta", members, now, now))
    assert result.success, "失败成员的既有提交仍应可收敛，分支已丢弃的成员应跳过而不是中止"
    assert (root / "alice.txt").read_text(encoding="utf-8") == "alice"
    assert [(item.member, item.status) for item in result.members] == [("alice", "已合并"), ("bob", "跳过")]


def _member_worktree(root: Path, slug: str) -> Path:
    worktree = root / ".yucode" / "worktrees" / slug
    git(root, "worktree", "add", "-q", "-b", f"yucode/worktree/{slug}", str(worktree), "HEAD")
    return worktree


def test_commit_member_skips_worktree_scaffolding(tmp_path: Path) -> None:
    root = repository(tmp_path)
    worktree = _member_worktree(root, "team/demo-alice")
    merger = TeamMergeService(root, scaffolding=("node_modules",))
    dependencies = worktree / "node_modules"; dependencies.mkdir()
    (dependencies / "pkg.js").write_text("x", encoding="utf-8")
    assert merger.commit_member("alice", worktree) is None, "只有初始化脚手架时不应产生成员提交"
    (worktree / "alice.txt").write_text("done", encoding="utf-8")
    assert merger.commit_member("alice", worktree)
    assert git(worktree, "show", "--name-only", "--format=", "HEAD").split() == ["alice.txt"]


def test_safe_to_cleanup_explains_deleted_worktree_with_unmerged_branch(tmp_path: Path) -> None:
    root = repository(tmp_path)
    branch = "yucode/worktree/team/demo-alice"
    git(root, "branch", branch)
    git(root, "switch", branch); (root / "alice.txt").write_text("alice", encoding="utf-8"); git(root, "add", "."); git(root, "commit", "-m", "alice"); git(root, "switch", "master")
    safe, reason = TeamMergeService(root).safe_to_cleanup(root / ".yucode" / "worktrees" / "team" / "demo-alice", branch)
    assert not safe and "已被外部删除" in reason and "丢弃分支" in reason
    assert "WinError" not in reason and "目录名称无效" not in reason


def test_safe_to_cleanup_allows_deleted_worktree_when_branch_merged(tmp_path: Path) -> None:
    root = repository(tmp_path)
    branch = "yucode/worktree/team/demo-bob"
    git(root, "branch", branch)
    safe, reason = TeamMergeService(root).safe_to_cleanup(root / ".yucode" / "worktrees" / "team" / "demo-bob", branch)
    assert safe and "已合并" in reason
