"""工具层共享的数据结构与协议。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


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
    """每项工具执行时不可变的环境。"""

    root: Path


class Tool(Protocol):
    """所有内置工具实现的统一协议。"""

    @property
    def definition(self) -> ToolDefinition:
        """返回工具的模型元信息。"""

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        """执行调用并始终返回结构化结果。"""
