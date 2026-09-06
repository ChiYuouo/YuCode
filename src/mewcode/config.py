"""读取并校验 MewCode 的 YAML 配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

import yaml


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


def load_config(path: Path | None = None) -> ProviderConfig:
    """从指定路径或当前目录的 ``mewcode.yaml`` 加载配置。"""
    config_path = path or Path.cwd() / "mewcode.yaml"
    if not config_path.is_file():
        raise ConfigError(f"找不到配置文件：{config_path.name}。请在当前目录创建 mewcode.yaml。")

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

    if protocol == "openai" and thinking_enabled:
        raise ConfigError("thinking.enabled 仅支持 anthropic 协议。")

    return ProviderConfig(
        protocol=protocol,
        model=model,
        base_url=base_url,
        api_key=api_key,
        thinking_enabled=thinking_enabled,
    )


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
