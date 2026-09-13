"""不可信 Worktree 名称的解析与目录边界。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_LENGTH = 160


class WorktreeSlugError(ValueError):
    pass


@dataclass(frozen=True)
class WorktreeSlug:
    value: str
    parts: tuple[str, ...]


def parse_worktree_slug(value: str) -> WorktreeSlug:
    if not isinstance(value, str) or not value or len(value) > _MAX_LENGTH:
        raise WorktreeSlugError("工作目录名称不能为空且长度不能超过 160。")
    if "\\" in value or value.startswith("/") or ":" in value:
        raise WorktreeSlugError("工作目录名称只能使用受控的 / 嵌套路径。")
    parts = tuple(value.split("/"))
    if not parts or any(part in {"", ".", ".."} or not _PART.fullmatch(part) for part in parts):
        raise WorktreeSlugError("工作目录名称只能包含字母、数字、-、_ 和受控的 /。")
    return WorktreeSlug(value, parts)


def worktree_root(repository_root: Path) -> Path:
    return repository_root.resolve() / ".yucode" / "worktrees"


def resolve_worktree_path(repository_root: Path, slug: WorktreeSlug) -> Path:
    root = worktree_root(repository_root).resolve()
    candidate = (root.joinpath(*slug.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise WorktreeSlugError("工作目录路径超出 .yucode/worktrees 范围。") from error
    return candidate
