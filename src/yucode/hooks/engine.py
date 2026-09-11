"""Hook 运行时调度与工具拦截。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from yucode.hooks.conditions import matches
from yucode.hooks.executors import ActionExecutor
from yucode.hooks.models import Hook, HookContext, HookEvent, HookRunResult, ToolRejectedError
from yucode.hooks.template import render_template

_LOG = logging.getLogger(__name__)


class HookEngine:
    def __init__(self, hooks: Sequence[Hook], root: Path) -> None:
        self._hooks = tuple(hooks); self._executor = ActionExecutor(root); self._once: set[int] = set(); self._prompts: list[str] = []; self._tasks: set[asyncio.Task] = set()

    async def run_hooks(self, context: HookContext) -> HookRunResult:
        if context.event is HookEvent.PRE_TOOL_USE: return await self.run_pre_tool_hooks(context)
        prompts: list[str] = []
        for hook in self._matching(context):
            if hook.async_run:
                self._once.add(hook.source_index) if hook.once else None
                task = asyncio.create_task(self._run_safely(hook, context)); self._tasks.add(task); task.add_done_callback(self._tasks.discard)
            else:
                try: prompts.extend((await self._executor.execute(hook.action, context)).prompts); self._once.add(hook.source_index) if hook.once else None
                except Exception as error: _LOG.warning("Hook 执行失败：%s", error)
        self._prompts.extend(prompts)
        return HookRunResult(tuple(prompts))

    async def run_pre_tool_hooks(self, context: HookContext) -> HookRunResult:
        if context.event is not HookEvent.PRE_TOOL_USE: raise ValueError("run_pre_tool_hooks 只接受 pre_tool_use。")
        prompts: list[str] = []
        for hook in self._matching(context):
            try:
                result = await self._executor.execute(hook.action, context)
                self._once.add(hook.source_index) if hook.once else None; prompts.extend(result.prompts)
                if hook.action.reject: raise ToolRejectedError(render_template(hook.action.reason or "工具调用被 Hook 拒绝。", context))
            except ToolRejectedError: raise
            except Exception as error: _LOG.warning("Hook 执行失败：%s", error)
        self._prompts.extend(prompts); return HookRunResult(tuple(prompts))

    def drain_prompts(self) -> tuple[str, ...]:
        result = tuple(self._prompts); self._prompts.clear(); return result

    def _matching(self, context: HookContext):
        return (hook for hook in self._hooks if hook.event is context.event and hook.source_index not in self._once and matches(hook.condition, context))

    async def _run_safely(self, hook: Hook, context: HookContext) -> None:
        try:
            result = await self._executor.execute(hook.action, context); self._prompts.extend(result.prompts)
        except Exception as error: _LOG.warning("异步 Hook 执行失败：%s", error)
