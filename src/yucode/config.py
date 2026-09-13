"""读取并校验 YuCode 的 YAML 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import os
from pathlib import Path
import re
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

import yaml

from yucode.permissions import PermissionMode
from yucode.hooks.loader import load_hooks
from yucode.hooks.models import Hook
from yucode import userdirs


class ConfigError(ValueError):
    """配置文件无法安全使用时抛出。"""


@dataclass(frozen=True)
class ProviderConfig:
    """当前激活的模型供应商配置。"""

    protocol: Literal["openai", "anthropic"]
    model: str
    base_url: str
    api_key: str
    thinking_enabled: bool = False


@dataclass(frozen=True)
class AgentConfig:
    """Agent Loop 的安全配置。"""

    max_iterations: int = 10


@dataclass(frozen=True)
class SubagentConfig:
    global_disallowed: tuple[str, ...] = ()
    background_allowed: tuple[str, ...] | None = None
    execution_timeout_seconds: float | None = None


@dataclass(frozen=True)
class WorktreeConfig:
    """Git Worktree 的初始化与清理配置。"""

    worktreeinclude: tuple[str, ...] = ()
    symlink_directories: tuple[str, ...] = ("node_modules", ".venv", "vendor")
    cleanup_after: timedelta = timedelta(days=7)
    cleanup_interval_seconds: float = 3600

    @property
    def scaffolding_paths(self) -> tuple[str, ...]:
        """初始化器会在 Worktree 内创建、但不属于使用方改动的仓库内相对路径。

        复制规则与软链接规则产生的都是运行环境脚手架，不是成员或子 Agent 的工作成果，
        因此"是否还有未提交改动"和"该提交哪些文件"都必须把它们排除在外。
        """
        return tuple(dict.fromkeys((*self.worktreeinclude, *self.symlink_directories)))


@dataclass(frozen=True)
class TeamConfig:
    """Agent Team 的本地运行与协调配置。"""

    enabled: bool = True
    backend_priority: tuple[str, ...] = ("tmux", "iterm2", "in_process")
    coordinator_enabled: bool = False
    mailbox_lock_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ContextConfig:
    """供应商无关的上下文窗口预算。"""

    window_tokens: int = 128_000


@dataclass(frozen=True)
class PermissionConfig:
    """启动时使用的权限模式。"""

    mode: PermissionMode = PermissionMode.DEFAULT


@dataclass(frozen=True)
class MCPServerConfig:
    """一个已校验且已展开环境变量的 MCP Server。"""

    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPConfigIssue:
    """仅影响一个 MCP Server 的可展示配置问题。"""

    server_name: str
    reason: str


@dataclass(frozen=True)
class AppConfig:
    """YuCode 的完整应用配置。"""

    provider: ProviderConfig
    agent: AgentConfig = AgentConfig()
    context: ContextConfig = ContextConfig()
    permissions: PermissionConfig = PermissionConfig()
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    mcp_issues: tuple[MCPConfigIssue, ...] = ()
    hooks: tuple[Hook, ...] = ()
    subagents: SubagentConfig = SubagentConfig()
    worktrees: WorktreeConfig = WorktreeConfig()
    teams: TeamConfig = TeamConfig()


def load_config(path: Path | None = None, home: Path | None = None) -> AppConfig:
    """从指定路径或当前目录的 ``yucode.yaml`` 加载配置。

    ``home`` 仅用于测试注入用户主目录，正常调用时省略。
    """
    config_path = path or Path.cwd() / "yucode.yaml"
    if not config_path.is_file():
        raise ConfigError(f"找不到配置文件：{config_path.name}。请在当前目录创建 yucode.yaml。")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"无法读取配置文件：{error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"配置文件不是有效的 YAML：{error}") from error

    if not isinstance(raw, Mapping):
        raise ConfigError("配置文件顶层必须是键值对象。")

    protocol = _required_text(raw, "protocol")
    if protocol not in {"openai", "anthropic"}:
        raise ConfigError("protocol 只能是 openai 或 anthropic。")

    model = _required_text(raw, "model")
    base_url = _required_text(raw, "base_url").rstrip("/")
    _validate_url(base_url)
    api_key = _required_text(raw, "api_key")
    thinking_enabled = _parse_thinking(raw.get("thinking"))
    agent = _parse_agent(raw.get("agent"))
    subagents = _parse_subagents(raw.get("subagents"))
    worktrees = _parse_worktrees(raw.get("worktrees"))
    teams = _parse_teams(raw.get("teams"))
    context = _parse_context(raw.get("context"))
    permissions = _parse_permissions(raw.get("permissions"))
    try:
        hooks = load_hooks(raw.get("hooks"))
    except ValueError as error:
        raise ConfigError(f"Hook 配置错误：{error}") from error

    if protocol == "openai" and thinking_enabled:
        raise ConfigError("thinking.enabled 仅支持 anthropic 协议。")

    servers, issues = _load_mcp_servers(raw, _load_user_raw(config_path, home))
    return AppConfig(
        provider=ProviderConfig(
            protocol=protocol,
            model=model,
            base_url=base_url,
            api_key=api_key,
            thinking_enabled=thinking_enabled,
        ),
        agent=agent,
        context=context,
        permissions=permissions,
        mcp_servers=servers,
        mcp_issues=issues,
        hooks=hooks,
        subagents=subagents,
        worktrees=worktrees,
        teams=teams,
    )


def _load_user_raw(project_path: Path, home: Path | None = None) -> Mapping[str, Any]:
    """用户配置只为 MCP Server 提供可选的补充来源。"""
    user_path = userdirs.resolve_path("yucode.yaml", home)
    if user_path.resolve() == project_path.resolve() or not user_path.is_file():
        return {}
    try:
        raw = yaml.safe_load(user_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, Mapping) else {}


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_mcp_servers(project: Mapping[str, Any], user: Mapping[str, Any]) -> tuple[tuple[MCPServerConfig, ...], tuple[MCPConfigIssue, ...]]:
    """合并两层声明；坏的单项以问题形式保留，不阻断启动。"""
    merged: dict[str, Any] = {}
    for source in (user, project):
        raw = source.get("mcp_servers", {})
        if isinstance(raw, Mapping):
            merged.update(raw)
    servers: list[MCPServerConfig] = []
    issues: list[MCPConfigIssue] = []
    for name, value in merged.items():
        label = str(name)
        try:
            servers.append(_parse_mcp_server(label, value))
        except ValueError as error:
            issues.append(MCPConfigIssue(label, str(error)))
    return tuple(servers), tuple(issues)


def _parse_mcp_server(name: str, value: Any) -> MCPServerConfig:
    if not name.strip() or not isinstance(value, Mapping):
        raise ValueError("声明必须是包含 transport 的键值对象。")
    transport = value.get("transport")
    if transport == "stdio":
        command = _required_text(value, "command")
        args = _string_list(value.get("args", []), "args")
        return MCPServerConfig(name, "stdio", command, args, _expanded_map(value.get("env", {}), "env"))
    if transport == "http":
        url = _required_text(value, "url").rstrip("/")
        _validate_url(url)
        return MCPServerConfig(name, "http", url=url, headers=_expanded_map(value.get("headers", {}), "headers"))
    raise ValueError("transport 只能是 stdio 或 http。")


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} 必须是字符串列表。")
    return tuple(value)


def _expanded_map(value: Any, field: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{field} 必须是字符串键值对象。")
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in os.environ:
            raise ValueError(f"环境变量 {variable} 未定义。")
        return os.environ[variable]
    return {key: _ENV_REF.sub(replace, item) for key, item in value.items()}


def _required_text(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"缺少或无效的配置字段：{field}。")
    return value.strip()


def _validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("base_url 必须是以 http:// 或 https:// 开头的完整地址。")


def _parse_thinking(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, Mapping):
        raise ConfigError("thinking 必须是包含 enabled 的键值对象。")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("thinking.enabled 必须是 true 或 false。")
    return enabled


def _parse_agent(value: Any) -> AgentConfig:
    if value is None:
        return AgentConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("agent 必须是包含 max_iterations 的键值对象。")
    max_iterations = value.get("max_iterations", 10)
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
        raise ConfigError("agent.max_iterations 必须是正整数。")
    return AgentConfig(max_iterations=max_iterations)


def _parse_subagents(value: Any) -> SubagentConfig:
    if value is None:
        return SubagentConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("subagents 必须是键值对象。")
    allowed = {"global_disallowed", "background_allowed", "execution_timeout_seconds"}
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"subagents 包含未知字段：{sorted(unknown)[0]}。")
    def names(item: Any, field: str, optional: bool = False):
        if item is None and optional:
            return None
        if not isinstance(item, list) or not all(isinstance(name, str) and name.strip() for name in item):
            raise ConfigError(f"subagents.{field} 必须是字符串列表。")
        if len(set(item)) != len(item):
            raise ConfigError(f"subagents.{field} 不能包含重复工具名。")
        return tuple(item)
    timeout = value.get("execution_timeout_seconds")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ConfigError("subagents.execution_timeout_seconds 必须是正数。")
    return SubagentConfig(names(value.get("global_disallowed", []), "global_disallowed"), names(value.get("background_allowed"), "background_allowed", True), float(timeout) if timeout is not None else None)


def _parse_worktrees(value: Any) -> WorktreeConfig:
    if value is None:
        return WorktreeConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("worktrees 必须是键值对象。")
    allowed = {"worktreeinclude", "symlink_directories", "cleanup_after_hours", "cleanup_interval_seconds"}
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"worktrees 包含未知字段：{sorted(unknown)[0]}。")

    def paths(raw: Any, field: str, default: tuple[str, ...]) -> tuple[str, ...]:
        items = default if raw is None else raw
        if not isinstance(items, (list, tuple)) or not all(isinstance(item, str) for item in items):
            raise ConfigError(f"worktrees.{field} 必须是字符串列表。")
        normalized: list[str] = []
        for item in items:
            candidate = item.strip().replace("\\", "/")
            path = Path(candidate)
            if not candidate or path.is_absolute() or ".." in path.parts or "." in path.parts:
                raise ConfigError(f"worktrees.{field} 只能包含仓库内的相对路径。")
            normalized.append(candidate)
        if len(set(normalized)) != len(normalized):
            raise ConfigError(f"worktrees.{field} 不能包含重复路径。")
        return tuple(normalized)

    cleanup_hours = value.get("cleanup_after_hours", 24 * 7)
    interval = value.get("cleanup_interval_seconds", 3600)
    if isinstance(cleanup_hours, bool) or not isinstance(cleanup_hours, (int, float)) or cleanup_hours <= 0:
        raise ConfigError("worktrees.cleanup_after_hours 必须是正数。")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        raise ConfigError("worktrees.cleanup_interval_seconds 必须是正数。")
    return WorktreeConfig(
        paths(value.get("worktreeinclude", []), "worktreeinclude", ()),
        paths(value.get("symlink_directories"), "symlink_directories", WorktreeConfig().symlink_directories),
        timedelta(hours=float(cleanup_hours)),
        float(interval),
    )


def _parse_teams(value: Any) -> TeamConfig:
    if value is None:
        return TeamConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("teams 必须是键值对象。")
    allowed = {"enabled", "backend_priority", "coordinator_enabled", "mailbox_lock_timeout_seconds"}
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"teams 包含未知字段：{sorted(unknown)[0]}。")
    enabled = value.get("enabled", True)
    coordinator_enabled = value.get("coordinator_enabled", False)
    if not isinstance(enabled, bool) or not isinstance(coordinator_enabled, bool):
        raise ConfigError("teams.enabled 和 teams.coordinator_enabled 必须是 true 或 false。")
    priority = value.get("backend_priority", ["tmux", "iterm2", "in_process"])
    valid_backends = {"tmux", "iterm2", "in_process"}
    if (not isinstance(priority, list) or not priority or not all(isinstance(item, str) and item in valid_backends for item in priority)
            or len(set(priority)) != len(priority)):
        raise ConfigError("teams.backend_priority 必须是无重复的 tmux、iterm2、in_process 列表。")
    timeout = value.get("mailbox_lock_timeout_seconds", 5.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("teams.mailbox_lock_timeout_seconds 必须是正数。")
    return TeamConfig(enabled, tuple(priority), coordinator_enabled, float(timeout))


def _parse_context(value: Any) -> ContextConfig:
    if value is None:
        return ContextConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("context 必须是包含 window_tokens 的键值对象。")
    window_tokens = value.get("window_tokens", 128_000)
    if isinstance(window_tokens, bool) or not isinstance(window_tokens, int):
        raise ConfigError("context.window_tokens 必须是整数。")
    if window_tokens <= 13_000:
        raise ConfigError("context.window_tokens 必须大于 13000。")
    return ContextConfig(window_tokens=window_tokens)


def _parse_permissions(value: Any) -> PermissionConfig:
    if value is None:
        return PermissionConfig()
    if not isinstance(value, Mapping):
        raise ConfigError("permissions 必须是包含 mode 的键值对象。")
    mode = value.get("mode", PermissionMode.DEFAULT.value)
    if not isinstance(mode, str):
        raise ConfigError("permissions.mode 必须是字符串。")
    try:
        return PermissionConfig(PermissionMode(mode))
    except ValueError as error:
        values = ", ".join(item.value for item in PermissionMode)
        raise ConfigError(f"permissions.mode 只能是 {values}。") from error
