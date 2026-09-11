"""命令机制的框架无关数据结构。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from yucode.permissions import PermissionMode
from yucode.providers.base import Usage

if TYPE_CHECKING:
    from yucode.agent import Agent
    from yucode.commands.registry import CommandRegistry


class CommandKind(str, Enum):
    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"


class CommandUsageError(ValueError):
    """命令参数不符合已登记用法。"""


class CommandRegistrationError(ValueError):
    """命令主名或别名发生冲突。"""


CommandHandler = Callable[["CommandContext", str], Awaitable[None]]


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    handler: CommandHandler
    argument_hint: str | None = None
    hidden: bool = False


@dataclass(frozen=True)
class CommandStatus:
    provider: str
    model: str
    mode: PermissionMode
    session_id: str | None
    message_count: int
    estimated_context_tokens: int
    last_turn_usage: Usage | None


class CommandUI(Protocol):
    async def show_message(self, text: str, *, error: bool = False) -> None: ...
    async def send_user_message(self, text: str) -> None: ...
    async def clear_chat(self) -> None: ...
    async def replace_history(self) -> None: ...
    async def compact(self) -> None: ...
    async def confirm_delete(self, session_id: str, detail: str) -> bool: ...
    def set_mode(self, mode: PermissionMode) -> None: ...
    def status(self) -> CommandStatus: ...
    def reset_usage(self) -> None: ...
    def refresh_status(self, text: str = "准备就绪") -> None: ...
    async def request_exit(self) -> None: ...


@dataclass(frozen=True)
class CommandContext:
    registry: object
    ui: CommandUI
    agent: "Agent"
    sessions: object | None = None
    skills: object | None = None
