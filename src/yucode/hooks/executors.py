"""Hook 的四类动作执行器。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from yucode.hooks.models import Action, ActionType, HookContext, HookRunResult
from yucode.hooks.template import render_template

_LOG = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, root: Path) -> None: self._root = root

    async def execute(self, action: Action, context: HookContext) -> HookRunResult:
        if action.type is ActionType.PROMPT: return HookRunResult((render_template(action.prompt or "", context),))
        if action.type is ActionType.AGENT:
            _LOG.info("Hook agent 动作当前为 stub，未启动子 Agent。")
            return HookRunResult()
        if action.type is ActionType.HTTP:
            async with httpx.AsyncClient(timeout=action.timeout_seconds or 30) as client:
                await client.request(action.method, render_template(action.url or "", context), content=render_template(action.body, context) if action.body else None)
            return HookRunResult()
        process = await asyncio.create_subprocess_exec("powershell", "-NoProfile", "-NonInteractive", "-Command", render_template(action.command or "", context), cwd=self._root)
        try: await asyncio.wait_for(process.wait(), timeout=action.timeout_seconds or 30)
        except TimeoutError:
            process.kill(); await process.wait(); raise TimeoutError("Hook 命令执行超时。")
        if process.returncode != 0: raise RuntimeError(f"Hook 命令退出码为 {process.returncode}。")
        return HookRunResult()
