"""新 Worktree 的安全环境初始化。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from yucode.config import WorktreeConfig


class WorktreeSetupError(ValueError):
    pass


class WorktreeInitializer:
    def __init__(self, repository_root: Path, config: WorktreeConfig) -> None:
        self._root = repository_root.resolve(); self._config = config

    def initialize(self, target: Path) -> None:
        target = target.resolve()
        for relative in self._config.worktreeinclude:
            source, destination = self._pair(target, relative)
            if not source.exists():
                continue
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
        self._configure_hooks(target)
        for relative in self._config.symlink_directories:
            source, destination = self._pair(target, relative)
            if not source.exists():
                continue
            if destination.exists() or destination.is_symlink():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(source, destination, target_is_directory=source.is_dir())
            except OSError as error:
                raise WorktreeSetupError(f"无法链接依赖目录 {relative}：{error}") from error

    def _configure_hooks(self, target: Path) -> None:
        source = self._root / ".git" / "hooks"
        if not source.is_dir():
            return
        try:
            result = subprocess.run(["git", "-C", str(target), "config", "core.hooksPath", str(source)], text=True, encoding="utf-8", capture_output=True, check=False)
        except OSError as error:
            raise WorktreeSetupError(f"无法配置 Git hooks：{error}") from error
        if result.returncode != 0:
            raise WorktreeSetupError(f"无法配置 Git hooks：{(result.stderr or result.stdout).strip()}")

    def _pair(self, target: Path, relative: str) -> tuple[Path, Path]:
        source = (self._root / relative).resolve(strict=False); destination = (target / relative).resolve(strict=False)
        try:
            source.relative_to(self._root); destination.relative_to(target)
        except ValueError as error:
            raise WorktreeSetupError(f"初始化路径超出受控范围：{relative}") from error
        return source, destination
