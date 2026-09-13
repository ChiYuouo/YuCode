"""Skill 激活状态、启动校验和无后台线程的热更新。"""

from __future__ import annotations


from yucode.skills.loader import SkillLoader
from yucode.skills.install import InstallResult, SkillInstaller
from yucode.skills.models import ActiveSkill, SkillCatalog, SkillDiagnostic, SkillSnapshot
from yucode.tools.base import ToolCatalog


class SkillConfigurationError(ValueError):
    """有效 Skill 引用了启动时不存在的工具。"""


class SkillRuntime:
    def __init__(self, loader: SkillLoader, tools: ToolCatalog) -> None:
        self._loader = loader
        self._tools = tools
        self._catalog = SkillCatalog()
        self._active: dict[str, ActiveSkill] = {}
        self._diagnostics: list[SkillDiagnostic] = []
        self._initialized = False

    @property
    def catalog(self) -> SkillCatalog:
        return self._catalog

    def initialize(self) -> tuple[SkillDiagnostic, ...]:
        self._catalog = self._loader.discover()
        self._validate_catalog(self._catalog)
        self._diagnostics.extend(self._catalog.diagnostics)
        self._initialized = True
        return self.drain_diagnostics()

    def refresh(self) -> tuple[SkillDiagnostic, ...]:
        if not self._initialized:
            return self.initialize()
        catalog = self._loader.discover()
        self._catalog = catalog
        self._diagnostics.extend(catalog.diagnostics)
        for name, active in tuple(self._active.items()):
            replacement = catalog.get(name)
            if replacement is not None:
                self._active[name] = ActiveSkill(replacement, active.arguments)
                continue
            if active.definition.source.entry_path.exists():
                self._diagnostics.append(SkillDiagnostic(f"Skill {name} 的新定义无法解析，继续使用最后一次有效定义。", active.definition.source.entry_path))
                continue
            self._active.pop(name)
            self._diagnostics.append(SkillDiagnostic(f"Skill {name} 已移除，已解除激活。"))
        return self.drain_diagnostics()

    def load(self, name: str, arguments: str = "") -> ActiveSkill:
        self.refresh()
        definition = self._catalog.get(name)
        if definition is None:
            raise ValueError(f"找不到可用 Skill：{name}。")
        active = ActiveSkill(definition, arguments)
        self._active[definition.name] = active
        return active

    def clear_active(self) -> None:
        self._active.clear()

    async def install(self, url: str) -> InstallResult:
        result = await SkillInstaller(self._loader.user_root).install(url)
        self.refresh()
        return result

    def active_items(self) -> tuple[ActiveSkill, ...]:
        self.refresh()
        return tuple(self._active.values())

    def snapshot(self) -> SkillSnapshot:
        self.refresh()
        return SkillSnapshot(self._catalog, tuple(self._active.values()))

    def drain_diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        values = tuple(self._diagnostics)
        self._diagnostics.clear()
        return values

    def _validate_catalog(self, catalog: SkillCatalog) -> None:
        global_names = {definition.name for definition in self._tools.definitions}
        for definition in catalog.definitions.values():
            private_names = {tool.name for tool in definition.private_tools}
            duplicate = global_names & private_names
            if duplicate:
                raise SkillConfigurationError(f"Skill {definition.name} 的专属工具与现有工具重名：{sorted(duplicate)[0]}。")
            unknown = definition.allowed_tools - global_names - private_names
            if unknown:
                raise SkillConfigurationError(f"Skill {definition.name} 的 allowedTools 包含不存在的工具：{sorted(unknown)[0]}。")
