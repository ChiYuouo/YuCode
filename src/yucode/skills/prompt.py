"""将 Skill 快照转为模型提示所需的纯数据。"""

from __future__ import annotations

from dataclasses import dataclass

from yucode.skills.models import SkillSnapshot


@dataclass(frozen=True)
class SkillPromptState:
    available: tuple[tuple[str, str], ...]
    active: tuple[tuple[str, str, str], ...]
    suggested_tools: tuple[str, ...]

    @property
    def catalog_text(self) -> str:
        if not self.available:
            return ""
        items = "\n".join(f"- {name}：{description}" for name, description in self.available)
        return f"可用 Skill（需要时调用 LoadSkill 按名称加载完整 SOP）：\n{items}"

    @property
    def active_text(self) -> str:
        if not self.active:
            return ""
        items = "\n\n".join(
            f"<active-skill name=\"{name}\" mode=\"{mode}\">\n{sop}\n</active-skill>"
            for name, mode, sop in self.active
        )
        return f"以下为已激活 Skill，必须优先遵循其完整 SOP：\n{items}"

    @property
    def suggested_tools_text(self) -> str:
        if not self.suggested_tools:
            return ""
        return "建议工具：" + "、".join(self.suggested_tools) + "。只可使用本轮提供的工具。"


def build_skill_prompt_state(snapshot: SkillSnapshot, tool_names: tuple[str, ...]) -> SkillPromptState:
    available = tuple((item.name, item.description) for item in snapshot.catalog.definitions.values())
    active = tuple((item.definition.name, item.definition.mode.value, item.rendered_sop) for item in snapshot.active)
    return SkillPromptState(available, active, tool_names)
