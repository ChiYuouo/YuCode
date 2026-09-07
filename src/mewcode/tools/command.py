"""经用户批准后运行可取消的 PowerShell 命令。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from mewcode.cancellation import Cancellation
from mewcode.tools.base import ToolContext, ToolDefinition, ToolResult, ToolSafety
from mewcode.tools.filesystem import MAX_RESULT_CHARS

COMMAND_TIMEOUT_SECONDS = 30


class RunCommandTool:
    safety = ToolSafety.SIDE_EFFECT

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

    async def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolContext,
        call_id: str,
        cancellation: Cancellation,
    ) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return _failure(call_id, "参数 command 必须是非空字符串。", "invalid_arguments")
        if cancellation.is_cancelled:
            return _failure(call_id, "用户已取消，命令未执行。", "cancelled")
        try:
            process = await asyncio.create_subprocess_exec(
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                cwd=context.root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return _failure(call_id, f"无法启动 PowerShell：{error}", "command_start_error")

        communicate = asyncio.create_task(process.communicate())
        cancel_wait = asyncio.create_task(cancellation.wait())
        done, _ = await asyncio.wait(
            {communicate, cancel_wait},
            timeout=COMMAND_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate not in done:
            process.kill()
            stdout, stderr = await communicate
            cancel_wait.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_wait
            if cancellation.is_cancelled:
                return _failure(call_id, "用户已取消，命令已终止。", "cancelled")
            return _failure(call_id, f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已终止。", "timeout")

        cancel_wait.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_wait
        stdout, stderr = communicate.result()
        output = b"".join(part for part in (stdout, stderr) if part).decode("utf-8", errors="replace")
        truncated = len(output) > MAX_RESULT_CHARS
        if truncated:
            output = output[:MAX_RESULT_CHARS] + "\n…（输出已截断）"
        if process.returncode == 0:
            summary = "命令执行成功。"
            if truncated:
                summary += " 输出已截断。"
            return ToolResult(call_id, self.definition.name, True, summary, output, target=command)
        summary = f"命令以退出码 {process.returncode} 结束。"
        if truncated:
            summary += " 输出已截断。"
        return ToolResult(call_id, self.definition.name, False, summary, output, "nonzero_exit", command)


def _failure(call_id: str, summary: str, code: str) -> ToolResult:
    return ToolResult(call_id, "run_command", False, summary, error_code=code)
