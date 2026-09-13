from datetime import UTC, datetime
from pathlib import Path

from yucode.worktrees.models import WorktreeRecord, WorktreeState
from yucode.worktrees.session import WorktreeSessionStore


def test_session_round_trip(tmp_path: Path) -> None:
    path = tmp_path / ".yucode" / "worktrees" / "demo"
    record = WorktreeRecord("demo", path, "yucode/worktree/demo", "abc", True, WorktreeState.READY, datetime.now(UTC), datetime.now(UTC))
    store = WorktreeSessionStore(tmp_path)
    store.save("demo", (record,))
    loaded = store.load()
    assert loaded.active_slug == "demo"
    assert loaded.records == (record,)


def test_session_ignores_path_outside_controlled_root(tmp_path: Path) -> None:
    store = WorktreeSessionStore(tmp_path)
    path = tmp_path / ".yucode" / "worktrees" / "session.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"version":1,"active_slug":"demo","records":[{"slug":"demo","path":"C:/outside","branch":"x","base_commit":"x","temporary":true,"state":"ready","created_at":"2026-01-01T00:00:00+00:00","last_used_at":"2026-01-01T00:00:00+00:00"}]}', encoding="utf-8")
    loaded = store.load()
    assert not loaded.records
    assert loaded.active_slug is None
