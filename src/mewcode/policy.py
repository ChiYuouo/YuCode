"""任务授权、工具流程门禁与敏感信息脱敏。"""

from __future__ import annotations

import re
from dataclasses import replace
from enum import Enum
from pathlib import Path

from mewcode.tools.base import ToolCall, ToolResult


class TaskAuthorization(str, Enum):
    """本次用户输入允许产生的最高副作用级别。"""

    ANSWER_ONLY = "answer_only"
    READ_ONLY = "read_only"
    EXECUTE = "execute"


_EXECUTE_WORDS = (
    "创建", "新建", "写入", "修改", "编辑", "删除", "执行", "运行", "实现", "修复", "重构", "更新", "添加", "验证",
    "create", "write", "edit", "modify", "delete", "run", "implement", "fix", "refactor", "update", "add", "verify",
)
_ANSWER_WORDS = (
    "解释", "说明", "介绍", "评审", "审查", "建议", "比较", "是什么", "为什么", "怎么", "是否",
    "explain", "review", "suggest", "compare", "what", "why", "how",
)
_SPECIAL_COMMAND_WORDS = (
    "get-content", "set-content", "add-content", "out-file", "remove-item", "get-childitem", "select-string",
)


def classify_authorization(text: str, mode: str) -> TaskAuthorization:
    """保守地识别用户是否明确授权副作用。"""
    if mode == "plan":
        return TaskAuthorization.READ_ONLY
    normalized = text.lower()
    if any(word in normalized for word in _EXECUTE_WORDS):
        return TaskAuthorization.EXECUTE
    if any(word in normalized for word in _ANSWER_WORDS) or "?" in normalized or "？" in normalized:
        return TaskAuthorization.ANSWER_ONLY
    return TaskAuthorization.READ_ONLY


class ExecutionPolicy:
    """记录本次运行的读取和验证证据，并拒绝不满足条件的调用。"""

    def __init__(self, authorization: TaskAuthorization, root: Path) -> None:
        self.authorization = authorization
        self._root = root.resolve()
        self.read_targets: set[str] = set()
        self.pending_verifications: set[str] = set()
        self.blocking_failure = False

    def preflight(self, call: ToolCall) -> ToolResult | None:
        """返回拒绝结果，或返回 None 允许继续执行。"""
        if call.name in {"write_file", "edit_file", "run_command"} and self.authorization is not TaskAuthorization.EXECUTE:
            return _policy_failure(
                call,
                "当前用户请求未明确授权副作用操作；只能回答或使用只读工具，请说明需要执行的具体操作。",
            )
        if call.name == "run_command":
            command = call.arguments.get("command")
            if isinstance(command, str) and any(word in command.lower() for word in _SPECIAL_COMMAND_WORDS):
                return _policy_failure(call, "该命令可由专用文件或搜索工具完成，必须优先使用专用工具。")
        target = _target(call, self._root)
        if call.name == "edit_file" and target is not None and target not in self.read_targets:
            return _policy_failure(call, "编辑已有文件前必须先成功读取同一目标文件。")
        if call.name == "write_file" and target is not None and self._exists(target) and target not in self.read_targets:
            return _policy_failure(call, "覆盖已有文件前必须先成功读取同一目标文件。")
        return None

    def record(self, result: ToolResult) -> None:
        """只采纳成功工具结果作为读取或验证证据。"""
        if result.error_code == "policy_violation":
            self.blocking_failure = True
            return
        if not result.success:
            return
        self.blocking_failure = False
        if not result.target:
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


class SensitiveDataRedactor:
    """替换常见凭据值，避免它们进入模型历史或用户可见文本。"""

    _ASSIGNMENT = re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|password|passwd|cookie)\b\s*[:=]\s*)([^\s\"']+)"
    )
    _BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{6,}")
    _OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")

    def redact(self, text: str) -> str:
        """返回不含明显凭据值的文本。"""
        redacted = self._ASSIGNMENT.sub(r"\1[已脱敏]", text)
        redacted = self._BEARER.sub(r"\1[已脱敏]", redacted)
        return self._OPENAI_KEY.sub("[已脱敏]", redacted)

    def redact_result(self, result: ToolResult) -> ToolResult:
        return replace(
            result,
            summary=self.redact(result.summary),
            content=self.redact(result.content),
            target=self.redact(result.target),
        )


def _target(call: ToolCall, root: Path) -> str | None:
    value = call.arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        candidate = (root / value).resolve(strict=False)
        return candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _policy_failure(call: ToolCall, summary: str) -> ToolResult:
    return ToolResult(call.id, call.name, False, summary, error_code="policy_violation")
