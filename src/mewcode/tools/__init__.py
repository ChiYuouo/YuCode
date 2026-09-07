"""MewCode 内置工具及其统一执行接口。"""

from mewcode.tools.base import ToolCall, ToolDefinition, ToolResult
from mewcode.tools.registry import ToolRegistry

__all__ = ["ToolCall", "ToolDefinition", "ToolRegistry", "ToolResult"]
