"""工具调用的读取、修改与验证流程状态。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yucode.tools.base import ToolCall, ToolResult


_SPECIAL_COMMAND_WORDS = (
    "get-content",
    "set-content",
    "add-content",
    "out-file",
    "remove-item",
    "get-childitem",
    "select-string",
)


@dataclass(frozen=True)
class WorkflowIssue:
    """可由 Agent 补足步骤后重试的流程问题。"""

    reason: str
    error_code: str = "workflow_precondition"


class ToolWorkflow:
    """只维护工具流程证据，不决定权限。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self.read_targets: set[str] = set()
        self.pending_verifications: set[str] = set()

    def check(self, call: ToolCall) -> WorkflowIssue | None:
        """返回当前调用尚未满足的流程前置条件。"""
        if call.name == "run_command":
            command = call.arguments.get("command")
            if isinstance(command, str) and any(word in command.lower() for word in _SPECIAL_COMMAND_WORDS):
                return WorkflowIssue("该命令可由专用文件或搜索工具完成，必须优先使用专用工具。")

        target = _target(call, self._root)
        if call.name == "edit_file" and target is not None and target not in self.read_targets:
            return WorkflowIssue("编辑已有文件前必须先成功读取同一目标文件。")
        if call.name == "write_file" and target is not None and self._exists(target) and target not in self.read_targets:
            return WorkflowIssue("覆盖已有文件前必须先成功读取同一目标文件。")
        return None

    def record(self, result: ToolResult) -> None:
        """只采纳真实成功结果作为读取或验证证据。"""
        if not result.success or not result.target:
            return
        if result.name == "read_file":
            self.read_targets.add(result.target)
            self.pending_verifications.discard(result.target)
        elif result.name in {"write_file", "edit_file"}:
            self.pending_verifications.add(result.target)

    def _exists(self, target: str) -> bool:
        try:
            return (self._root / target).resolve(strict=False).is_file()
        except OSError:
            return False


def _target(call: ToolCall, root: Path) -> str | None:
    value = call.arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        candidate = (root / value).resolve(strict=False)
        return candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
