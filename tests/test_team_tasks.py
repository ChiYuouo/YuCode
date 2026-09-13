from datetime import UTC, datetime
from pathlib import Path
import pytest

from yucode.teams.models import AgentTeam, TeamBackend, TeamMember, TeamTaskState
from yucode.teams.tasks import TeamTaskError, TeamTaskStore


def team(tmp_path: Path) -> AgentTeam:
    now = datetime.now(UTC)
    root = tmp_path / "demo"; root.mkdir()
    return AgentTeam(1, "demo", "lead", root, (TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS),), now, now)


def test_dependencies_unlock_only_after_completion(tmp_path: Path) -> None:
    store = TeamTaskStore(); value = team(tmp_path)
    first = store.create(value, "研究", "读 README", assignee="alice")
    second = store.create(value, "总结", "形成摘要", dependencies=(first.task_id,))
    assert second.state is TeamTaskState.BLOCKED
    store.update(value, first.task_id, state=TeamTaskState.IN_PROGRESS)
    store.update(value, first.task_id, state=TeamTaskState.COMPLETED)
    assert store.get(value, second.task_id).state is TeamTaskState.READY


def test_rejects_unknown_dependency(tmp_path: Path) -> None:
    with pytest.raises(TeamTaskError, match="不存在"):
        TeamTaskStore().create(team(tmp_path), "坏任务", "", dependencies=("missing",))


def test_reports_blocked_by_blocks_and_rejects_cycle(tmp_path: Path) -> None:
    store = TeamTaskStore(); value = team(tmp_path)
    first = store.create(value, "first", "")
    second = store.create(value, "second", "", dependencies=(first.task_id,))
    assert store.blocks(value, first.task_id) == (second.task_id,)
    with pytest.raises(TeamTaskError, match="循环"):
        store.update(value, first.task_id, dependencies=(second.task_id,))


def test_rejects_self_and_duplicate_dependencies_and_cancel_does_not_unlock(tmp_path: Path) -> None:
    store = TeamTaskStore(); value = team(tmp_path)
    first = store.create(value, "first", "")
    with pytest.raises(TeamTaskError, match="重复"):
        store.create(value, "duplicate", "", dependencies=(first.task_id, first.task_id))
    second = store.create(value, "second", "", dependencies=(first.task_id,))
    with pytest.raises(TeamTaskError, match="自身"):
        store.update(value, second.task_id, dependencies=(second.task_id,))
    store.update(value, first.task_id, state=TeamTaskState.CANCELLED)
    assert store.get(value, second.task_id).state is TeamTaskState.BLOCKED
