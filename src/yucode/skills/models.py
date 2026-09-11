"""Skill 发现和运行时共享的不可变数据。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any, Literal, Mapping

from yucode.tools.base import ToolSafety


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class SkillMode(str, Enum):
    INLINE = "inline"
    FORK = "fork"


@dataclass(frozen=True)
class HistoryScope:
    """独立 Skill 可以带入的主对话历史范围。"""

    kind: Literal["none", "recent", "all"]
    turns: int = 0

    @classmethod
    def none(cls) -> "HistoryScope":
        return cls("none")

    @classmethod
    def all(cls) -> "HistoryScope":
        return cls("all")

    @classmethod
    def recent(cls, turns: int) -> "HistoryScope":
        if turns <= 0:
            raise ValueError("recent 历史轮数必须是正整数。")
        return cls("recent", turns)


@dataclass(frozen=True)
class SkillSource:
    tier: Literal["project", "user", "builtin"]
    package_root: Path
    entry_path: Path
    fingerprint: str


@dataclass(frozen=True)
class SkillToolManifest:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    safety: ToolSafety
    command: tuple[str, ...]
    package_root: Path


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    allowed_tools: frozenset[str]
    mode: SkillMode
    history_scope: HistoryScope
    model: str | None
    sop: str
    source: SkillSource
    private_tools: tuple[SkillToolManifest, ...] = ()
    resource_root: Path | None = None

    def render(self, arguments: str) -> str:
        """在交给模型前替换唯一的公开参数占位符。"""
        return self.sop.replace("$ARGUMENTS", arguments)


@dataclass(frozen=True)
class ActiveSkill:
    definition: SkillDefinition
    arguments: str = ""

    @property
    def rendered_sop(self) -> str:
        return self.definition.render(self.arguments)


@dataclass(frozen=True)
class SkillSnapshot:
    """一轮请求使用的 Skill 目录和激活定义快照。"""

    catalog: "SkillCatalog"
    active: tuple[ActiveSkill, ...]


@dataclass(frozen=True)
class SkillDiagnostic:
    message: str
    path: Path | None = None
    level: Literal["warning", "error"] = "warning"


@dataclass(frozen=True)
class SkillCatalog:
    definitions: Mapping[str, SkillDefinition] = field(default_factory=dict)
    diagnostics: tuple[SkillDiagnostic, ...] = ()

    def get(self, name: str) -> SkillDefinition | None:
        return self.definitions.get(name.lower())


def validate_skill_name(value: object) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise ValueError("name 必须是以小写字母开头，只包含小写字母、数字、- 或 _ 的名称。")
    return value
