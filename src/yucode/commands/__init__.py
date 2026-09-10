"""YuCode 斜杠命令的公共入口。"""

from yucode.commands.builtins import build_builtin_registry
from yucode.commands.dispatcher import CommandDispatcher
from yucode.commands.parser import InputKind, ParsedInput, parse_input

__all__ = ("CommandDispatcher", "InputKind", "ParsedInput", "build_builtin_registry", "parse_input")
