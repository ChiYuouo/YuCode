import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


from yucode.worktrees.git import GitWorktreeClient
from yucode.worktrees.manager import WorktreeManager


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True).stdout


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    _git(root, "init"); _git(root, "config", "user.email", "test@example.com"); _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("base", encoding="utf-8")
    _git(root, "add", "README.md"); _git(root, "commit", "-m", "initial")
    return root


def test_create_enter_exit_and_fast_recovery(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("feature/api")
    assert record.path.is_dir()
    assert manager.create("feature/api") == record
    manager.enter("feature/api")
    assert manager.current_root() == record.path
    manager.exit()
    assert manager.current_root() == root


def test_remove_protects_uncommitted_changes_and_force_removes(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("demo")
    (record.path / "changed.txt").write_text("changed", encoding="utf-8")
    refused = manager.remove("demo")
    assert not refused.removed and record.path.exists()
    result = manager.remove("demo", force=True)
    assert result.removed and "分支已保留" in result.reason
    assert not record.path.exists()
    assert not (root / ".yucode" / "worktrees" / "demo").exists()
    assert (root / ".yucode" / "worktrees" / "session.json").is_file()
    assert "yucode/worktree/demo" in subprocess.run(["git", "branch", "--list", "yucode/worktree/demo"], cwd=root, text=True, capture_output=True, check=True).stdout


def test_force_delete_branch_discards_branch(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("discard/me")
    (record.path / "changed.txt").write_text("changed", encoding="utf-8")
    result = manager.remove("discard/me", force=True, delete_branch=True)
    assert result.removed and "分支已删除" in result.reason
    branches = subprocess.run(["git", "branch", "--list", record.branch], cwd=root, text=True, capture_output=True, check=True).stdout
    assert not branches.strip()


def test_remove_cleans_orphan_record_when_directory_was_deleted(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("team/demo")
    shutil.rmtree(record.path)
    assert [item.slug for item in WorktreeManager(root).list()] == ["team/demo"], "目录缺失时记录仍应可读，等待显式清理"
    result = WorktreeManager(root).remove("team/demo")
    assert result.removed and "已清理失效" in result.reason and "分支已保留" in result.reason
    assert "已撤销 Git" in result.reason, "Git 仍登记失效路径时应一并撤销"
    assert WorktreeManager(root).list() == ()
    assert record.path not in GitWorktreeClient(root).paths()
    assert _git_out(root, "branch", "--list", record.branch).strip()
    assert not (root / ".yucode" / "worktrees" / "team").exists(), "空的嵌套父目录应被清理"
    assert (root / ".yucode" / "worktrees" / "session.json").is_file()


def test_orphan_record_after_git_prune_is_cleaned_without_git_notice(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("demo")
    shutil.rmtree(record.path)
    _git(root, "worktree", "prune")
    result = WorktreeManager(root).remove("demo")
    assert result.removed and "已清理失效" in result.reason and "已撤销 Git" not in result.reason
    assert WorktreeManager(root).list() == ()
    assert _git_out(root, "branch", "--list", record.branch).strip()


def test_orphan_record_requires_force_to_discard_branch(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("demo")
    shutil.rmtree(record.path)
    refused = WorktreeManager(root).remove("demo", delete_branch=True)
    assert not refused.removed and "删除分支" in refused.reason
    assert [item.slug for item in WorktreeManager(root).list()] == ["demo"]
    result = WorktreeManager(root).remove("demo", force=True, delete_branch=True)
    assert result.removed and "分支已删除" in result.reason
    assert not _git_out(root, "branch", "--list", record.branch).strip()
    assert WorktreeManager(root).create("demo").path.is_dir(), "清理记录并丢弃分支后可重建同名 Worktree"


def test_orphan_record_cleanup_respects_temporary_and_automatic_rules(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    manual = manager.create("keep/me")
    shutil.rmtree(manual.path)
    skipped = WorktreeManager(root).remove("keep/me", automatic=True)
    assert not skipped.removed and "临时" in skipped.reason
    temporary = manager.create("tmp/me", temporary=True)
    shutil.rmtree(temporary.path)
    cleaned = WorktreeManager(root).remove("tmp/me", automatic=True)
    assert cleaned.removed
    assert {item.slug for item in WorktreeManager(root).list()} == {"keep/me"}


def test_existing_directory_without_git_registration_is_still_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    path = root / ".yucode" / "worktrees" / "demo"; path.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    (root / ".yucode" / "worktrees" / "session.json").write_text(json.dumps({
        "version": 1, "active_slug": None, "records": [{
            "slug": "demo", "path": str(path), "branch": "yucode/worktree/demo", "base_commit": "0" * 40,
            "temporary": True, "state": "ready", "created_at": now, "last_used_at": now, "initialization_error": None,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    manager = WorktreeManager(root)
    result = manager.remove("demo")
    assert not result.removed and "不一致" in result.reason
    assert path.is_dir() and len(manager.list()) == 1


def test_worktree_scaffolding_is_not_reported_as_uncommitted_changes(tmp_path: Path) -> None:
    root = _repository(tmp_path); manager = WorktreeManager(root)
    record = manager.create("demo")
    dependencies = record.path / "node_modules"; dependencies.mkdir()
    (dependencies / "pkg.js").write_text("x", encoding="utf-8")
    result = WorktreeManager(root).remove("demo")
    assert result.removed, "初始化脚手架不属于使用方改动，不应阻止移除"
    assert not record.path.exists()
