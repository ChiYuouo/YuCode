"""子 Agent 领域中的不可变数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from yucode.permissions import PermissionMode
from yucode.providers.base import Usage


class AgentModel(str, Enum):
    """角色可选择的模型档位。"""

    INHERIT = "inherit"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class SubagentKind(str, Enum):
    DEFINITION = "definition"
    FORK = "fork"
    TEAM_MEMBER = "team_member"


class AgentIsolation(str, Enum):
    NONE = "none"
    WORKTREE = "worktree"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BACKGROUND = "background"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class AgentSource:
    tier: Literal["plugin", "builtin", "user", "project"]
    path: Path


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    tools: frozenset[str] | None
    disallowed_tools: frozenset[str]
    model: AgentModel
    max_iterations: int
    permission_mode: PermissionMode
    prompt: str
    source: AgentSource
    isolation: AgentIsolation = AgentIsolation.NONE


@dataclass(frozen=True)
class AgentDiagnostic:
    message: str
    path: Path


@dataclass(frozen=True)
class AgentCatalog:
    definitions: dict[str, AgentDefinition] = field(default_factory=dict)
    diagnostics: tuple[AgentDiagnostic, ...] = ()

    def get(self, name: str) -> AgentDefinition | None:
        return self.definitions.get(name)


@dataclass(frozen=True)
class SubagentRequest:
    kind: SubagentKind
    prompt: str
    definition_name: str | None = None
    run_in_background: bool = False
    isolation: AgentIsolation | None = None


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    parent_task_id: str | None
    kind: SubagentKind
    definition_name: str | None
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    progress: str = ""


@dataclass(frozen=True)
class TaskNotification:
    task_id: str
    status: TaskStatus
    summary: str
    usage: Usage


@dataclass(frozen=True)
class TaskOutcome:
    status: TaskStatus
    summary: str
    usage: Usage = field(default_factory=Usage)
    error: str | None = None


@dataclass(frozen=True)
class TraceNode:
    task_id: str
    parent_task_id: str | None
    kind: SubagentKind
    definition_name: str | None
    status: TaskStatus
    usage: Usage = field(default_factory=Usage)
    started_at: datetime | None = None
    ended_at: datetime | None = None
