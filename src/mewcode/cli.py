"""MewCode 的终端交互入口。"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from mewcode.config import ConfigError, ProviderConfig, load_config
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import Provider
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools.registry import ToolRegistry
from mewcode.tui.app import ChatApp


def main() -> None:
    """从当前目录读取配置后启动交互会话。"""
    console = Console()
    try:
        config = load_config()
    except ConfigError as error:
        console.print(f"配置错误：{error}", style="red")
        return

    ChatApp(create_provider(config), config, ToolRegistry(Path.cwd())).run()


def create_provider(config: ProviderConfig) -> Provider:
    """根据配置选择唯一的后端实现。"""
    if config.protocol == "openai":
        return OpenAIProvider(config)
    return AnthropicProvider(config)
