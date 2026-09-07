"""经用户批准后运行的 PowerShell 命令工具。"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from typing import Any

from mewcode.tools.base import ToolContext, ToolDefinition, ToolResult
from mewcode.tools.filesystem import MAX_RESULT_CHARS

COMMAND_TIMEOUT_SECONDS = 30


class RunCommandTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "run_command",
            "在工作目录中执行一条 PowerShell 命令。执行前必须取得用户确认。",
            {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 PowerShell 命令"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return _failure(call_id, "参数 command 必须是非空字符串。", "invalid_arguments")
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=context.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _failure(call_id, f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已终止。", "timeout")
        except OSError as error:
            return _failure(call_id, f"无法启动 PowerShell：{error}", "command_start_error")

        output = "".join(part for part in (completed.stdout, completed.stderr) if part)
        truncated = len(output) > MAX_RESULT_CHARS
        if truncated:
            output = output[:MAX_RESULT_CHARS] + "\n…（输出已截断）"
        if completed.returncode == 0:
            summary = "命令执行成功。"
            if truncated:
                summary += " 输出已截断。"
            return ToolResult(call_id, self.definition.name, True, summary, output, target=command)
        summary = f"命令以退出码 {completed.returncode} 结束。"
        if truncated:
            summary += " 输出已截断。"
        return ToolResult(call_id, self.definition.name, False, summary, output, "nonzero_exit", command)


def _failure(call_id: str, summary: str, code: str) -> ToolResult:
    return ToolResult(call_id, "run_command", False, summary, error_code=code)
