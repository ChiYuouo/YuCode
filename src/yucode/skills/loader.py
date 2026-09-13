"""从项目、用户和内置目录发现 Skill。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from yucode import userdirs
from yucode.skills.models import (
    HistoryScope,
    SkillCatalog,
    SkillDefinition,
    SkillDiagnostic,
    SkillMode,
    SkillSource,
    SkillToolManifest,
    validate_skill_name,
)
from yucode.tools.base import ToolSafety


class SkillLoader:
    """按 builtin → user → project 优先级发现 Skill。"""

    def __init__(
        self,
        project_root: Path,
        user_root: Path | None = None,
        builtin_root: Path | None = None,
    ) -> None:
        self._project_root = (project_root / ".yucode" / "skills").resolve()
        self._user_root = (user_root or userdirs.resolve_path("skills")).resolve()
        self._builtin_root = (builtin_root or Path(__file__).with_name("builtin")).resolve()

    @property
    def roots(self) -> tuple[tuple[str, Path], ...]:
        return (("builtin", self._builtin_root), ("user", self._user_root), ("project", self._project_root))

    @property
    def user_root(self) -> Path:
        return self._user_root

    def discover(self) -> SkillCatalog:
        merged: dict[str, SkillDefinition] = {}
        diagnostics: list[SkillDiagnostic] = []
        for tier, root in self.roots:
            found, issues = self._discover_tier(tier, root)
            diagnostics.extend(issues)
            merged.update(found)
        return SkillCatalog(dict(merged), tuple(diagnostics))

    def _discover_tier(self, tier: str, root: Path) -> tuple[dict[str, SkillDefinition], list[SkillDiagnostic]]:
        if not root.is_dir():
            return {}, []
        definitions: dict[str, SkillDefinition] = {}
        diagnostics: list[SkillDiagnostic] = []
        entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
        for entry in entries:
            if entry.is_file() and entry.suffix.lower() == ".md":
                definition, diagnostic = self._parse_entry(tier, entry, entry.parent, False)
            elif entry.is_dir() and (entry / "SKILL.md").is_file():
                definition, diagnostic = self._parse_entry(tier, entry / "SKILL.md", entry, True)
            else:
                continue
            if diagnostic is not None:
                diagnostics.append(diagnostic)
                continue
            assert definition is not None
            if definition.name in definitions:
                diagnostics.append(SkillDiagnostic(f"已跳过重复的 Skill 名称：{definition.name}。", entry))
                continue
            definitions[definition.name] = definition
        return definitions, diagnostics

    def _parse_entry(
        self, tier: str, entry: Path, package_root: Path, is_package: bool
    ) -> tuple[SkillDefinition | None, SkillDiagnostic | None]:
        try:
            self._ensure_inside(entry, package_root)
            text = entry.read_text(encoding="utf-8")
            metadata, body = _split_frontmatter(text)
            if is_package:
                extra = package_root / "prompt.md"
                if extra.exists():
                    self._ensure_inside(extra, package_root)
                    body = f"{body.rstrip()}\n\n{extra.read_text(encoding='utf-8').strip()}".strip()
            definition = self._definition(tier, entry, package_root, metadata, body, is_package)
            return definition, None
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
            return None, SkillDiagnostic(f"已跳过无法解析的 Skill：{error}", entry)

    def _definition(
        self,
        tier: str,
        entry: Path,
        package_root: Path,
        metadata: Mapping[str, Any],
        body: str,
        is_package: bool,
    ) -> SkillDefinition:
        name = validate_skill_name(metadata.get("name"))
        description = _required_text(metadata, "description")
        tools = _string_set(metadata.get("allowedTools"), "allowedTools")
        mode_value = metadata.get("mode")
        try:
            mode = SkillMode(mode_value)
        except (TypeError, ValueError) as error:
            raise ValueError("mode 只能是 inline 或 fork。") from error
        history = _history_scope(metadata.get("history", "none"))
        model = metadata.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("model 必须是非空字符串。")
        if not body.strip():
            raise ValueError("Skill 正文不能为空。")
        manifests = self._tool_manifests(package_root) if is_package else ()
        resources = package_root / "references" if is_package and (package_root / "references").is_dir() else None
        if resources is not None:
            self._ensure_inside(resources, package_root)
        source = SkillSource(tier, package_root.resolve(), entry.resolve(), _fingerprint(package_root if is_package else entry))
        return SkillDefinition(name, description, tools, mode, history, model.strip() if isinstance(model, str) else None, body.strip(), source, manifests, resources)

    def _tool_manifests(self, package_root: Path) -> tuple[SkillToolManifest, ...]:
        path = package_root / "tool.json"
        if not path.exists():
            return ()
        self._ensure_inside(path, package_root)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ValueError(f"tool.json 不是有效 JSON/YAML：{error}") from error
        values: Iterable[Any]
        if isinstance(raw, list):
            values = raw
        elif isinstance(raw, Mapping) and isinstance(raw.get("tools"), list):
            values = raw["tools"]
        elif isinstance(raw, Mapping):
            values = (raw,)
        else:
            raise ValueError("tool.json 必须是工具对象或工具对象列表。")
        tools: list[SkillToolManifest] = []
        names: set[str] = set()
        for raw_tool in values:
            if not isinstance(raw_tool, Mapping):
                raise ValueError("tool.json 的每个工具必须是对象。")
            tool_name = validate_skill_name(raw_tool.get("name"))
            if tool_name in names:
                raise ValueError(f"tool.json 中工具名称重复：{tool_name}。")
            names.add(tool_name)
            description = _required_text(raw_tool, "description")
            schema = raw_tool.get("input_schema")
            if not isinstance(schema, Mapping):
                raise ValueError(f"工具 {tool_name} 的 input_schema 必须是对象。")
            try:
                safety = ToolSafety(raw_tool.get("safety"))
            except (TypeError, ValueError) as error:
                raise ValueError(f"工具 {tool_name} 的 safety 必须是 read_only 或 side_effect。") from error
            command = _string_tuple(raw_tool.get("command"), f"工具 {tool_name} 的 command")
            scripts = [value for value in command if value.startswith(".") or Path(value).suffix.lower() in {".py", ".ps1", ".sh", ".bat", ".cmd"}]
            if not scripts:
                raise ValueError(f"工具 {tool_name} 的 command 必须引用包内实现脚本。")
            for script in scripts:
                script_path = (package_root / script).resolve()
                self._ensure_inside(script_path, package_root)
                if not script_path.is_file():
                    raise ValueError(f"工具 {tool_name} 的实现脚本不存在：{script}。")
            tools.append(SkillToolManifest(tool_name, description, dict(schema), safety, command, package_root.resolve()))
        return tuple(tools)

    @staticmethod
    def _ensure_inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("能力包文件不能越出包目录。") from error


def _split_frontmatter(text: str) -> tuple[Mapping[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("Skill 必须以 YAML frontmatter 开头。")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Skill frontmatter 起始标记无效。")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = yaml.safe_load("".join(lines[1:index]))
            if not isinstance(raw, Mapping):
                raise ValueError("Skill frontmatter 必须是键值对象。")
            return raw, "".join(lines[index + 1:])
    raise ValueError("Skill frontmatter 缺少结束标记。")


def _required_text(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少或无效字段：{field}。")
    return value.strip()


def _string_set(value: Any, field: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field} 必须是非空字符串列表。")
    items = frozenset(item.strip() for item in value)
    if len(items) != len(value):
        raise ValueError(f"{field} 不能包含重复工具名。")
    return items


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} 必须是非空字符串列表。")
    return tuple(value)


def _history_scope(value: Any) -> HistoryScope:
    if value == "none":
        return HistoryScope.none()
    if value == "all":
        return HistoryScope.all()
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return HistoryScope.recent(value)
    raise ValueError("history 只能是 none、all 或正整数。")


def _fingerprint(path: Path) -> str:
    digest = sha256()
    if path.is_file():
        paths = (path,)
        root = path.parent
    else:
        root = path
        paths = tuple(sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()))
    for item in paths:
        digest.update(item.relative_to(root).as_posix().encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()
