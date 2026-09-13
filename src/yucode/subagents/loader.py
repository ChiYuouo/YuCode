"""从四层目录发现并解析 Markdown Agent 定义。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from yucode.permissions import PermissionMode
from yucode.subagents.models import AgentCatalog, AgentDefinition, AgentDiagnostic, AgentIsolation, AgentModel, AgentSource

_ALLOWED_FIELDS = {"name", "description", "tools", "disallowedTools", "model", "maxIterations", "permissionMode", "isolation"}


class AgentDefinitionLoader:
    """按 plugin → builtin → user → project 顺序加载定义。"""

    def __init__(self, project_root: Path, plugin_roots: Iterable[Path] = (), user_root: Path | None = None, builtin_root: Path | None = None) -> None:
        self._project_root = (project_root / ".yucode" / "agents").resolve()
        self._user_root = (user_root or Path.home() / ".yucode" / "agents").resolve()
        self._builtin_root = (builtin_root or Path(__file__).with_name("builtin")).resolve()
        self._plugin_roots = tuple(root.resolve() for root in plugin_roots)

    @property
    def roots(self) -> tuple[tuple[str, Path], ...]:
        return (
            *(("plugin", path) for path in self._plugin_roots),
            ("builtin", self._builtin_root),
            ("user", self._user_root),
            ("project", self._project_root),
        )

    def discover(self) -> AgentCatalog:
        definitions: dict[str, AgentDefinition] = {}
        diagnostics: list[AgentDiagnostic] = []
        for tier, root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.md"), key=lambda item: item.name.lower()):
                try:
                    definition = self._parse(tier, path)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                    diagnostics.append(AgentDiagnostic(f"已跳过无法解析的 Agent 定义：{error}", path))
                    continue
                definitions[definition.name] = definition
        return AgentCatalog(definitions, tuple(diagnostics))

    def _parse(self, tier: str, path: Path) -> AgentDefinition:
        metadata, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        unknown = set(metadata) - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"包含未知字段：{sorted(unknown)[0]}。")
        name = _text(metadata, "name")
        if not all(char.isalnum() or char in "_-" for char in name):
            raise ValueError("name 只能包含字母、数字、下划线或连字符。")
        description = _text(metadata, "description")
        if not body.strip():
            raise ValueError("正文不能为空。")
        try:
            model = AgentModel(metadata.get("model", "inherit"))
        except (TypeError, ValueError) as error:
            raise ValueError("model 只能是 inherit、haiku、sonnet 或 opus。") from error
        max_iterations = metadata.get("maxIterations", 10)
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("maxIterations 必须是正整数。")
        try:
            permission_mode = PermissionMode(metadata.get("permissionMode", PermissionMode.DEFAULT.value))
        except (TypeError, ValueError) as error:
            choices = "、".join(item.value for item in PermissionMode)
            raise ValueError(f"permissionMode 只能是 {choices}。") from error
        tools = _tool_set(metadata.get("tools"), "tools", optional=True)
        disallowed = _tool_set(metadata.get("disallowedTools", []), "disallowedTools", optional=False)
        try:
            isolation = AgentIsolation(metadata.get("isolation", "none"))
        except (TypeError, ValueError) as error:
            raise ValueError("isolation 只能是 worktree。") from error
        return AgentDefinition(name, description, tools, disallowed, model, max_iterations, permission_mode, body.strip(), AgentSource(tier, path.resolve()), isolation)


def _split_frontmatter(text: str) -> tuple[Mapping[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("必须以 YAML frontmatter 开头。")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = yaml.safe_load("".join(lines[1:index]))
            if not isinstance(raw, Mapping):
                raise ValueError("frontmatter 必须是键值对象。")
            return raw, "".join(lines[index + 1:])
    raise ValueError("frontmatter 缺少结束标记。")


def _text(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少或无效字段：{field}。")
    return value.strip()


def _tool_set(value: Any, field: str, *, optional: bool) -> frozenset[str] | None:
    if value is None and optional:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} 必须是字符串列表。")
    result = frozenset(item.strip() for item in value)
    if len(result) != len(value):
        raise ValueError(f"{field} 不能包含重复工具名。")
    return result
