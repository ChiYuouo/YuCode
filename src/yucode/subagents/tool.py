"""提供给主 Agent 的固定子 Agent 工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from yucode.cancellation import Cancellation
from yucode.subagents.models import AgentIsolation, SubagentKind, SubagentRequest
from yucode.subagents.service import SubagentService
from yucode.tools.base import ToolContext, ToolDefinition, ToolResult, ToolSafety


class AgentTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition("Agent", "委派独立子 Agent 完成探索、规划或一般任务。用户明确要求隔离执行、使用 Worktree 或不影响主目录时，必须传 isolation: worktree。", {
            "type": "object", "properties": {
                "subagent_type": {"type": "string", "description": "角色名称，或 fork。"},
                "prompt": {"type": "string", "description": "交给子 Agent 的明确任务。"},
                "run_in_background": {"type": "boolean", "description": "是否立即转入后台。"},
                "isolation": {"type": "string", "enum": ["none", "worktree"], "description": "本次任务的隔离模式；省略时使用角色默认值。"},
            }, "required": ["subagent_type", "prompt"], "additionalProperties": False,
        })

    @property
    def safety(self) -> ToolSafety: return ToolSafety.SIDE_EFFECT

    def __init__(self, service: SubagentService) -> None: self._service = service

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        kind_value = arguments.get("subagent_type"); prompt = arguments.get("prompt"); background = arguments.get("run_in_background", False); isolation_value = arguments.get("isolation")
        if not isinstance(kind_value, str) or not kind_value.strip(): return ToolResult(call_id, "Agent", False, "subagent_type 必须是非空字符串。", error_code="invalid_arguments")
        if not isinstance(prompt, str) or not prompt.strip(): return ToolResult(call_id, "Agent", False, "prompt 必须是非空字符串。", error_code="invalid_arguments")
        if not isinstance(background, bool): return ToolResult(call_id, "Agent", False, "run_in_background 必须是布尔值。", error_code="invalid_arguments")
        if isolation_value is not None and not isinstance(isolation_value, str):
            return ToolResult(call_id, "Agent", False, "isolation 必须是 none 或 worktree。", error_code="invalid_arguments")
        try:
            isolation = AgentIsolation(isolation_value) if isolation_value is not None else None
        except ValueError:
            return ToolResult(call_id, "Agent", False, "isolation 只能是 none 或 worktree。", error_code="invalid_arguments")
        kind = SubagentKind.FORK if kind_value.strip() == "fork" else SubagentKind.DEFINITION
        if kind is SubagentKind.FORK and isolation is not None:
            return ToolResult(call_id, "Agent", False, "Fork 子 Agent 不支持 isolation；请选择定义式角色。", error_code="invalid_isolation")
        request = SubagentRequest(kind, prompt.strip(), None if kind is SubagentKind.FORK else kind_value.strip(), background, isolation)
        return await self._service.delegate(request, call_id, context.approval)
