"""工具调用的校验、确认与统一错误包装。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.registry import ToolRegistry

ApprovalCallback = Callable[[ToolCall], bool]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call: ToolCall, approve_command: ApprovalCallback | None = None) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return _failure(call, f"未知工具：{call.name}。", "unknown_tool")
        if not isinstance(call.arguments, Mapping):
            return _failure(call, "工具参数必须是对象。", "invalid_arguments")
        if call.name == "run_command":
            if approve_command is None or not approve_command(call):
                return _failure(call, "用户拒绝执行命令。", "user_rejected")
        try:
            return tool.execute(call.arguments, self._registry.context, call.id)
        except Exception as error:  # 工具边界必须把所有意外错误转为模型可处理结果。
            return _failure(call, f"工具执行异常：{error}", "tool_exception")


def _failure(call: ToolCall, summary: str, code: str) -> ToolResult:
    return ToolResult(call.id, call.name, False, summary, error_code=code)
