"""Hook 动作文本中的上下文变量替换。"""

from __future__ import annotations

import re

from yucode.hooks.conditions import context_value, validate_field
from yucode.hooks.models import HookContext

_VARIABLE = re.compile(r"\$(EVENT|TOOL_NAME|FILE_PATH|MESSAGE|ERROR|TOOL_ARGS(?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
_DOLLAR_WORD = re.compile(r"\$([A-Z][A-Z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")


def validate_template(value: str) -> None:
    for match in _DOLLAR_WORD.finditer(value):
        validate_field(match.group(1))


def render_template(value: str, context: HookContext) -> str:
    return _VARIABLE.sub(lambda item: context_value(context, item.group(1)), value)
