"""MewCode 的异步 Agent Loop。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from mewcode.cancellation import Cancellation
from mewcode.conversation import Conversation
from mewcode.permissions import (
    ApprovalCallback,
    PermissionManager,
    PermissionMode,
    SensitiveDataRedactor,
    classify_authorization,
)
from mewcode.prompting import RuntimeContext, SystemPromptBuilder
from mewcode.providers.base import Provider, ProviderError, StreamCancelled, Usage
from mewcode.tools.base import ToolCall, ToolResult
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import ToolRegistry
from mewcode.workflow import ToolWorkflow


class StopReason(str, Enum):
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    CANCELLED = "cancelled"
    UNKNOWN_TOOL_LIMIT = "unknown_tool_limit"
    STREAM_ERROR = "stream_error"
    VERIFICATION_REQUIRED = "verification_required"


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


class Agent:
    """驱动模型、工具和历史，向界面只暴露异步事件。"""

    def __init__(
        self,
        provider: Provider,
        conversation: Conversation,
        registry: ToolRegistry,
        max_iterations: int = 10,
        prompt_builder: SystemPromptBuilder | None = None,
        permissions: PermissionManager | None = None,
    ) -> None:
        if max_iterations <= 0:
            raise ValueError("max_iterations 必须是正整数。")
        self._provider = provider
        self._conversation = conversation
        self._registry = registry
        self._permissions = permissions or PermissionManager(registry.context.root)
        self._executor = ToolExecutor(registry, self._permissions)
        self._max_iterations = max_iterations
        self._prompt_builder = prompt_builder or SystemPromptBuilder()
        self._redactor = SensitiveDataRedactor()

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def permissions(self) -> PermissionManager:
        return self._permissions

    async def run(
        self,
        text: str,
        cancellation: Cancellation,
        approve: ApprovalCallback | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self._conversation.append_user(text)
        total = Usage()
        visible_parts: list[str] = []
        unknown_rounds = 0
        verification_misses = 0
        is_plan = self._permissions.mode is PermissionMode.PLAN
        runtime_mode = "plan" if is_plan else "full"
        authorization = classify_authorization(text, runtime_mode)
        workflow = ToolWorkflow(self._registry.context.root)

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
            requires_verification = bool(workflow.pending_verifications)
            requires_guarded_response = requires_verification
            try:
                tools = self._registry.read_only_definitions if is_plan else self._registry.definitions
                request = self._prompt_builder.build(
                    RuntimeContext(
                        self._registry.context.root,
                        runtime_mode,
                        iteration,
                        authorization.value,
                        tuple(sorted(workflow.pending_verifications)),
                        False,
                    ),
                    tools,
                    self._conversation.messages,
                )
                async for event in self._provider.stream(request, cancellation):
                    if cancellation.is_cancelled:
                        raise StreamCancelled()
                    if event.kind == "text":
                        content = self._redactor.redact(event.content)
                        response.text += content
                        if not requires_guarded_response:
                            yield TextDelta(iteration, content)
                    elif event.kind == "thinking":
                        yield ThinkingDelta(iteration, self._redactor.redact(event.content))
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
            if response.text and not requires_guarded_response:
                visible_parts.append(response.text)

            if not response.calls:
                if workflow.pending_verifications:
                    if verification_misses >= 1:
                        async for event in self._finish(
                            StopReason.VERIFICATION_REQUIRED,
                            visible_parts,
                            total,
                            iteration,
                            "修改结果尚未验证，任务未完成。",
                            "修改结果尚未验证；请读取待验证目标后再报告完成。",
                        ):
                            yield event
                        return
                    verification_misses += 1
                    continue
                self._conversation.append_assistant(response.text)
                async for event in self._finish(StopReason.COMPLETED, visible_parts, total, iteration, "任务已完成。"):
                    yield event
                return

            self._conversation.append_assistant(response.text, response.calls)

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
            results = await self._executor.execute_many(
                response.calls, cancellation, authorization, workflow, approve
            )
            results = [self._redactor.redact_result(result) for result in results]
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
    cache = first.cache
    if second.cache.available:
        cache = type(cache)(
            available=True,
            read_input_tokens=first.cache.read_input_tokens + second.cache.read_input_tokens,
            write_input_tokens=first.cache.write_input_tokens + second.cache.write_input_tokens,
        )
    return Usage(
        first.input_tokens + second.input_tokens,
        first.output_tokens + second.output_tokens,
        first.thinking_tokens + second.thinking_tokens,
        cache,
    )
