"""团队身份和持久化路径的安全边界。"""

from __future__ import annotations

import os
from pathlib import Path
import re


class TeamIdentityError(ValueError):
    """不可信团队身份或路径被拒绝。"""


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_name(value: str, label: str = "名称") -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise TeamIdentityError(f"{label}只能包含字母、数字、下划线和连字符，长度为 1 到 64。")
    return value


def team_storage_root(workspace_root: Path) -> Path:
    """返回当前项目专属的 Team 持久化根目录。"""
    return _resolved(workspace_root) / ".yucode" / "teams"


def resolve_team_root(storage_root: Path, team_name: str) -> Path:
    name = validate_name(team_name, "团队名称")
    root = _resolved(storage_root)
    candidate = _resolved(root / name)
    if not _is_within(candidate, root):
        raise TeamIdentityError("团队目录必须位于受控团队根目录内。")
    return candidate


def resolve_member_root(team_root: Path, member_name: str) -> Path:
    name = validate_name(member_name, "成员名称")
    root = _resolved(team_root)
    candidate = _resolved(root / "members" / name)
    if not _is_within(candidate, root):
        raise TeamIdentityError("成员目录必须位于受控团队目录内。")
    return candidate


def _resolved(path: Path) -> Path:
    value = str(path.resolve(strict=False))
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(candidate)), os.path.normcase(str(root)))) == os.path.normcase(str(root))
    except ValueError:
        return False
