"""Worktree 创建、切换、保护删除和清理。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from yucode.config import WorktreeConfig
from yucode.worktrees.git import GitWorktreeClient, GitWorktreeError
from yucode.worktrees.models import RemovalResult, WorktreeRecord, WorktreeState
from yucode.worktrees.session import WorktreeSessionStore
from yucode.worktrees.setup import WorktreeInitializer, WorktreeSetupError
from yucode.worktrees.slug import parse_worktree_slug, resolve_worktree_path, worktree_root


class WorktreeManager:
    def __init__(self, repository_root: Path, config: WorktreeConfig | None = None) -> None:
        self._root = repository_root.resolve(); self._config = config or WorktreeConfig()
        self._store = WorktreeSessionStore(self._root); loaded = self._store.load()
        self._records = {item.slug: item for item in loaded.records}; self._active_slug = loaded.active_slug
        self._warnings = list(loaded.warnings); self._git = GitWorktreeClient(self._root, self._config.scaffolding_paths); self._setup = WorktreeInitializer(self._root, self._config)

    @property
    def warnings(self) -> tuple[str, ...]: return tuple(self._warnings)
    @property
    def scaffolding_paths(self) -> tuple[str, ...]:
        """初始化器在 Worktree 内创建、不应算作使用方改动的路径。"""
        return self._config.scaffolding_paths
    @property
    def repository_root(self) -> Path: return self._root
    def current_root(self) -> Path: return self._records[self._active_slug].path if self._active_slug in self._records else self._root

    def create(self, slug_value: str, *, temporary: bool = False) -> WorktreeRecord:
        slug = parse_worktree_slug(slug_value); path = resolve_worktree_path(self._root, slug)
        existing = self._records.get(slug.value)
        if path.exists():
            if existing is None or existing.path != path or path not in self._git.paths():
                raise ValueError("目标目录已存在，但不是可恢复的已登记 Worktree。")
            return existing
        if existing is not None:
            raise ValueError("Worktree 记录存在但目录缺失，请先移除无效记录。")
        branch = f"yucode/worktree/{slug.value}"
        now = datetime.now(UTC)
        base = self._git.create(path, branch)
        record = WorktreeRecord(slug.value, path, branch, base, temporary, WorktreeState.INITIALIZING, now, now)
        self._records[record.slug] = record; self._save()
        try:
            self._setup.initialize(path)
        except WorktreeSetupError as error:
            record = replace(record, state=WorktreeState.FAILED, initialization_error=str(error))
            self._records[record.slug] = record; self._save(); raise
        record = replace(record, state=WorktreeState.READY)
        self._records[record.slug] = record; self._save(); return record

    def enter(self, slug_value: str) -> WorktreeRecord:
        slug = parse_worktree_slug(slug_value); record = self._records.get(slug.value)
        if record is None or record.state is not WorktreeState.READY or not record.path.is_dir():
            raise ValueError("找不到可进入的 Worktree。")
        self._active_slug = record.slug; self._records[record.slug] = replace(record, last_used_at=datetime.now(UTC)); self._save()
        return self._records[record.slug]

    def exit(self) -> None:
        self._active_slug = None; self._save()

    def list(self) -> tuple[WorktreeRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: item.last_used_at, reverse=True))

    def remove(self, slug_value: str, *, force: bool = False, automatic: bool = False, delete_branch: bool = False) -> RemovalResult:
        slug = parse_worktree_slug(slug_value); record = self._records.get(slug.value)
        if record is None: return RemovalResult(False, "找不到指定 Worktree。")
        expected = resolve_worktree_path(self._root, slug)
        if record.path != expected: return RemovalResult(False, "Worktree 登记信息与 Git 状态不一致，已拒绝删除。")
        if not record.path.exists():
            return self._remove_orphan_record(record, force=force, automatic=automatic, delete_branch=delete_branch)
        if record.path not in self._git.paths(): return RemovalResult(False, "Worktree 登记信息与 Git 状态不一致，已拒绝删除。")
        if automatic and (force or not record.temporary): return RemovalResult(False, "自动清理只允许处理临时 Worktree。")
        if delete_branch and not force:
            return RemovalResult(False, "删除分支必须同时明确使用 --force。")
        protection = self._git.inspect_changes(record)
        if protection.protected and not force:
            reasons = []
            if protection.has_uncommitted_changes: reasons.append("存在未提交修改")
            if protection.has_unpushed_commits: reasons.append("存在未推送提交")
            if protection.inspection_error: reasons.append(protection.inspection_error)
            return RemovalResult(False, "；".join(reasons) + "，已拒绝删除。")
        if automatic and protection.protected: return RemovalResult(False, "自动清理无法确认目录安全，已跳过。")
        if self._active_slug == record.slug: self._active_slug = None
        # 上面的变更检查比 Git 自身的 untracked 检查更严格：能走到这里就说明目录里没有
        # 未提交修改也未推送提交，只剩下初始化脚手架等无害内容，因此强制移除是安全的。
        self._git.remove(record.path, force=True)
        del self._records[record.slug]; self._save()
        self._remove_empty_parents(record.path)
        if not delete_branch:
            return RemovalResult(True, f"已移除 Worktree；分支已保留：{record.branch}。")
        try:
            self._git.delete_branch(record.branch, force=True)
        except GitWorktreeError as error:
            return RemovalResult(True, f"已移除 Worktree；分支删除失败，已保留：{record.branch}。原因：{error}")
        return RemovalResult(True, f"已移除 Worktree；分支已删除：{record.branch}。")

    def _remove_orphan_record(self, record: WorktreeRecord, *, force: bool, automatic: bool, delete_branch: bool) -> RemovalResult:
        """目录已被外部删除时，只清理失效的登记，不再删除任何文件。

        仅当目录确实不在磁盘上时才进入这里；目录仍在但与 Git 不一致的情况由 ``remove``
        的常规路径按失败关闭拒绝。Git 侧仍登记该路径时一并撤销该失效登记，使
        ``/worktree list`` 与 ``git worktree list`` 保持一致。分支默认保留。
        """
        if automatic and not record.temporary: return RemovalResult(False, "自动清理只允许处理临时 Worktree。")
        if delete_branch and not force: return RemovalResult(False, "删除分支必须同时明确使用 --force。")
        timeline: list[str] = []
        if record.path in self._git.paths():
            try:
                self._git.remove(record.path, force=True)
            except GitWorktreeError as error:
                return RemovalResult(False, f"无法撤销失效的 Git Worktree 登记：{error}")
            timeline.append("已撤销 Git 中该 Worktree 的失效登记")
        if self._active_slug == record.slug: self._active_slug = None
        del self._records[record.slug]; self._save()
        self._remove_empty_parents(record.path)
        detail = "；".join(("目录已不存在", *timeline))
        if not delete_branch:
            return RemovalResult(True, f"已清理失效的 Worktree 记录（{detail}）；分支已保留：{record.branch}。")
        try:
            self._git.delete_branch(record.branch, force=True)
        except GitWorktreeError as error:
            return RemovalResult(True, f"已清理失效的 Worktree 记录（{detail}）；分支删除失败，已保留：{record.branch}。原因：{error}")
        return RemovalResult(True, f"已清理失效的 Worktree 记录（{detail}）；分支已删除：{record.branch}。")

    def resume(self) -> Path:
        if self._active_slug is None: return self._root
        record = self._records.get(self._active_slug)
        if record is None or record.state is not WorktreeState.READY or not record.path.is_dir() or record.path not in self._git.paths():
            self._warnings.append("保存的 Worktree 已失效，已回到主目录。")
            self._active_slug = None; self._save(); return self._root
        return record.path

    def cleanup_stale(self) -> tuple[RemovalResult, ...]:
        now = datetime.now(UTC); results: list[RemovalResult] = []
        for record in tuple(self._records.values()):
            if record.temporary and now - record.last_used_at >= self._config.cleanup_after:
                results.append(self.remove(record.slug, automatic=True))
        return tuple(results)

    def _save(self) -> None:
        self._store.save(self._active_slug, tuple(self._records.values()))

    def _remove_empty_parents(self, removed_path: Path) -> None:
        """只清理受控根内的空嵌套目录，永不移除根目录或 session.json。"""
        root = worktree_root(self._root).resolve()
        current = removed_path.parent.resolve(strict=False)
        while current != root:
            try:
                if not current.is_dir() or any(current.iterdir()):
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent
