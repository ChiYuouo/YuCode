"""Hook 条件字段读取与求值。"""

from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
import re
from typing import Any

from yucode.hooks.models import Condition, ConditionGroup, ConditionOperator, HookContext

_FIELDS = {"EVENT", "TOOL_NAME", "FILE_PATH", "MESSAGE", "ERROR", "TASK_ID", "PARENT_TASK_ID", "TASK_STATUS"}
_CONDITION_KEYS = {"field", "operator", "value"}


def context_value(context: HookContext, field: str) -> str:
    """读取固定字段或嵌套工具参数；不存在时返回空字符串。"""
    values = {"EVENT": context.event.value, "TOOL_NAME": context.tool_name, "FILE_PATH": context.file_path,
              "MESSAGE": context.message, "ERROR": context.error, "TASK_ID": context.task_id,
              "PARENT_TASK_ID": context.parent_task_id, "TASK_STATUS": context.task_status}
    if field in values:
        return values[field]
    if field.startswith("TOOL_ARGS."):
        value: Any = context.tool_args
        for part in field.removeprefix("TOOL_ARGS.").split("."):
            if not isinstance(value, Mapping) or part not in value:
                return ""
            value = value[part]
        return "" if value is None else str(value)
    return ""


def validate_field(field: Any) -> str:
    if not isinstance(field, str) or not field:
        raise ValueError("条件 field 必须是非空字符串。")
    if field not in _FIELDS and not (field.startswith("TOOL_ARGS.") and all(field.removeprefix("TOOL_ARGS.").split("."))):
        raise ValueError(f"未知上下文字段：{field}。")
    return field


def parse_condition_group(raw: Any) -> ConditionGroup:
    if not isinstance(raw, Mapping) or set(raw) not in ({"all"}, {"any"}):
        raise ValueError("if 必须且只能包含 all 或 any。")
    mode = next(iter(raw))
    entries = raw[mode]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"if.{mode} 必须是非空条件列表。")
    conditions: list[Condition] = []
    for item in entries:
        if not isinstance(item, Mapping): raise ValueError("每个条件必须是键值对象。")
        unknown = set(item) - _CONDITION_KEYS
        if unknown: raise ValueError(f"条件包含未知字段：{sorted(unknown, key=str)[0]}。")
        field = validate_field(item.get("field")); value = item.get("value")
        if not isinstance(value, str): raise ValueError("条件 value 必须是字符串。")
        try: operator = ConditionOperator(item.get("operator"))
        except ValueError as error: raise ValueError("条件 operator 只能是 ==、!=、=~ 或 ~=。") from error
        if operator is ConditionOperator.REGEX:
            try: re.compile(value)
            except re.error as error: raise ValueError(f"条件正则无效：{error}。") from error
        conditions.append(Condition(field, operator, value))
    return ConditionGroup(mode, tuple(conditions))


def matches(group: ConditionGroup | None, context: HookContext) -> bool:
    if group is None: return True
    checks = [_matches(item, context_value(context, item.field)) for item in group.conditions]
    return all(checks) if group.mode == "all" else any(checks)


def _matches(condition: Condition, actual: str) -> bool:
    if condition.operator is ConditionOperator.EQUALS: return actual == condition.value
    if condition.operator is ConditionOperator.NOT_EQUALS: return actual != condition.value
    if condition.operator is ConditionOperator.REGEX: return re.search(condition.value, actual) is not None
    return fnmatchcase(actual, condition.value)
