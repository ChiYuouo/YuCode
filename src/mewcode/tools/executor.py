"""工具调用的异步分批、确认与统一错误包装。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

from mewcode.cancellation import Cancellation
from mewcode.policy import ExecutionPolicy
from mewcode.tools.base import ToolCall, ToolResult, ToolSafety
from mewcode.tools.registry import ToolRegistry

ApprovalCallback = Callable[[ToolCall], Awaitable[bool]]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        call: ToolCall,
        cancellation: Cancellation,
        approve_command: ApprovalCallback | None = None,
        allowed_names: frozenset[str] | None = None,
        policy: ExecutionPolicy | None = None,
    ) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return _failure(call, f"未知工具：{call.name}。", "unknown_tool")
        if allowed_names is not None and call.name not in allowed_names:
            return _failure(call, f"当前模式不允许使用工具：{call.name}。", "tool_not_available")
        if not isinstance(call.arguments, Mapping):
            return _failure(call, "工具参数必须是对象。", "invalid_arguments")
        if policy is not None:
            rejected = policy.preflight(call)
            if rejected is not None:
                policy.record(rejected)
                return rejected
        if call.name == "run_command":
            if cancellation.is_cancelled:
                return _failure(call, "用户已取消，命令未执行。", "cancelled")
            if approve_command is None or not await approve_command(call):
                code = "cancelled" if cancellation.is_cancelled else "user_rejected"
                summary = "用户已取消，命令未执行。" if cancellation.is_cancelled else "用户拒绝执行命令。"
                return _failure(call, summary, code)
        try:
            result = await tool.execute(call.arguments, self._registry.context, call.id, cancellation)
        except Exception as error:  # 工具边界必须把所有意外错误转为模型可处理结果。
            result = _failure(call, f"工具执行异常：{error}", "tool_exception")
        if policy is not None:
            policy.record(result)
        return result

    async def execute_many(
        self,
        calls: Sequence[ToolCall],
        cancellation: Cancellation,
        approve_command: ApprovalCallback | None = None,
        allowed_names: frozenset[str] | None = None,
        policy: ExecutionPolicy | None = None,
    ) -> list[ToolResult]:
        """按顺序屏障执行调用，并始终返回与输入同序的结果。"""
        results: list[ToolResult | None] = [None] * len(calls)
        for indexes in self._batches(calls, allowed_names):
            if cancellation.is_cancelled:
                break
            batch = await asyncio.gather(
                *(
                    self.execute(calls[index], cancellation, approve_command, allowed_names, policy)
                    for index in indexes
                )
            )
            for index, result in zip(indexes, batch, strict=True):
                results[index] = result
        for index, result in enumerate(results):
            if result is None:
                results[index] = _failure(calls[index], "用户已取消，工具未执行。", "cancelled")
        return [result for result in results if result is not None]

    def _batches(
        self, calls: Sequence[ToolCall], allowed_names: frozenset[str] | None = None
    ) -> list[list[int]]:
        batches: list[list[int]] = []
        read_batch: list[int] = []
        for index, call in enumerate(calls):
            tool = self._registry.get(call.name)
            allowed = allowed_names is None or call.name in allowed_names
            if tool is not None and allowed and tool.safety is ToolSafety.READ_ONLY:
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
