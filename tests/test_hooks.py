from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yucode.hooks.conditions import matches, parse_condition_group
from yucode.hooks.engine import HookEngine
from yucode.hooks.loader import load_hooks
from yucode.hooks.models import HookContext, HookEvent, ToolRejectedError
from yucode.hooks.template import render_template


def test_conditions_support_all_four_operators_and_nested_arguments() -> None:
    context = HookContext(HookEvent.PRE_TOOL_USE, tool_name="run_command", tool_args={"nested": {"value": "safe-42"}})
    for operator, value in (("==", "run_command"), ("!=", "write_file"), ("=~", "run_.*"), ("~=", "run_*")):
        assert matches(parse_condition_group({"all": [{"field": "TOOL_NAME", "operator": operator, "value": value}]}), context)
    assert matches(parse_condition_group({"any": [{"field": "TOOL_ARGS.nested.value", "operator": "==", "value": "safe-42"}]}), context)


def test_template_replaces_context_and_missing_values() -> None:
    context = HookContext(HookEvent.FILE_CHANGE, tool_name="write_file", file_path="a.txt", tool_args={"path": "a.txt"})
    assert render_template("$EVENT $TOOL_NAME $FILE_PATH $TOOL_ARGS.path $ERROR", context) == "file_change write_file a.txt a.txt "


def test_loader_rejects_async_reject_and_accepts_agent_stub() -> None:
    with pytest.raises(ValueError, match="不允许 async"):
        load_hooks([{"event": "pre_tool_use", "async": True, "action": {"type": "prompt", "prompt": "x", "reject": True, "reason": "no"}}])
    assert load_hooks([{"event": "startup", "action": {"type": "agent"}}])[0].action.type.value == "agent"


def test_engine_once_prompt_and_tool_rejection(tmp_path: Path) -> None:
    hooks = load_hooks([
        {"event": "turn_start", "once": True, "action": {"type": "prompt", "prompt": "规则：$EVENT"}},
        {"event": "pre_tool_use", "if": {"all": [{"field": "TOOL_NAME", "operator": "==", "value": "run_command"}]}, "action": {"type": "prompt", "prompt": "x", "reject": True, "reason": "拒绝 $TOOL_NAME"}},
    ])
    engine = HookEngine(hooks, tmp_path)
    async def scenario() -> None:
        await engine.run_hooks(HookContext(HookEvent.TURN_START)); await engine.run_hooks(HookContext(HookEvent.TURN_START))
        assert engine.drain_prompts() == ("规则：turn_start",)
        with pytest.raises(ToolRejectedError, match="拒绝 run_command"):
            await engine.run_pre_tool_hooks(HookContext(HookEvent.PRE_TOOL_USE, tool_name="run_command"))
    asyncio.run(scenario())
