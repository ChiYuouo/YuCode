"""供应商无关的会话上下文外置与压缩。"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from yucode.cancellation import Cancellation
from yucode.config import ContextConfig
from yucode.conversation import Conversation
from yucode.prompting import ModelRequest
from yucode.providers.base import (
    Message,
    Provider,
    StreamEvent,
    StreamCancelled,
    TextContent,
    ToolCallContent,
    ToolResultContent,
    Usage,
)
from yucode.tools.base import ToolDefinition, ToolResult


SINGLE_RESULT_LIMIT = 50_000
MESSAGE_RESULTS_LIMIT = 200_000
AUTO_RESERVE_TOKENS = 13_000
MANUAL_RESERVE_TOKENS = 3_000
RECENT_TOKENS = 10_000
RECENT_MESSAGES = 5
CHARS_PER_TOKEN = 4
SUMMARY_CACHE_KEY = "yucode-context-summary-v1"


class ContextAction(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class ContextResult:
    action: ContextAction
    status: Literal["unchanged", "offloaded", "compacting", "compacted", "failed", "circuit_open"]
    detail: str = ""
    offloaded_count: int = 0
    released_characters: int = 0
    before_tokens: int | None = None
    after_tokens: int | None = None


class TokenBudgetTracker:
    """以最近 API usage 为锚点，仅估算历史字符变化。"""

    def __init__(self) -> None:
        self._input_tokens: int | None = None
        self._history_chars = 0

    def record_usage(self, history: Sequence[Message], input_tokens: int) -> None:
        if input_tokens <= 0:
            return
        self._input_tokens = input_tokens
        self._history_chars = _history_chars(history)

    def estimate(self, history: Sequence[Message]) -> int:
        current_chars = _history_chars(history)
        if self._input_tokens is None:
            return _chars_to_tokens(current_chars)
        return max(0, self._input_tokens + _chars_to_tokens(current_chars - self._history_chars))

    def reset(self) -> None:
        self._input_tokens = None
        self._history_chars = 0


class ContextArtifactStore:
    """保存可由 read_file 重读的本会话工具输出。"""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()
        self._directory = self._workspace_root / ".yucode" / "context"
        self._sequence = 0
        self.reset_session()

    def reset_session(self) -> None:
        if self._directory.exists():
            shutil.rmtree(self._directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._sequence = 0

    def save_result(self, result: ToolResult) -> str:
        self._sequence += 1
        path = self._directory / f"tool-result-{self._sequence:04d}.txt"
        path.write_text(result.content, encoding="utf-8")
        return path.relative_to(self._workspace_root).as_posix()


class ContextManager:
    """在请求前按固定顺序外置工具结果并压缩历史。"""

    def __init__(
        self,
        conversation: Conversation,
        provider: Provider,
        workspace_root: Path,
        config: ContextConfig = ContextConfig(),
    ) -> None:
        self._conversation = conversation
        self._provider = provider
        self._config = config
        self._store = ContextArtifactStore(workspace_root)
        self._budget = TokenBudgetTracker()
        self._automatic_failures = 0
        self._automatic_circuit_open = False

    def record_model_usage(self, usage: Usage) -> None:
        self._budget.record_usage(self._conversation.messages, usage.input_tokens)

    def estimated_tokens(self) -> int:
        """返回当前历史的估算 Token，供状态命令显示。"""
        return self._budget.estimate(self._conversation.messages)

    def reset_conversation_state(self) -> None:
        """切换会话后重置估算与自动压缩状态，不删除共享外置结果。"""
        self._budget.reset()
        self._automatic_failures = 0
        self._automatic_circuit_open = False

    async def prepare_request(
        self, tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> AsyncIterator[ContextResult]:
        offloaded = self._offload_large_results()
        if offloaded is not None:
            yield offloaded
            if offloaded.status == "failed":
                return
        if self._budget.estimate(self._conversation.messages) >= self._auto_limit:
            async for result in self._compact(ContextAction.AUTO, tools, cancellation):
                yield result

    async def compact_manually(
        self, tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> AsyncIterator[ContextResult]:
        offloaded = self._offload_large_results()
        if offloaded is not None:
            yield offloaded
            if offloaded.status == "failed":
                return
        async for result in self._compact(ContextAction.MANUAL, tools, cancellation):
            yield result

    async def compact_emergency(
        self, tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> AsyncIterator[ContextResult]:
        offloaded = self._offload_large_results()
        if offloaded is not None:
            yield offloaded
            if offloaded.status == "failed":
                return
        async for result in self._compact(ContextAction.EMERGENCY, tools, cancellation):
            yield result

    async def prepare_recovered_history(
        self, tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> AsyncIterator[ContextResult]:
        """恢复旧会话时至多压缩一次，避免进入普通请求后的重试循环。"""
        offloaded = self._offload_large_results()
        if offloaded is not None:
            yield offloaded
            if offloaded.status == "failed":
                return
        if self._budget.estimate(self._conversation.messages) < self._auto_limit:
            return
        async for result in self._compact(ContextAction.AUTO, tools, cancellation):
            yield result
            if result.status in {"failed", "circuit_open"}:
                return
        if self._budget.estimate(self._conversation.messages) >= self._auto_limit:
            yield ContextResult(ContextAction.AUTO, "failed", "恢复会话压缩后仍超过上下文安全线。")

    @property
    def _auto_limit(self) -> int:
        return self._config.window_tokens - AUTO_RESERVE_TOKENS

    def _offload_large_results(self) -> ContextResult | None:
        original = self._conversation.messages
        before_characters = _history_chars(original)
        changed = False
        count = 0
        rewritten: list[Message] = []
        try:
            for message in original:
                if isinstance(message.content, str):
                    rewritten.append(message)
                    continue
                blocks = list(message.blocks)
                result_indexes = [
                    index for index, block in enumerate(blocks)
                    if isinstance(block, ToolResultContent) and len(block.result.content) > 0
                ]
                selected = {
                    index for index in result_indexes
                    if len(blocks[index].result.content) > SINGLE_RESULT_LIMIT
                }
                remaining = sum(len(blocks[index].result.content) for index in result_indexes if index not in selected)
                for index in sorted(result_indexes, key=lambda item: len(blocks[item].result.content), reverse=True):
                    if remaining <= MESSAGE_RESULTS_LIMIT:
                        break
                    if index not in selected:
                        selected.add(index)
                        remaining -= len(blocks[index].result.content)
                for index in selected:
                    result = blocks[index].result
                    location = self._store.save_result(result)
                    blocks[index] = ToolResultContent(_externalized_result(result, location))
                    changed = True
                    count += 1
                rewritten.append(Message(message.role, tuple(blocks)))
        except OSError as error:
            return ContextResult(ContextAction.AUTO, "failed", f"上下文缓存保存失败：{error}")
        if not changed:
            return None
        self._conversation.replace_messages(rewritten)
        released = max(0, before_characters - _history_chars(self._conversation.messages))
        return ContextResult(
            ContextAction.AUTO,
            "offloaded",
            offloaded_count=count,
            released_characters=released,
        )

    async def _compact(
        self, action: ContextAction, tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> AsyncIterator[ContextResult]:
        if action is not ContextAction.MANUAL and self._automatic_circuit_open:
            yield ContextResult(action, "circuit_open", "自动上下文压缩已熔断；可使用 /compact 手动尝试。")
            return
        before_tokens = self._budget.estimate(self._conversation.messages)
        source, retained = _split_history(self._conversation.messages)
        if not source:
            if action is ContextAction.MANUAL:
                yield ContextResult(action, "unchanged", "没有可压缩的较早历史。")
            return
        yield ContextResult(action, "compacting", before_tokens=before_tokens)
        try:
            summary = await self._create_summary(source, tools, cancellation)
        except StreamCancelled:
            raise
        except Exception as error:
            if action is not ContextAction.MANUAL:
                self._automatic_failures += 1
                if self._automatic_failures >= 3:
                    self._automatic_circuit_open = True
                    yield ContextResult(action, "circuit_open", "自动摘要连续失败 3 次，已熔断；可使用 /compact 手动尝试。")
                    return
            yield ContextResult(action, "failed", f"上下文摘要失败：{error}")
            return
        if action is not ContextAction.MANUAL:
            self._automatic_failures = 0
        self._conversation.replace_messages(
            (
                Message("assistant", summary),
                Message("assistant", _BOUNDARY_MESSAGE),
                *retained,
            )
        )
        yield ContextResult(
            action,
            "compacted",
            before_tokens=before_tokens,
            after_tokens=self._budget.estimate(self._conversation.messages),
        )

    async def _create_summary(
        self, source: Sequence[Message], tools: Sequence[ToolDefinition], cancellation: Cancellation
    ) -> str:
        request = ModelRequest(
            history=(*source, Message("user", _summary_instruction(tools))),
            stable_instructions=_SUMMARY_SYSTEM_PROMPT,
            runtime_messages=(),
            tools=(),
            prompt_cache_key=SUMMARY_CACHE_KEY,
        )
        output = ""
        async for event in self._provider.stream(request, cancellation):
            if event.kind == "text":
                output += event.content
            elif event.kind == "tool_call":
                raise ValueError("摘要模型尝试调用工具。")
        match = re.search(r"<structured-summary>\s*(.*?)\s*</structured-summary>", output, re.DOTALL)
        draft = re.search(r"<analysis-draft>\s*.*?\s*</analysis-draft>", output, re.DOTALL)
        if match is None or draft is None:
            raise ValueError("摘要未返回正式结构化内容。")
        generated = match.group(1).strip()
        required = (
            "当前任务目标", "已经完成的工作", "已执行验证及其结果", "当前代码与文件状态",
            "重要决定与约束", "最近读取的文件快照", "当前可用工具与外置资料位置", "未完成事项与推荐下一步",
        )
        if any(f"## {heading}" not in generated for heading in required):
            raise ValueError("摘要缺少固定结构。")
        return f"## 原始用户要求\n{_raw_user_messages(source)}\n\n{generated}"


def _externalized_result(result: ToolResult, location: str) -> ToolResult:
    preview = result.content[:500]
    content = (
        f"完整工具结果已外置到：{location}\n"
        f"预览：{preview}\n"
        "需要精确内容时，必须使用 read_file 重新读取该路径；不得根据预览臆测细节。"
    )
    return ToolResult(result.call_id, result.name, result.success, result.summary, content, result.error_code, result.target)


def _split_history(messages: Sequence[Message]) -> tuple[tuple[Message, ...], tuple[Message, ...]]:
    retained: list[Message] = []
    tokens = 0
    for message in reversed(messages):
        retained.append(message)
        tokens += _chars_to_tokens(_message_chars(message))
        if len(retained) >= RECENT_MESSAGES and tokens >= RECENT_TOKENS:
            break
    retained.reverse()
    return tuple(messages[:len(messages) - len(retained)]), tuple(retained)


def _raw_user_messages(messages: Sequence[Message]) -> str:
    values = [block.text for message in messages if message.role == "user" for block in message.blocks if isinstance(block, TextContent)]
    return "\n\n".join(values) if values else "（无较早用户原始消息）"


def _history_chars(messages: Sequence[Message]) -> int:
    return sum(_message_chars(message) for message in messages)


def _message_chars(message: Message) -> int:
    parts: list[str] = []
    for block in message.blocks:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ToolResultContent):
            parts.append(block.result.for_model())
        elif isinstance(block, ToolCallContent):
            parts.append(block.call.name + json.dumps(block.call.arguments, ensure_ascii=False, sort_keys=True))
    return len("\n".join(parts))


def _chars_to_tokens(characters: int) -> int:
    return characters // CHARS_PER_TOKEN


def _summary_instruction(tools: Sequence[ToolDefinition]) -> str:
    names = ", ".join(tool.name for tool in tools) or "无"
    return (
        "请压缩上方较早会话历史。不得调用工具。先在 <analysis-draft> 中完成事实核对草稿，"
        "再在 <structured-summary> 中输出正式摘要。草稿会被丢弃。正式摘要只能包含历史可证实的事实，"
        "不得补全或猜测代码。正式摘要必须依次包含以下二级标题：\n"
        "## 当前任务目标\n## 已经完成的工作\n## 已执行验证及其结果\n## 当前代码与文件状态\n"
        "## 重要决定与约束\n## 最近读取的文件快照\n## 当前可用工具与外置资料位置\n## 未完成事项与推荐下一步\n"
        f"当前可用工具名：{names}。原始用户要求由系统逐字保留，请勿改写。"
    )


_SUMMARY_SYSTEM_PROMPT = "你是 YuCode 的上下文压缩器。只完成摘要任务，绝不调用工具。"
_BOUNDARY_MESSAGE = (
    "<context-boundary>以上内容是上下文摘要。需要文件或工具结果的精确细节时，必须重新读取原始文件或外置缓存；"
    "不得根据摘要臆测代码或内容。</context-boundary>"
)
