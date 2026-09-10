"""命令执行和可理解的错误反馈。"""

from __future__ import annotations

from yucode.commands.models import CommandContext, CommandUsageError
from yucode.commands.parser import InputKind, ParsedInput


class CommandDispatcher:
    def __init__(self, context: CommandContext) -> None:
        self._context = context

    async def dispatch(self, parsed: ParsedInput) -> None:
        if parsed.kind is not InputKind.COMMAND:
            return
        definition = self._context.registry.get(parsed.name)
        if definition is None or not parsed.name:
            await self._context.ui.show_message("未知命令。输入 /help 查看可用命令。", error=True)
            return
        try:
            await definition.handler(self._context, parsed.arguments)
        except CommandUsageError as error:
            await self._context.ui.show_message(f"{error}\n用法：{definition.usage}", error=True)
        except Exception as error:
            await self._context.ui.show_message(f"命令执行失败：{error}", error=True)
