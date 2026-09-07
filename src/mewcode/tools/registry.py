"""内置工具的集中注册。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from mewcode.tools.base import Tool, ToolContext, ToolDefinition
from mewcode.tools.command import RunCommandTool
from mewcode.tools.filesystem import EditFileTool, FindFilesTool, ReadFileTool, SearchCodeTool, WriteFileTool


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

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
