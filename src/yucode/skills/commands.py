"""Skill 激活状态驱动的动态斜杠命令目录。"""

from __future__ import annotations

from yucode.commands.models import CommandContext, CommandDefinition, CommandKind
from yucode.skills.runtime import SkillRuntime
from yucode.skills.models import SkillMode
from yucode.skills.execution import run_fork


class SkillCommandCatalog:
    def __init__(self, base, runtime: SkillRuntime) -> None:
        self._base, self._runtime = base, runtime

    def get(self, name: str):
        normalized = name.lstrip("/").lower()
        found = self._base.get(normalized)
        if found is not None:
            return found
        if not normalized.startswith("skill:"):
            return None
        skill_name = normalized.removeprefix("skill:")
        active = next((item for item in self._runtime.active_items() if item.definition.name == skill_name), None)
        definition = active.definition if active is not None else self._runtime.catalog.get(skill_name)
        return self._definition(definition) if definition is not None else None

    def visible(self):
        return (*self._base.visible(), *(self._definition(item) for item in self._runtime.catalog.definitions.values()))

    def complete(self, prefix: str):
        normalized = prefix.lower()
        return tuple(item for item in self.visible() if item.name.lower().startswith(normalized) or any(alias.lower().startswith(normalized) for alias in item.aliases))

    def _definition(self, definition):
        async def execute(context: CommandContext, arguments: str) -> None:
            current = self._runtime.load(definition.name, arguments)
            if current.definition.mode is SkillMode.FORK:
                await context.ui.show_message(f"正在独立执行 Skill「{current.definition.name}」…")
                approve = getattr(context.ui, "request_skill_permission", None)
                result = await run_fork(context.agent, current, arguments, approve)
                context.agent.conversation.append_assistant(result.summary)
                render_summary = getattr(context.ui, "show_skill_summary", None)
                if render_summary is not None:
                    await render_summary(result.summary)
                else:
                    await context.ui.show_message(result.summary)
                return
            message = (
                f"用户已通过 /skill:{current.definition.name} 命令装载 inline Skill「{current.definition.name}」，"
                f"其完整指令如下，请严格按指令执行，不要再次调用 LoadSkill：\n\n"
                f"===== Skill「{current.definition.name}」指令开始 =====\n"
                f"{current.rendered_sop}\n"
                f"===== Skill 指令结束 =====\n\n"
                f"用户参数：{arguments if arguments else '（无）'}"
            )
            await context.ui.send_user_message(message)
        name = f"skill:{definition.name}"
        return CommandDefinition(name, (), definition.description, f"/{name} [参数]", CommandKind.PROMPT, execute, "可选参数")
