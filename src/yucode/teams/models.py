"""Agent Team 领域的不可变值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping


class TeamBackend(str, Enum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in_process"


class MemberState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    STOPPED = "stopped"
    FAILED = "failed"


class TeamTaskState(str, Enum):
    BLOCKED = "blocked"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MessageKind(str, Enum):
    TEXT = "text"
    BROADCAST = "broadcast"
    SHUTDOWN = "shutdown"
    PLAN_REQUEST = "plan_request"
    PLAN_DECISION = "plan_decision"
    REMINDER = "reminder"
    TASK_UPDATE = "task_update"


@dataclass(frozen=True)
class TeamMember:
    name: str
    agent_id: str
    role: str
    workspace_root: Path
    backend: TeamBackend
    requires_approval: bool = False
    state: MemberState = MemberState.CREATED
    worktree_slug: str | None = None
    transcript_id: str | None = None
    backend_handle: str | None = None
    approval_request_id: str | None = None
    approved_request_id: str | None = None
    pending_prompt: str | None = None
    pending_task_id: str | None = None
    merged: bool = False
    plan_submitted_request_id: str | None = None
    writable: bool = False


@dataclass(frozen=True)
class AgentTeam:
    version: int
    name: str
    lead_id: str
    root: Path
    members: tuple[TeamMember, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TeamTask:
    task_id: str
    title: str
    detail: str
    assignee: str | None
    dependencies: tuple[str, ...]
    state: TeamTaskState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TeamMessage:
    message_id: str
    sender: str
    recipients: tuple[str, ...]
    kind: MessageKind
    body: str
    summary: str
    created_at: datetime
    read_by: frozenset[str] = field(default_factory=frozenset)
    protocol: Mapping[str, str] = field(default_factory=dict)
