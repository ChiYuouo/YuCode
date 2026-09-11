"""工具层共享的数据结构与协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from yucode.cancellation import Cancellation


class ToolSafety(str, Enum):
    """工具执行对工作区的影响分类。"""

    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"


@dataclass(frozen=True)
class ToolDefinition:
    """可发送给模型的一项工具描述。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次已解析工具调用。"""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果；失败同样以结构化形式返回模型。"""

    call_id: str
    name: str
    success: bool
    summary: str
    content: str = ""
    error_code: str | None = None
    target: str = ""

    def for_model(self) -> str:
        """生成供应商无关、可读的结果正文。"""
        state = "成功" if self.success else "失败"
        lines = [f"工具 {self.name} {state}：{self.summary}"]
        if self.error_code:
            lines.append(f"错误码：{self.error_code}")
        if self.content:
            lines.append(self.content)
        return "\n".join(lines)


@dataclass(frozen=True)
class ToolContext:
    """每项工具执行时的环境；旧工具只需要使用 root。"""

    root: Path
    approval: Any = None
    authorization: Any = None


class Tool(Protocol):
    """所有内置工具实现的统一协议。"""

    @property
    def definition(self) -> ToolDefinition:
        """返回工具的模型元信息。"""

    @property
    def safety(self) -> ToolSafety:
        """返回用于批次调度的安全分类。"""

    async def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolContext,
        call_id: str,
        cancellation: Cancellation,
    ) -> ToolResult:
        """执行调用并始终返回结构化结果。"""


class ToolCatalog(Protocol):
    """能够按名称解析工具的全局目录或当前请求视图。"""

    @property
    def context(self) -> ToolContext: ...

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    @property
    def read_only_definitions(self) -> tuple[ToolDefinition, ...]: ...

    def get(self, name: str) -> Tool | None: ...


class ToolView:
    """一轮请求内冻结的工具可见范围，不修改全局注册表。"""

    def __init__(self, context: ToolContext, tools: Mapping[str, Tool]) -> None:
        self._context = context
        self._tools = dict(tools)

    @property
    def context(self) -> ToolContext:
        return self._context

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values())

    @property
    def read_only_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self._tools.values() if tool.safety is ToolSafety.READ_ONLY)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
