from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from yucode.agent import (
    Agent, AgentFinished, ProgressUpdated, StopReason, TextDelta,
    ToolResultReady, UsageUpdated,
)
from yucode.cancellation import Cancellation
from yucode.conversation import Conversation
from yucode.permissions import ApprovalChoice, PermissionMode
from yucode.providers.base import CacheUsage, ProviderError, StreamCancelled, StreamEvent, Usage
from yucode.tools.base import ToolCall
from yucode.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self, rounds: list[list[StreamEvent | Exception]]) -> None:
        self.rounds = rounds
        self.requests = []

    async def stream(self, request, cancellation) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        for item in self.rounds[len(self.requests) - 1]:
            await asyncio.sleep(0)
            if isinstance(item, Exception):
                raise item
            yield item


def collect(agent: Agent, text="任务", cancellation=None, approval=None):
    async def scenario():
        async def allow_once(_):
            return ApprovalChoice.ONCE

        return [event async for event in agent.run(text, cancellation or Cancellation(), approval or allow_once)]

    return asyncio.run(scenario())


def finished(events):
    matches = [event for event in events if isinstance(event, AgentFinished)]
    assert len(matches) == 1
    assert events[-1] is matches[0]
    return matches[0]


def test_streams_deltas_collects_text_and_usage_then_completes(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent("text", "你"), StreamEvent("text", "好"), StreamEvent("usage", usage=Usage(3, 2, 0))]])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    events = collect(agent)
    assert [event.content for event in events if isinstance(event, TextDelta)] == ["你", "好"]
    usage = next(event for event in events if isinstance(event, UsageUpdated))
    assert usage.current == Usage(3, 2, 0) and usage.total == Usage(3, 2, 0)
    assert finished(events) == AgentFinished(StopReason.COMPLETED, "你好", Usage(3, 2, 0))
    assert agent.conversation.messages[-1].blocks[0].text == "你好"


def test_runs_multiple_tool_rounds_without_user_prompting(tmp_path: Path) -> None:
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": "a.txt", "content": "ok"})), StreamEvent("usage", usage=Usage(1, 1))],
        [StreamEvent("tool_call", tool_call=ToolCall("2", "read_file", {"path": "a.txt"})), StreamEvent("usage", usage=Usage(2, 1))],
        [StreamEvent("text", "验证完成"), StreamEvent("usage", usage=Usage(3, 2))],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    events = collect(agent, "创建文件并验证")
    assert len(provider.requests) == 3
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "ok"
    assert [event.result.call_id for event in events if isinstance(event, ToolResultReady)] == ["1", "2"]
    assert finished(events).usage == Usage(6, 4, 0)


def test_permission_denial_returns_to_model_and_allows_safe_recovery(tmp_path: Path) -> None:
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "run_command", {"command": "git reset --hard"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("2", "write_file", {"path": "safe.txt", "content": "ok"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("3", "read_file", {"path": "safe.txt"}))],
        [StreamEvent("text", "已改用项目内文件并完成验证")],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))

    events = collect(agent, "创建安全文件并验证")

    results = [event.result for event in events if isinstance(event, ToolResultReady)]
    assert results[0].error_code == "dangerous_command"
    assert results[1].success and results[2].success
    assert (tmp_path / "safe.txt").read_text(encoding="utf-8") == "ok"
    assert len(provider.requests) == 4
    assert finished(events).reason is StopReason.COMPLETED


def test_plan_mode_only_exposes_read_tools_and_instruction(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent("text", "计划")]])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    agent.permissions.set_mode(PermissionMode.PLAN)
    result = finished(collect(agent, "分析项目"))
    assert result.reason is StopReason.COMPLETED
    assert {tool.name for tool in provider.requests[0].tools} == {"read_file", "find_files", "search_code"}
    assert "规划模式" in provider.requests[0].stable_instructions


def test_plan_mode_rejects_hallucinated_side_effect_tool(tmp_path: Path) -> None:
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": "forbidden.txt", "content": "x"}))],
        [StreamEvent("text", "无法写入，只提供计划")],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    agent.permissions.set_mode(PermissionMode.PLAN)

    events = collect(agent, "只规划")

    result = next(event.result for event in events if isinstance(event, ToolResultReady))
    assert result.error_code == "permission_plan"
    assert not (tmp_path / "forbidden.txt").exists()
    assert finished(events).reason is StopReason.COMPLETED


def test_iteration_limit_does_not_execute_last_calls(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": "never.txt", "content": "x"}))]])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path), max_iterations=1)
    events = collect(agent)
    assert finished(events).reason is StopReason.ITERATION_LIMIT
    result = next(event.result for event in events if isinstance(event, ToolResultReady))
    assert result.error_code == "iteration_limit"
    assert not (tmp_path / "never.txt").exists()


def test_two_unknown_rounds_stop_and_known_tool_resets_counter(tmp_path: Path) -> None:
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "missing", {}))],
        [StreamEvent("tool_call", tool_call=ToolCall("2", "read_file", {"path": "absent"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("3", "missing", {}))],
        [StreamEvent("tool_call", tool_call=ToolCall("4", "missing", {}))],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    result = finished(collect(agent))
    assert result.reason is StopReason.UNKNOWN_TOOL_LIMIT
    assert len(provider.requests) == 4


def test_provider_error_keeps_partial_text_and_discards_tool_call(tmp_path: Path) -> None:
    provider = FakeProvider([[
        StreamEvent("text", "部分"),
        StreamEvent("tool_call", tool_call=ToolCall("1", "read_file", {"path": "a"})),
        ProviderError("网络断开"),
    ]])
    conversation = Conversation()
    agent = Agent(provider, conversation, ToolRegistry(tmp_path))
    result = finished(collect(agent))
    assert result.reason is StopReason.STREAM_ERROR and result.text == "部分"
    assert conversation.messages[-1].content == "部分"
    assert "网络断开" in (result.error or "")


def test_cancelled_before_first_event_rolls_back_user_message(tmp_path: Path) -> None:
    cancellation = Cancellation()
    cancellation.cancel()
    conversation = Conversation()
    agent = Agent(FakeProvider([]), conversation, ToolRegistry(tmp_path))
    result = finished(collect(agent, cancellation=cancellation))
    assert result.reason is StopReason.CANCELLED
    assert conversation.messages == ()


def test_cancelled_during_model_stream_stops_without_next_round(tmp_path: Path) -> None:
    class WaitingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, _request, cancellation):
            self.calls += 1
            yield StreamEvent("text", "开始")
            await cancellation.wait()
            raise StreamCancelled()

    async def scenario():
        provider = WaitingProvider()
        cancellation = Cancellation()
        agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))

        async def cancel_soon():
            await asyncio.sleep(0.01)
            cancellation.cancel()

        asyncio.create_task(cancel_soon())
        events = [event async for event in agent.run("任务", cancellation)]
        return provider, events

    provider, events = asyncio.run(scenario())
    assert provider.calls == 1
    assert finished(events).reason is StopReason.CANCELLED


