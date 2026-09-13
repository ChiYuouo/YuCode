"""Worktree 领域的不可变数据。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class WorktreeState(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class WorktreeRecord:
    slug: str
    path: Path
    branch: str
    base_commit: str
    temporary: bool
    state: WorktreeState
    created_at: datetime
    last_used_at: datetime
    initialization_error: str | None = None


@dataclass(frozen=True)
class ChangeProtection:
    has_uncommitted_changes: bool = False
    has_unpushed_commits: bool = False
    inspection_error: str | None = None

    @property
    def protected(self) -> bool:
        return self.has_uncommitted_changes or self.has_unpushed_commits or self.inspection_error is not None


@dataclass(frozen=True)
class RemovalResult:
    removed: bool
    reason: str

