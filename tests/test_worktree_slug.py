from pathlib import Path

import pytest

from yucode.worktrees.slug import WorktreeSlugError, parse_worktree_slug, resolve_worktree_path


def test_nested_slug_stays_in_yucode_worktree_root(tmp_path: Path) -> None:
    slug = parse_worktree_slug("agent/task_1")
    assert resolve_worktree_path(tmp_path, slug) == tmp_path / ".yucode" / "worktrees" / "agent" / "task_1"


@pytest.mark.parametrize("value", ["", ".", "..", "a//b", "a/../b", "/tmp/x", "a\\b", "a:b", "a space"])
def test_unsafe_slug_is_rejected(value: str) -> None:
    with pytest.raises(WorktreeSlugError):
        parse_worktree_slug(value)
