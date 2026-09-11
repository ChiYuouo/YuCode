"""读取并校验 YuCode 的 YAML 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

import yaml

from yucode.permissions import PermissionMode
from yucode.hooks.loader import load_hooks
from yucode.hooks.models import Hook


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


def load_config(path: Path | None = None) -> AppConfig:
    """从指定路径或当前目录的 ``yucode.yaml`` 加载配置。"""
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
    context = _parse_context(raw.get("context"))
    permissions = _parse_permissions(raw.get("permissions"))
    try:
        hooks = load_hooks(raw.get("hooks"))
    except ValueError as error:
        raise ConfigError(f"Hook 配置错误：{error}") from error

    if protocol == "openai" and thinking_enabled:
        raise ConfigError("thinking.enabled 仅支持 anthropic 协议。")

    servers, issues = _load_mcp_servers(raw, _load_user_raw(config_path))
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
    )


def _load_user_raw(project_path: Path) -> Mapping[str, Any]:
    """用户配置只为 MCP Server 提供可选的补充来源。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return {}
    user_path = Path(appdata) / "YuCode" / "yucode.yaml"
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
