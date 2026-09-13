"""仅接受已验证路径的 Git Worktree 调用。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from yucode.worktrees.models import ChangeProtection, WorktreeRecord


class GitWorktreeError(ValueError):
    pass


class GitWorktreeClient:
    def __init__(self, repository_root: Path, scaffolding: tuple[str, ...] = ()) -> None:
        self._root = repository_root.resolve(); self._scaffolding = scaffolding

    def ensure_repository(self) -> None:
        result = self._run("rev-parse", "--is-inside-work-tree")
        if result.strip().lower() != "true":
            raise GitWorktreeError("当前目录不是 Git 仓库。")

    def create(self, path: Path, branch: str) -> str:
        self.ensure_repository()
        base = self._run("rev-parse", "HEAD")
        self._run("worktree", "add", "-b", branch, str(path), "HEAD")
        return base

    def paths(self) -> set[Path]:
        self.ensure_repository()
        output = self._run("worktree", "list", "--porcelain")
        return {Path(line[len("worktree "):]).resolve(strict=False) for line in output.splitlines() if line.startswith("worktree ")}

    def status(self, root: Path) -> str:
        """返回工作区状态，不把 Worktree 初始化产生的脚手架算作改动。"""
        return self._run_at(root, "status", "--porcelain", *self.status_exclusions()).strip()

    def status_exclusions(self) -> tuple[str, ...]:
        """供 `git status` 与 `git add` 使用的 pathspec 排除项。"""
        if not self._scaffolding:
            return ()
        return ("--", ".", *(f":(exclude){item}" for item in self._scaffolding))

    def inspect_changes(self, record: WorktreeRecord) -> ChangeProtection:
        try:
            status = self.status(record.path)
            dirty = bool(status.strip())
            try:
                upstream = self._run_at(record.path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
            except GitWorktreeError:
                ahead = self._run_at(record.path, "rev-list", "--count", f"{record.base_commit}..HEAD")
            else:
                ahead = self._run_at(record.path, "rev-list", "--count", f"{upstream}..HEAD")
            return ChangeProtection(dirty, int(ahead.strip() or "0") > 0)
        except (GitWorktreeError, ValueError) as error:
            return ChangeProtection(inspection_error=f"无法安全检查 Worktree 变更：{error}")

    def remove(self, path: Path, *, force: bool) -> None:
        arguments = ["worktree", "remove"]
        if force: arguments.append("--force")
        arguments.append(str(path))
        self._run(*arguments)

    def delete_branch(self, branch: str, *, force: bool) -> None:
        """删除已由调用方明确确认要丢弃的本地分支。"""
        self._run("branch", "-D" if force else "-d", branch)

    def _run(self, *args: str) -> str:
        return self._execute(self._root, *args)

    def _run_at(self, root: Path, *args: str) -> str:
        return self._execute(root, *args)

    @staticmethod
    def _execute(cwd: Path, *args: str) -> str:
        try:
            completed = subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8", capture_output=True, check=False)
        except OSError as error:
            raise GitWorktreeError(f"无法启动 Git：{error}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip() or "未知 Git 错误"
            raise GitWorktreeError(detail)
        return completed.stdout.strip()
