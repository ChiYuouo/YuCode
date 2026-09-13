"""用户级目录解析。

用户级数据统一放在 ``~/.yucode``，让 Windows、macOS 与 Linux 行为一致。
早期版本在 Windows 上使用 ``%APPDATA%\\YuCode``，本模块把它保留为只读回退：
新位置缺失某项时仍会从旧位置读取，避免升级后已有配置与能力包突然失效。
"""

from __future__ import annotations

import os
from pathlib import Path

USER_DIRECTORY = ".yucode"
LEGACY_DIRECTORY = "YuCode"
LEGACY_READ_PATHS = ("yucode.yaml", "skills")


def user_root(home: Path | None = None) -> Path:
    """规范的用户级根目录 ``~/.yucode``。"""
    return (home or Path.home()) / USER_DIRECTORY


def legacy_root() -> Path | None:
    """旧版用户级根目录；仅在设置了 APPDATA 的平台上返回路径。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / LEGACY_DIRECTORY


def resolve_path(relative: str, home: Path | None = None) -> Path:
    """解析一个用户级路径：新位置优先，缺失时回退到旧位置。

    调用方只负责读取语义，因此"两处都不存在"时返回新位置，由调用方按不存在处理。
    """
    current = user_root(home) / relative
    if current.exists():
        return current
    legacy = legacy_root()
    if legacy is not None:
        candidate = legacy / relative
        if candidate.exists():
            return candidate
    return current


def migration_warnings() -> tuple[str, ...]:
    """旧目录中仍被读取的内容存在时，给出一次中文迁移提示。

    只检查 ``LEGACY_READ_PATHS``：这些是 YuCode 架构上确实会从旧位置读取的路径。
    旧目录里的其他残留（例如早期开发留下的目录）不会被读取，因此不触发提示，
    否则提示会因为无关文件而永远消不掉。
    """
    legacy = legacy_root()
    if legacy is None:
        return ()
    pending = [name for name in LEGACY_READ_PATHS if (legacy / name).exists()]
    if not pending:
        return ()
    return (
        f"检测到旧版用户级目录：{legacy}（其中仍有 {'、'.join(pending)}）。"
        f"用户级数据已统一到 {user_root()}，建议把这些内容移动到新位置；"
        f"新位置缺失时仍会读取旧目录。",
    )
