"""Worktree 会话的安全持久化。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from yucode.worktrees.models import WorktreeRecord, WorktreeState
from yucode.worktrees.slug import parse_worktree_slug, resolve_worktree_path, worktree_root

_VERSION = 1


@dataclass(frozen=True)
class WorktreeSession:
    active_slug: str | None = None
    records: tuple[WorktreeRecord, ...] = ()
    warnings: tuple[str, ...] = ()


class WorktreeSessionStore:
    def __init__(self, repository_root: Path) -> None:
        self._root = repository_root.resolve()
        self._path = worktree_root(self._root) / "session.json"

    def load(self) -> WorktreeSession:
        if not self._path.is_file():
            return WorktreeSession()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return WorktreeSession(warnings=(f"无法读取 Worktree 会话：{error}",))
        if not isinstance(raw, dict) or raw.get("version") != _VERSION:
            return WorktreeSession(warnings=("Worktree 会话格式无效，已忽略。",))
        records: list[WorktreeRecord] = []; warnings: list[str] = []
        for item in raw.get("records", []):
            try:
                record = self._decode(item)
            except (TypeError, ValueError) as error:
                warnings.append(f"已忽略无效 Worktree 记录：{error}")
            else:
                records.append(record)
        active = raw.get("active_slug")
        if active is not None and not any(item.slug == active for item in records):
            warnings.append("当前 Worktree 记录无效，已回到主目录。")
            active = None
        return WorktreeSession(active, tuple(records), tuple(warnings))

    def save(self, active_slug: str | None, records: tuple[WorktreeRecord, ...]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        raw = {"version": _VERSION, "active_slug": active_slug, "records": [self._encode(item) for item in records]}
        descriptor, temporary = tempfile.mkstemp(prefix=".session.", suffix=".tmp", dir=self._path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(raw, handle, ensure_ascii=False, separators=(",", ":")); handle.flush()
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True); raise

    def _decode(self, raw: object) -> WorktreeRecord:
        if not isinstance(raw, dict): raise ValueError("记录必须是对象")
        slug = parse_worktree_slug(raw["slug"])
        path = resolve_worktree_path(self._root, slug)
        if Path(raw["path"]).resolve(strict=False) != path: raise ValueError("路径与名称不一致")
        return WorktreeRecord(slug.value, path, str(raw["branch"]), str(raw["base_commit"]), bool(raw["temporary"]), WorktreeState(raw["state"]), datetime.fromisoformat(raw["created_at"]), datetime.fromisoformat(raw["last_used_at"]), raw.get("initialization_error"))

    @staticmethod
    def _encode(item: WorktreeRecord) -> dict[str, object]:
        return {"slug": item.slug, "path": str(item.path), "branch": item.branch, "base_commit": item.base_commit, "temporary": item.temporary, "state": item.state.value, "created_at": item.created_at.isoformat(), "last_used_at": item.last_used_at.isoformat(), "initialization_error": item.initialization_error}
