"""子 Agent 的工具收敛策略。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from yucode.subagents.models import AgentDefinition
from yucode.tools.base import ToolCatalog, ToolView


@dataclass(frozen=True)
class ToolPolicy:
    """所有规则都只能移除工具，绝不增加权限。"""

    global_disallowed: frozenset[str] = frozenset()
    background_allowed: frozenset[str] | None = None

    def allowed_names(
        self,
        available: Iterable[str],
        definition: AgentDefinition | None,
        *,
        background: bool,
        allow_delegation: bool = False,
    ) -> frozenset[str]:
        names = set(available)
        names.difference_update(self.global_disallowed)
        if definition is not None:
            if definition.tools is not None:
                names.intersection_update(definition.tools)
            names.difference_update(definition.disallowed_tools)
        if background and self.background_allowed is not None:
            names.intersection_update(self.background_allowed)
        if not allow_delegation:
            names.discard("Agent")
        return frozenset(names)

    def rejection_reason(self, name: str, allowed: frozenset[str]) -> str:
        if name in allowed:
            return ""
        return f"工具 {name} 不在当前子 Agent 的允许范围内，未执行。"


def build_filtered_view(catalog: ToolCatalog, names: Iterable[str]) -> ToolView:
    """从当前目录冻结一个只含许可工具的视图。"""
    selected = {name: catalog.get(name) for name in names}
    return ToolView(catalog.context, {name: tool for name, tool in selected.items() if tool is not None})
