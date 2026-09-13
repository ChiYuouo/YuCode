"""创建运行时状态相互隔离的定义式与 Fork 子 Agent。"""

from __future__ import annotations

from yucode.agent import Agent
from yucode.conversation import Conversation
from yucode.permissions import PermissionManager
from yucode.prompting import SystemPromptBuilder
from yucode.subagents.models import AgentDefinition
from yucode.subagents.policy import ToolPolicy, build_filtered_view
from yucode.tools.base import ToolView
from pathlib import Path


class SubagentFactory:
    def __init__(self, policy: ToolPolicy) -> None:
        self._policy = policy

    def create_definition(self, parent: Agent, definition: AgentDefinition, *, background: bool, workspace_root: Path | None = None, notice: str | None = None) -> Agent:
        names = self._policy.allowed_names((item.name for item in parent._registry.definitions), definition, background=background)
        catalog = parent._registry.view_for(workspace_root) if workspace_root is not None and hasattr(parent._registry, "view_for") else parent._registry
        view = build_filtered_view(catalog, names)
        base = parent._prompt_builder
        custom = getattr(base, "_custom_instructions", "")
        # 定义式子 Agent 只继承项目共同规则与自身角色，不继承主 Agent 的长期记忆。
        prompt = SystemPromptBuilder(f"{custom}\n\n{definition.prompt}".strip())
        return Agent(parent._provider, Conversation(), view, definition.max_iterations, prompt_builder=prompt,
                     permissions=PermissionManager(view.context.root, definition.permission_mode), hook_engine=parent._hooks,
                     runtime_notices=(notice,) if notice else ())

    def create_fork(self, parent: Agent, *, background: bool = True) -> Agent:
        names = self._policy.allowed_names((item.name for item in parent._registry.definitions), None, background=background)
        view = build_filtered_view(parent._registry, names)
        conversation = Conversation(); conversation.replace_for_recovery(tuple(parent.conversation.messages))
        return Agent(parent._provider, conversation, view, parent._max_iterations, prompt_builder=parent._prompt_builder,
                     permissions=PermissionManager(view.context.root, parent.permissions.mode), hook_engine=parent._hooks)

    def create_team_member(self, parent: Agent, conversation: Conversation, workspace_root: Path,
                           team_tools: tuple, role: str, *, write_allowed: bool) -> Agent:
        """创建不继承 Lead 协作权限的 Team 成员实例。"""
        catalog = parent._registry.view_for(workspace_root) if hasattr(parent._registry, "view_for") else parent._registry
        tools = {item.name: catalog.get(item.name) for item in catalog.definitions}
        tools.pop("Agent", None)
        if not write_allowed:
            for name in ("write_file", "edit_file", "run_command"):
                tools.pop(name, None)
        tools.update({item.definition.name: item for item in team_tools})
        view = ToolView(catalog.context, {name: item for name, item in tools.items() if item is not None})
        custom = getattr(parent._prompt_builder, "_custom_instructions", "")
        prompt = SystemPromptBuilder(f"{custom}\n\n你的 Team 成员角色：{role}".strip())
        return Agent(parent._provider, conversation, view, parent._max_iterations, prompt_builder=prompt,
                     permissions=PermissionManager(view.context.root), hook_engine=parent._hooks)
