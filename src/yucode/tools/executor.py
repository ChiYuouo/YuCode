"""工具调用的异步分批、确认与统一错误包装。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from yucode.cancellation import Cancellation
from yucode.permissions import ApprovalCallback as PermissionApprovalCallback
from yucode.permissions import PermissionManager, PermissionOutcome, TaskAuthorization
from yucode.tools.base import ToolCall, ToolCatalog, ToolContext, ToolResult, ToolSafety
from yucode.tools.registry import ToolRegistry
from yucode.workflow import ToolWorkflow

class ToolExecutor:
    def __init__(self, registry: ToolRegistry, permissions: PermissionManager | None = None) -> None:
        self._registry = registry
        self._permissions = permissions or PermissionManager(registry.context.root)

    async def execute(
        self,
        call: ToolCall,
        cancellation: Cancellation,
        authorization: TaskAuthorization,
        workflow: ToolWorkflow,
        approve: PermissionApprovalCallback | None = None,
        tools: ToolCatalog | None = None,
    ) -> ToolResult:
        catalog = tools or self._registry
        tool = catalog.get(call.name)
        if tool is None:
            return _failure(call, f"未知工具：{call.name}。", "unknown_tool")
        if not isinstance(call.arguments, Mapping):
            return _failure(call, "工具参数必须是对象。", "invalid_arguments")
        decision = self._permissions.evaluate(call, tool, authorization, workflow)
        if decision.outcome is PermissionOutcome.ASK:
            request = self._permissions.request_for(call)
            decision = await self._permissions.resolve_prompt(request, approve)
        if decision.outcome is PermissionOutcome.DENY:
            return _failure(call, decision.reason, decision.error_code or "permission_rejected")
        try:
            context = ToolContext(catalog.context.root, approve, authorization)
            result = await tool.execute(call.arguments, context, call.id, cancellation)
        except Exception as error:  # 工具边界必须把所有意外错误转为模型可处理结果。
            result = _failure(call, f"工具执行异常：{error}", "tool_exception")
        workflow.record(result)
        return result

    async def execute_many(
        self,
        calls: Sequence[ToolCall],
        cancellation: Cancellation,
        authorization: TaskAuthorization,
        workflow: ToolWorkflow,
        approve: PermissionApprovalCallback | None = None,
        tools: ToolCatalog | None = None,
    ) -> list[ToolResult]:
        """按顺序屏障执行调用，并始终返回与输入同序的结果。"""
        results: list[ToolResult | None] = [None] * len(calls)
        catalog = tools or self._registry
        for indexes in self._batches(calls, catalog):
            if cancellation.is_cancelled:
                break
            batch = await asyncio.gather(
                *(
                    self.execute(calls[index], cancellation, authorization, workflow, approve, catalog)
                    for index in indexes
                )
            )
            for index, result in zip(indexes, batch, strict=True):
                results[index] = result
        for index, result in enumerate(results):
            if result is None:
                results[index] = _failure(calls[index], "用户已取消，工具未执行。", "cancelled")
        return [result for result in results if result is not None]

    def _batches(self, calls: Sequence[ToolCall], catalog: ToolCatalog | None = None) -> list[list[int]]:
        batches: list[list[int]] = []
        read_batch: list[int] = []
        for index, call in enumerate(calls):
            tool = (catalog or self._registry).get(call.name)
            if tool is not None and tool.safety is ToolSafety.READ_ONLY:
                read_batch.append(index)
                continue
            if read_batch:
                batches.append(read_batch)
                read_batch = []
            batches.append([index])
        if read_batch:
            batches.append(read_batch)
        return batches


def _failure(call: ToolCall, summary: str, code: str) -> ToolResult:
    return ToolResult(call.id, call.name, False, summary, error_code=code)
