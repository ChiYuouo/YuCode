"""Hook YAML 规则解析与集中校验。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from yucode.hooks.conditions import parse_condition_group
from yucode.hooks.models import Action, ActionType, Hook, HookEvent
from yucode.hooks.template import validate_template

_RULE_KEYS = {"id", "event", "if", "action", "once", "async"}
_ACTION_KEYS = {"type", "command", "prompt", "url", "method", "body", "timeout_seconds", "reject", "reason"}


def load_hooks(raw: Any) -> tuple[Hook, ...]:
    if raw is None: return ()
    if not isinstance(raw, list): raise ValueError("hooks 必须是列表。")
    hooks = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        try: hook = _parse_hook(item, index)
        except ValueError as error: raise ValueError(f"{_raw_label(item, index)} 无效：{error}") from error
        if hook.identifier is not None:
            if hook.identifier in seen: raise ValueError(f"Hook id 重复：{hook.identifier}。每条规则需要唯一的 id。")
            seen.add(hook.identifier)
        hooks.append(hook)
    return tuple(hooks)


def _raw_label(raw: Any, index: int) -> str:
    """解析失败时仍尽量用配置里的 id 指认规则，否则退回声明顺序。"""
    if isinstance(raw, Mapping):
        identifier = raw.get("id")
        if isinstance(identifier, str) and identifier.strip():
            return f"Hook「{identifier.strip()}」"
    return f"Hook 第 {index} 条"


def _parse_hook(raw: Any, index: int) -> Hook:
    if not isinstance(raw, Mapping): raise ValueError("规则必须是键值对象。")
    unknown = set(raw) - _RULE_KEYS
    if unknown: raise ValueError(f"包含未知字段：{sorted(unknown, key=str)[0]}。")
    identifier = raw.get("id")
    if identifier is not None and (not isinstance(identifier, str) or not identifier.strip()):
        raise ValueError("id 必须是非空字符串。")
    try: event = HookEvent(raw.get("event"))
    except ValueError as error: raise ValueError("event 必须是支持的生命周期事件。") from error
    condition = parse_condition_group(raw["if"]) if "if" in raw else None
    action = _parse_action(raw.get("action"))
    once, async_run = raw.get("once", False), raw.get("async", False)
    if not isinstance(once, bool) or not isinstance(async_run, bool): raise ValueError("once 和 async 必须是布尔值。")
    if action.reject:
        if event is not HookEvent.PRE_TOOL_USE: raise ValueError("reject 只允许用于 pre_tool_use。")
        if async_run: raise ValueError("reject Hook 不允许 async。")
        if not action.reason: raise ValueError("reject Hook 必须提供 reason。")
    return Hook(event, action, condition, once, async_run, index, identifier.strip() if isinstance(identifier, str) else None)


def _parse_action(raw: Any) -> Action:
    if not isinstance(raw, Mapping): raise ValueError("action 必须是键值对象。")
    unknown = set(raw) - _ACTION_KEYS
    if unknown: raise ValueError(f"action 包含未知字段：{sorted(unknown, key=str)[0]}。")
    try: kind = ActionType(raw.get("type"))
    except ValueError as error: raise ValueError("action.type 只能是 command、prompt、http 或 agent。") from error
    def text(name: str, required=False) -> str | None:
        value = raw.get(name)
        if value is None and not required: return None
        if not isinstance(value, str) or (required and not value.strip()): raise ValueError(f"action.{name} 必须是非空字符串。")
        validate_template(value); return value
    command = text("command", kind is ActionType.COMMAND); prompt = text("prompt", kind is ActionType.PROMPT)
    url = text("url", kind is ActionType.HTTP); body = text("body")
    if url is not None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc: raise ValueError("action.url 必须是 HTTP(S) 完整地址。")
    method = raw.get("method", "POST")
    if not isinstance(method, str) or not method.strip(): raise ValueError("action.method 必须是非空字符串。")
    timeout = raw.get("timeout_seconds")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0): raise ValueError("action.timeout_seconds 必须是正数。")
    reject = raw.get("reject", False); reason = text("reason")
    if not isinstance(reject, bool): raise ValueError("action.reject 必须是布尔值。")
    return Action(kind, command, prompt, url, method.upper(), body, float(timeout) if timeout else None, reject, reason)
