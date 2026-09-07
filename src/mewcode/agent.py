"""MewCode 的异步 Agent Loop。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from mewcode.cancellation import Cancellation
from mewcode.conversation import Conversation
from mewcode.providers.base import Provider, ProviderError, StreamCancelled, Usage
from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.executor import ApprovalCallback, ToolExecutor
from mewcode.tools.registry import ToolRegistry


class RunMode(str, Enum):
    FULL = "full"
    PLAN = "plan"


class StopReason(str, Enum):
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    CANCELLED = "cancelled"
    UNKNOWN_TOOL_LIMIT = "unknown_tool_limit"
    STREAM_ERROR = "stream_error"


class ProgressPhase(str, Enum):
    MODEL = "model"
    TOOLS = "tools"
    STOPPED = "stopped"


@dataclass(frozen=True)
class TextDelta:
    iteration: int
    content: str


@dataclass(frozen=True)
class ThinkingDelta:
    iteration: int
    content: str


@dataclass(frozen=True)
class ToolCallStarted:
    iteration: int
    call: ToolCall


@dataclass(frozen=True)
class ToolResultReady:
    iteration: int
    result: ToolResult


@dataclass(frozen=True)
class UsageUpdated:
    iteration: int
    current: Usage
    total: Usage


@dataclass(frozen=True)
class ProgressUpdated:
    iteration: int
    max_iterations: int
    phase: ProgressPhase
    detail: str


@dataclass(frozen=True)
class AgentFinished:
    reason: StopReason
    text: str
    usage: Usage
    error: str | None = None


AgentEvent = (
    TextDelta
    | ThinkingDelta
    | ToolCallStarted
    | ToolResultReady
    | UsageUpdated
    | ProgressUpdated
    | AgentFinished
)


@dataclass
class _CollectedResponse:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = Usage()


FULL_INSTRUCTIONS = (
    "你是 MewCode 的执行 Agent。持续使用可用工具观察、操作和验证，直到用户任务真正完成；"
    "只在不再需要工具时给出最终回复。"
)
PLAN_INSTRUCTIONS = (
    "你处于只读计划模式。只能使用提供的只读工具探索上下文，最终输出可执行计划；"
    "不得修改文件、执行命令或声称已经实施计划。"
)


class Agent:
    """驱动模型、工具和历史，向界面只暴露异步事件。"""

    def __init__(
        self,
        provider: Provider,
        conversation: Conversation,
        registry: ToolRegistry,
        max_iterations: int = 10,
    ) -> None:
        if max_iterations <= 0:
            raise ValueError("max_iterations 必须是正整数。")
        self._provider = provider
        self._conversation = conversation
        self._registry = registry
        self._executor = ToolExecutor(registry)
        self._max_iterations = max_iterations

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    async def run(
        self,
        text: str,
        mode: RunMode,
        cancellation: Cancellation,
        approve_command: ApprovalCallback | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self._conversation.append_user(text)
        total = Usage()
        visible_parts: list[str] = []
        unknown_rounds = 0

        for iteration in range(1, self._max_iterations + 1):
            if cancellation.is_cancelled:
                if iteration == 1:
                    self._conversation.discard_last_plain_user()
                async for event in self._finish(StopReason.CANCELLED, visible_parts, total, iteration, "用户已取消。"):
                    yield event
                return

            yield ProgressUpdated(
                iteration, self._max_iterations, ProgressPhase.MODEL,
                f"第 {iteration}/{self._max_iterations} 轮：正在请求模型",
            )
            response = _CollectedResponse()
            try:
                tools = self._registry.read_only_definitions if mode is RunMode.PLAN else self._registry.definitions
                instructions = PLAN_INSTRUCTIONS if mode is RunMode.PLAN else FULL_INSTRUCTIONS
                async for event in self._provider.stream(
                    self._conversation.messages, cancellation, tools, instructions
                ):
                    if cancellation.is_cancelled:
                        raise StreamCancelled()
                    if event.kind == "text":
                        response.text += event.content
                        yield TextDelta(iteration, event.content)
                    elif event.kind == "thinking":
                        yield ThinkingDelta(iteration, event.content)
                    elif event.kind == "tool_call" and event.tool_call is not None:
                        response.calls.append(event.tool_call)
                        yield ToolCallStarted(iteration, event.tool_call)
                    elif event.kind == "usage" and event.usage is not None:
                        response.usage = event.usage
                        yield UsageUpdated(iteration, event.usage, _add_usage(total, event.usage))
                if cancellation.is_cancelled:
                    raise StreamCancelled()
            except (KeyboardInterrupt, StreamCancelled):
                total = _add_usage(total, response.usage)
                if response.text:
                    self._conversation.append_partial_assistant(response.text)
                    visible_parts.append(response.text)
                elif iteration == 1:
                    self._conversation.discard_last_plain_user()
                async for event in self._finish(StopReason.CANCELLED, visible_parts, total, iteration, "用户已取消。"):
                    yield event
                return
            except Exception as error:
                total = _add_usage(total, response.usage)
                if response.text:
                    self._conversation.append_partial_assistant(response.text)
                    visible_parts.append(response.text)
                elif iteration == 1:
                    self._conversation.discard_last_plain_user()
                message = str(error) if isinstance(error, ProviderError) else f"请求处理异常：{error}"
                async for event in self._finish(StopReason.STREAM_ERROR, visible_parts, total, iteration, message, message):
                    yield event
                return

            total = _add_usage(total, response.usage)
            if response.text:
                visible_parts.append(response.text)
            self._conversation.append_assistant(response.text, response.calls)

            if not response.calls:
                async for event in self._finish(StopReason.COMPLETED, visible_parts, total, iteration, "任务已完成。"):
                    yield event
                return

            if iteration == self._max_iterations:
                results = [
                    ToolResult(call.id, call.name, False, "达到 Agent 迭代上限，工具未执行。", error_code="iteration_limit")
                    for call in response.calls
                ]
                self._conversation.append_tool_results(results)
                for result in results:
                    yield ToolResultReady(iteration, result)
                async for event in self._finish(
                    StopReason.ITERATION_LIMIT,
                    visible_parts,
                    total,
                    iteration,
                    "达到 Agent 迭代上限。",
                    "达到 Agent 迭代上限。",
                ):
                    yield event
                return

            yield ProgressUpdated(
                iteration, self._max_iterations, ProgressPhase.TOOLS,
                f"第 {iteration}/{self._max_iterations} 轮：正在执行 {len(response.calls)} 个工具",
            )
            allowed_names = frozenset(definition.name for definition in tools)
            results = await self._executor.execute_many(
                response.calls, cancellation, approve_command, allowed_names
            )
            self._conversation.append_tool_results(results)
            for result in results:
                yield ToolResultReady(iteration, result)

            if cancellation.is_cancelled:
                async for event in self._finish(StopReason.CANCELLED, visible_parts, total, iteration, "用户已取消。"):
                    yield event
                return

            known_call_present = any(self._registry.get(call.name) is not None for call in response.calls)
            unknown_rounds = 0 if known_call_present else unknown_rounds + 1
            if unknown_rounds >= 2:
                async for event in self._finish(
                    StopReason.UNKNOWN_TOOL_LIMIT,
                    visible_parts,
                    total,
                    iteration,
                    "模型连续两轮请求未知工具，已停止。",
                    "模型连续两轮请求未知工具，已停止。",
                ):
                    yield event
                return

    async def _finish(
        self,
        reason: StopReason,
        visible_parts: list[str],
        usage: Usage,
        iteration: int,
        detail: str,
        error: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        yield ProgressUpdated(iteration, self._max_iterations, ProgressPhase.STOPPED, detail)
        yield AgentFinished(reason, "".join(visible_parts), usage, error)


def _add_usage(first: Usage, second: Usage) -> Usage:
    return Usage(
        first.input_tokens + second.input_tokens,
        first.output_tokens + second.output_tokens,
        first.thinking_tokens + second.thinking_tokens,
    )
