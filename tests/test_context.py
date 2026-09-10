from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from yucode.cancellation import Cancellation
from yucode.config import ContextConfig
from yucode.context import ContextAction, ContextManager, TokenBudgetTracker
from yucode.conversation import Conversation
from yucode.providers.base import ProviderError, StreamEvent
from yucode.tools.base import ToolResult


class SummaryProvider:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.requests = []

    async def stream(self, request, _cancellation) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        yield StreamEvent("text", self.response)


def run(stream):
    async def collect():
        return [result async for result in stream]

    return asyncio.run(collect())


def summary() -> str:
    return """<analysis-draft>只供丢弃</analysis-draft>
<structured-summary>
## 当前任务目标
目标
## 已经完成的工作
无
## 已执行验证及其结果
无
## 当前代码与文件状态
无
## 重要决定与约束
无
## 最近读取的文件快照
无
## 当前可用工具与外置资料位置
无
## 未完成事项与推荐下一步
继续
</structured-summary>"""


def test_offloads_single_large_result_to_readable_cache(tmp_path: Path) -> None:
    conversation = Conversation()
    content = "x" * 50_001
    conversation.append_tool_results([ToolResult("1", "read_file", True, "已读取", content, target="a.txt")])
    manager = ContextManager(conversation, SummaryProvider(summary()), tmp_path)

    results = run(manager.prepare_request((), Cancellation()))

    assert results[0].status == "offloaded"
    stored = tmp_path / ".yucode" / "context" / "tool-result-0001.txt"
    assert stored.read_text(encoding="utf-8") == content
    result = conversation.messages[0].blocks[0].result
    assert "read_file" in result.for_model() and ".yucode/context/tool-result-0001.txt" in result.content
    assert results[0].released_characters > 0


def test_offloads_largest_results_until_message_total_is_limited(tmp_path: Path) -> None:
    conversation = Conversation()
    values = ["a" * 49_000, "b" * 48_000, "c" * 47_000, "d" * 46_000, "e" * 45_000, "f" * 44_000]
    conversation.append_tool_results([ToolResult(str(index), "tool", True, "ok", value) for index, value in enumerate(values)])
    manager = ContextManager(conversation, SummaryProvider(summary()), tmp_path)

    results = run(manager.prepare_request((), Cancellation()))

    assert results[0].offloaded_count == 2
    blocks = conversation.messages[0].blocks
    assert ".yucode/context/" in blocks[0].result.content
    assert ".yucode/context/" in blocks[1].result.content
    assert blocks[2].result.content == values[2]


def test_budget_uses_usage_anchor_and_history_delta() -> None:
    conversation = Conversation()
    conversation.append_user("abcd")
    tracker = TokenBudgetTracker()
    tracker.record_usage(conversation.messages, 100)
    conversation.append_user("efgh")

    assert tracker.estimate(conversation.messages) == 101


def test_new_context_manager_cleans_previous_session_cache(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_tool_results([ToolResult("1", "tool", True, "ok", "x" * 50_001)])
    first = ContextManager(conversation, SummaryProvider(summary()), tmp_path)
    run(first.prepare_request((), Cancellation()))
    assert any((tmp_path / ".yucode" / "context").iterdir())

    ContextManager(Conversation(), SummaryProvider(summary()), tmp_path)

    assert list((tmp_path / ".yucode" / "context").iterdir()) == []


def test_manual_compaction_keeps_recent_messages_and_drops_draft(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_user("最早原始用户要求" + "y" * 8_000)
    conversation.append_assistant("较早的执行记录" + "z" * 8_000)
    for index in range(5):
        conversation.append_assistant(f"近期 {index}" + "x" * 8_000)
    provider = SummaryProvider(summary())
    manager = ContextManager(conversation, provider, tmp_path)

    results = run(manager.compact_manually((), Cancellation()))
    result = results[-1]

    assert result.status == "compacted"
    assert [item.status for item in results] == ["compacting", "compacted"]
    assert result.before_tokens is not None and result.after_tokens is not None
    assert result.after_tokens < result.before_tokens
    assert provider.requests[0].tools == ()
    history = conversation.messages
    assert "最早原始用户要求" in history[0].content
    assert "只供丢弃" not in str(history)
    assert "context-boundary" in history[1].content
    assert history[0].content.count("## ") == 9
    assert len(history[2:]) == 5


def test_automatic_compaction_uses_configured_window(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_user("较早要求")
    for index in range(5):
        conversation.append_assistant(f"近期 {index}" + "x" * 8_000)
    manager = ContextManager(conversation, SummaryProvider(summary()), tmp_path, ContextConfig(14_000))

    results = run(manager.prepare_request((), Cancellation()))

    assert results[-1].action is ContextAction.AUTO and results[-1].status == "compacted"


def test_small_window_first_message_is_silent_without_summary_request(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_user("首条普通消息")
    provider = SummaryProvider(summary())
    manager = ContextManager(conversation, provider, tmp_path, ContextConfig(13_001))

    results = run(manager.prepare_request((), Cancellation()))

    assert results == []
    assert provider.requests == []


def test_automatic_failures_open_circuit_but_manual_still_attempts(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_user("早期")
    for index in range(5):
        conversation.append_assistant(f"近期 {index}" + "x" * 8_000)
    manager = ContextManager(conversation, SummaryProvider(ProviderError("失败")), tmp_path)

    outcomes = [run(manager.compact_emergency((), Cancellation()))[-1] for _ in range(3)]

    assert outcomes[-1].status == "circuit_open"
    manual = run(manager.compact_manually((), Cancellation()))[-1]
    assert manual.action is ContextAction.MANUAL and manual.status == "failed"


def test_recovered_history_compacts_once_then_reports_if_still_over_limit(tmp_path: Path) -> None:
    conversation = Conversation()
    conversation.append_user("较早要求")
    for index in range(5):
        conversation.append_assistant(f"近期 {index}" + "x" * 8_000)
    provider = SummaryProvider(summary())
    manager = ContextManager(conversation, provider, tmp_path, ContextConfig(14_000))

    results = run(manager.prepare_recovered_history((), Cancellation()))

    assert [result.status for result in results] == ["compacting", "compacted", "failed"]
    assert len(provider.requests) == 1
