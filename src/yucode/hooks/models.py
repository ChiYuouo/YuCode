"""Hook 运行时的核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping


class HookEvent(str, Enum):
    SESSION_START = "session_start"; SESSION_END = "session_end"
    TURN_START = "turn_start"; TURN_END = "turn_end"
    PRE_TOOL_USE = "pre_tool_use"; POST_TOOL_USE = "post_tool_use"
    PRE_SEND = "pre_send"; POST_RECEIVE = "post_receive"
    STARTUP = "startup"; SHUTDOWN = "shutdown"; ERROR = "error"; COMPACT = "compact"
    PERMISSION_REQUEST = "permission_request"; FILE_CHANGE = "file_change"; COMMAND_EXECUTE = "command_execute"


class ActionType(str, Enum):
    COMMAND = "command"; PROMPT = "prompt"; HTTP = "http"; AGENT = "agent"


class ConditionOperator(str, Enum):
    EQUALS = "=="; NOT_EQUALS = "!="; REGEX = "=~"; GLOB = "~="


@dataclass(frozen=True)
class Condition:
    field: str
    operator: ConditionOperator
    value: str


@dataclass(frozen=True)
class ConditionGroup:
    mode: Literal["all", "any"]
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class Action:
    type: ActionType
    command: str | None = None
    prompt: str | None = None
    url: str | None = None
    method: str = "POST"
    body: str | None = None
    timeout_seconds: float | None = None
    reject: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class Hook:
    event: HookEvent
    action: Action
    condition: ConditionGroup | None = None
    once: bool = False
    async_run: bool = False
    source_index: int = 0


@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    tool_name: str = ""
    file_path: str = ""
    message: str = ""
    error: str = ""
    tool_args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookRunResult:
    prompts: tuple[str, ...] = ()


class ToolRejectedError(Exception):
    """Hook 拒绝工具调用时使用的内部信号。"""

