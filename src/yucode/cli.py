"""YuCode 的终端交互入口。"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from yucode.agent import Agent
from yucode.commands import build_builtin_registry
from yucode.commands.models import CommandRegistrationError
from yucode.context import ContextManager
from yucode.config import ConfigError, ProviderConfig, load_config
from yucode.conversation import Conversation
from yucode.instructions import InstructionLoader
from yucode.memory import MemoryManager
from yucode.mcp.manager import MCPManager
from yucode.permissions import PermissionManager
from yucode.providers.anthropic import AnthropicProvider
from yucode.providers.base import Provider
from yucode.providers.openai import OpenAIProvider
from yucode.prompting import SystemPromptBuilder
from yucode.sessions import SessionManager
from yucode.skills.loader import SkillLoader
from yucode.skills.runtime import SkillRuntime
from yucode.skills.commands import SkillCommandCatalog
from yucode.tools.registry import ToolRegistry
from yucode.tui.app import ChatApp


def main() -> None:
    """从当前目录读取配置后启动交互会话。"""
    console = Console()
    try:
        command_registry = build_builtin_registry()
    except CommandRegistrationError as error:
        console.print(f"命令配置错误：{error}", style="red")
        raise SystemExit(1) from error
    try:
        config = load_config()
    except ConfigError as error:
        console.print(f"配置错误：{error}", style="red")
        return

    workspace_root = Path.cwd()
    registry = ToolRegistry(workspace_root)
    skills = SkillRuntime(SkillLoader(workspace_root), registry)
    permissions = PermissionManager(registry.context.root, config.permissions.mode)
    provider = create_provider(config.provider)
    loaded_instructions = InstructionLoader().load(workspace_root)
    memory = MemoryManager(provider, workspace_root)
    indexes = memory.load_indexes()
    memory_text = "\n\n".join(f"{index.scope.value} 记忆索引：\n{index.content}" for index in indexes)
    prompt_builder = SystemPromptBuilder(loaded_instructions.content, long_term_memory=memory_text)
    sessions = SessionManager(workspace_root)
    sessions.create_session()
    sessions.cleanup_expired()
    conversation = Conversation(sessions.record_event)
    context = ContextManager(conversation, provider, registry.context.root, config.context)
    agent = Agent(
        provider,
        conversation,
        registry,
        config.agent.max_iterations,
        permissions=permissions,
        context_manager=context,
        prompt_builder=prompt_builder,
        memory_manager=memory,
        skill_runtime=skills,
    )
    agent.mcp_manager = MCPManager(config.mcp_servers, config.mcp_issues)
    agent.session_manager = sessions
    agent.command_registry = SkillCommandCatalog(command_registry, skills)
    agent.skill_runtime = skills
    agent.startup_warnings = (*loaded_instructions.warnings, *memory.drain_diagnostics())
    ChatApp(agent, config.provider).run()


def create_provider(config: ProviderConfig) -> Provider:
    """根据配置选择唯一的后端实现。"""
    if config.protocol == "openai":
        return OpenAIProvider(config)
    return AnthropicProvider(config)
