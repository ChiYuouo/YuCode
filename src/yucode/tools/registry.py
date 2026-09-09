"""内置工具的集中注册。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from yucode.tools.base import Tool, ToolContext, ToolDefinition, ToolSafety
from yucode.tools.command import RunCommandTool
from yucode.tools.filesystem import EditFileTool, FindFilesTool, ReadFileTool, SearchCodeTool, WriteFileTool


class ToolRegistry:
    """管理当前会话可用工具及其工作目录。"""

    def __init__(self, root: Path, tools: Iterable[Tool] | None = None) -> None:
        self._context = ToolContext(root.resolve())
        selected = tools or (
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            RunCommandTool(),
            FindFilesTool(),
            SearchCodeTool(),
        )
        self._tools = {tool.definition.name: tool for tool in selected}

    @property
    def context(self) -> ToolContext:
        return self._context

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    @property
    def read_only_definitions(self) -> tuple[ToolDefinition, ...]:
        """返回规划模式允许暴露给模型的工具。"""
        return tuple(
            tool.definition for tool in self._tools.values() if tool.safety is ToolSafety.READ_ONLY
        )

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def register_many(self, tools: Iterable[Tool]) -> None:
        """原子追加已发现工具，拒绝任何名称冲突。"""
        selected = tuple(tools)
        names = [tool.definition.name for tool in selected]
        if len(names) != len(set(names)) or any(name in self._tools for name in names):
            raise ValueError("MCP 工具名称与现有工具冲突。")
        self._tools.update({tool.definition.name: tool for tool in selected})
