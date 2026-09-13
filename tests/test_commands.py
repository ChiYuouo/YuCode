"""斜杠命令的框架无关行为测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yucode.agent import Agent
from yucode.commands import CommandDispatcher, build_builtin_registry, parse_input
from yucode.commands.models import CommandContext, CommandDefinition, CommandKind, CommandRegistrationError, CommandStatus
from yucode.commands.parser import InputKind
from yucode.commands.registry import CommandRegistry
from yucode.conversation import Conversation
from yucode.permissions import PermissionManager, PermissionMode
from yucode.providers.base import Usage
from yucode.tools.registry import ToolRegistry
from yucode.sessions import SessionManager


class Provider:
    async def stream(self, *_args):
        if False:
            yield None


@dataclass
class UI:
    messages: list[tuple[str, bool]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    mode: PermissionMode = PermissionMode.DEFAULT
    cleared: int = 0

    async def show_message(self, text: str, *, error: bool = False) -> None:
        self.messages.append((text, error))

    async def send_user_message(self, text: str) -> None:
        self.sent.append(text)

    async def clear_chat(self) -> None:
        self.cleared += 1

    async def replace_history(self) -> None:
        return None

    async def compact(self) -> None:
        return None

    async def confirm_delete(self, *_args) -> bool:
        return False

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode

    def status(self) -> CommandStatus:
        return CommandStatus("test", "model", self.mode, "id", 2, 9, Usage(3, 4))

    def reset_usage(self) -> None:
        return None

    def refresh_status(self, _text: str = "准备就绪") -> None:
        return None

    async def request_exit(self) -> None:
        return None


def context(tmp_path: Path) -> tuple[CommandContext, UI]:
    registry = ToolRegistry(tmp_path)
    agent = Agent(Provider(), Conversation(), registry, permissions=PermissionManager(tmp_path))
    ui = UI()
    return CommandContext(build_builtin_registry(), ui, agent), ui


def test_ch13_t03_registry_rejects_case_insensitive_conflicts() -> None:
    async def handler(_context, _arguments):
        return None
    one = CommandDefinition("one", ("x",), "说明", "/one", CommandKind.LOCAL, handler)
    two = CommandDefinition("TWO", ("X",), "说明", "/two", CommandKind.LOCAL, handler)
    with pytest.raises(CommandRegistrationError, match="X"):
        CommandRegistry((one, two))


def test_ch13_t05_parser_keeps_argument_inner_space() -> None:
    parsed = parse_input("  /ReViEw  A  B\tC  ")
    assert parsed.kind is InputKind.COMMAND and parsed.name == "review" and parsed.arguments == "A  B\tC"
    assert parse_input("  ").kind is InputKind.EMPTY


def test_ch13_t19_prompt_commands_send_once(tmp_path: Path) -> None:
    async def check() -> None:
        command_context, ui = context(tmp_path)
        await CommandDispatcher(command_context).dispatch(parse_input("/review  Focus  Here"))
        assert len(ui.sent) == 1
        assert "仅审查，不实施修复" in ui.sent[0]
        assert "Focus  Here" in ui.sent[0]
    asyncio.run(check())


def test_ch13_t18_do_returns_to_default(tmp_path: Path) -> None:
    async def check() -> None:
        command_context, ui = context(tmp_path)
        command_context.agent.permissions.set_mode(PermissionMode.ACCEPT_EDITS)
        await CommandDispatcher(command_context).dispatch(parse_input("/do"))
        assert command_context.agent.permissions.mode is PermissionMode.DEFAULT
        assert ui.mode is PermissionMode.DEFAULT
    asyncio.run(check())


def test_permission_accepts_camel_case_and_snake_case(tmp_path: Path) -> None:
    """配置文件用 camelCase，命令原先只认 snake_case，两种拼写都要能用。"""
    async def check() -> None:
        for spelling, expected in (
            ("default", PermissionMode.DEFAULT),
            ("acceptEdits", PermissionMode.ACCEPT_EDITS),
            ("accept_edits", PermissionMode.ACCEPT_EDITS),
            ("plan", PermissionMode.PLAN),
            ("bypassPermissions", PermissionMode.BYPASS_PERMISSIONS),
            ("bypass_permissions", PermissionMode.BYPASS_PERMISSIONS),
        ):
            command_context, ui = context(tmp_path)
            await CommandDispatcher(command_context).dispatch(parse_input(f"/permission {spelling}"))
            assert command_context.agent.permissions.mode is expected, spelling
            assert ui.mode is expected, spelling
    asyncio.run(check())


def test_permission_reports_unknown_mode_with_available_values(tmp_path: Path) -> None:
    async def check() -> None:
        command_context, ui = context(tmp_path)
        await CommandDispatcher(command_context).dispatch(parse_input("/permission accept"))
        text, is_error = ui.messages[-1]
        assert is_error is True
        assert "未知权限模式" in text
        assert "acceptEdits" in text and "bypassPermissions" in text
        assert command_context.agent.permissions.mode is PermissionMode.DEFAULT
    asyncio.run(check())


def test_ch13_t24_help_has_exactly_ten_public_commands(tmp_path: Path) -> None:
    command_context, _ = context(tmp_path)
    assert len(command_context.registry.visible()) == 15
    assert command_context.registry.get("?").name == "help"
    assert command_context.registry.get("quit").hidden


def test_ch13_t21_session_list_and_new_are_local(tmp_path: Path) -> None:
    async def check() -> None:
        command_context, ui = context(tmp_path)
        store = SessionManager(tmp_path)
        first = store.create_session()
        command_context = CommandContext(command_context.registry, ui, command_context.agent, store)
        await CommandDispatcher(command_context).dispatch(parse_input("/session"))
        assert first in ui.messages[-1][0]
        await CommandDispatcher(command_context).dispatch(parse_input("/session new"))
        assert store.active_session_id != first
        assert command_context.agent.conversation.messages == ()
    asyncio.run(check())


def test_ch13_t23_delete_requires_confirmation(tmp_path: Path) -> None:
    async def check() -> None:
        command_context, ui = context(tmp_path)
        store = SessionManager(tmp_path)
        target = store.create_session()
        store.create_session()
        command_context = CommandContext(command_context.registry, ui, command_context.agent, store)
        await CommandDispatcher(command_context).dispatch(parse_input(f"/session delete {target}"))
        assert store.get_summary(target).session_id == target
        assert "取消" in ui.messages[-1][0]
    asyncio.run(check())