def test_progress_identifies_model_tools_and_stop(tmp_path: Path) -> None:
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "read_file", {"path": "missing"}))],
        [StreamEvent("text", "结束")],
    ])
    events = collect(Agent(provider, Conversation(), ToolRegistry(tmp_path)))
    phases = [event.phase.value for event in events if isinstance(event, ProgressUpdated)]
    assert phases == ["model", "tools", "model", "stopped"]


def test_agent_keeps_runtime_messages_out_of_history_and_accumulates_cache(tmp_path: Path) -> None:
    provider = FakeProvider([
        [
            StreamEvent("tool_call", tool_call=ToolCall("1", "read_file", {"path": "missing"})),
            StreamEvent("usage", usage=Usage(3, 1, cache=CacheUsage(True, 2, 1))),
        ],
        [StreamEvent("text", "完成"), StreamEvent("usage", usage=Usage(4, 2, cache=CacheUsage(True, 3, 0)))],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))

    events = collect(agent)

    assert len(provider.requests) == 2
    assert provider.requests[0].stable_instructions == provider.requests[1].stable_instructions
    assert provider.requests[0].runtime_messages[0] != provider.requests[1].runtime_messages[0]
    assert all("system-reminder" not in str(message.content) for message in agent.conversation.messages)
    assert finished(events).usage.cache == CacheUsage(True, 5, 1)


def test_explanation_request_uses_mode_permission_behavior(tmp_path: Path) -> None:
    for mode in (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS_PERMISSIONS):
        target = tmp_path / f"blocked-{mode.value}.txt"
        provider = FakeProvider([
            [StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": target.name, "content": "x"}))],
            [StreamEvent("tool_call", tool_call=ToolCall("2", "read_file", {"file_path": target.name}))],
            [StreamEvent("text", "未获得授权，未写入文件")],
        ])
        agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
        agent.permissions.set_mode(mode)

        events = collect(agent, "解释这段代码的作用")

        result = next(event.result for event in events if isinstance(event, ToolResultReady))
        assert result.success
        assert target.read_text(encoding="utf-8") == "x"
        assert finished(events).reason is StopReason.COMPLETED
        assert "未写入文件" in finished(events).text


def test_workflow_precondition_returns_to_model_and_allows_retry(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("old", encoding="utf-8")
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "write_file", {"path": "existing.txt", "content": "new"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("2", "read_file", {"path": "existing.txt"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("3", "write_file", {"path": "existing.txt", "content": "new"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("4", "read_file", {"path": "existing.txt"}))],
        [StreamEvent("text", "已补读、覆盖并验证")],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))
    agent.permissions.set_mode(PermissionMode.ACCEPT_EDITS)

    events = collect(agent, "把 existing.txt 完整覆盖为 new 并验证")

    results = [event.result for event in events if isinstance(event, ToolResultReady)]
    assert results[0].error_code == "workflow_precondition"
    assert [result.success for result in results[1:]] == [True, True, True]
    assert path.read_text(encoding="utf-8") == "new"
    assert len(provider.requests) == 5
    assert finished(events).reason is StopReason.COMPLETED


def test_edit_requires_read_and_post_edit_verification_before_visible_completion(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("old", encoding="utf-8")
    provider = FakeProvider([
        [StreamEvent("tool_call", tool_call=ToolCall("1", "read_file", {"path": "a.txt"}))],
        [StreamEvent("tool_call", tool_call=ToolCall("2", "edit_file", {"path": "a.txt", "old_text": "old", "new_text": "new"}))],
        [StreamEvent("text", "已完成修改")],
        [StreamEvent("tool_call", tool_call=ToolCall("3", "read_file", {"path": "a.txt"}))],
        [StreamEvent("text", "已验证完成")],
    ])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))

    events = collect(agent, "修改 a.txt 并验证")

    assert path.read_text(encoding="utf-8") == "new"
    assert [event.content for event in events if isinstance(event, TextDelta)] == ["已验证完成"]
    assert finished(events).reason is StopReason.COMPLETED


def test_redacts_sensitive_text_before_showing_or_storing_history(tmp_path: Path) -> None:
    provider = FakeProvider([[StreamEvent("text", "api_key=sk-abcdefghijk")]])
    agent = Agent(provider, Conversation(), ToolRegistry(tmp_path))

    events = collect(agent, "解释配置")

    assert "sk-abcdefghijk" not in finished(events).text
    assert "[已脱敏]" in finished(events).text
    assert "sk-abcdefghijk" not in str(agent.conversation.messages)
