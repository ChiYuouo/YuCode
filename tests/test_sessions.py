from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from yucode.conversation import ConversationEvent
from yucode.providers.base import TextContent, ToolCallContent, ToolResultContent
from yucode.sessions import SessionError, SessionManager
from yucode.tools.base import ToolCall, ToolResult


def manager(tmp_path: Path, now: datetime | None = None) -> SessionManager:
    return SessionManager(tmp_path, now=lambda: now or datetime(2026, 1, 31, tzinfo=UTC))


def append_complete_turn(store: SessionManager, text: str = "第一条") -> tuple[ToolCall, ToolResult]:
    call = ToolCall("call-1", "read_file", {"file_path": "README.md"})
    result = ToolResult("call-1", "read_file", True, "已读取", "YuCode", target="README.md")
    store.record_event(ConversationEvent("user", text))
    store.record_event(ConversationEvent("assistant", "我来读取。", (call,)))
    store.record_event(ConversationEvent("tool_results", results=(result,)))
    store.record_event(ConversationEvent("assistant", "项目名是 YuCode。"))
    return call, result


def test_creates_unique_id_and_appends_jsonl(tmp_path: Path) -> None:
    store = manager(tmp_path)
    first = store.create_session()
    store.record_event(ConversationEvent("user", "你好"))
    second = store.create_session()

    assert first != second
    path = tmp_path / ".yucode" / "sessions" / f"{first}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["type"] == "user" and rows[0]["payload"] == {"text": "你好"}


def test_recovers_complete_events_and_derives_summary(tmp_path: Path) -> None:
    store = manager(tmp_path)
    session_id = store.create_session()
    call, result = append_complete_turn(store, "读取项目名称")

    recovered = store.recover(session_id)

    assert recovered.summary.title == "读取项目名称"
    assert recovered.summary.message_count == 4
    assert isinstance(recovered.messages[1].blocks[1], ToolCallContent)
    assert recovered.messages[1].blocks[1].call == call
    assert isinstance(recovered.messages[2].blocks[0], ToolResultContent)
    assert recovered.messages[2].blocks[0].result == result


def test_skips_bad_line_but_recovers_later_valid_records(tmp_path: Path) -> None:
    store = manager(tmp_path)
    session_id = store.create_session()
    store.record_event(ConversationEvent("user", "第一条"))
    path = tmp_path / ".yucode" / "sessions" / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{坏行\n")
    store.record_event(ConversationEvent("assistant", "仍可恢复"))

    recovered = store.recover(session_id)

    assert [message.blocks[0].text for message in recovered.messages if isinstance(message.blocks[0], TextContent)] == ["第一条", "仍可恢复"]
    assert any("已跳过" in warning for warning in recovered.warnings)


def test_unmatched_tool_call_truncates_following_records(tmp_path: Path) -> None:
    store = manager(tmp_path)
    session_id = store.create_session()
    store.record_event(ConversationEvent("user", "保留"))
    store.record_event(ConversationEvent("assistant", "调用", (ToolCall("a", "read_file", {}),)))
    store.record_event(ConversationEvent("user", "不得恢复"))

    recovered = store.recover(session_id)

    assert len(recovered.messages) == 1
    assert recovered.messages[0].blocks[0].text == "保留"
    assert any("没有匹配结果" in warning for warning in recovered.warnings)


def test_mismatched_tool_result_truncates_and_resumed_user_merges(tmp_path: Path) -> None:
    store = manager(tmp_path)
    good_id = store.create_session()
    call = ToolCall("call-1", "read_file", {})
    result = ToolResult("call-1", "read_file", True, "已读取", "内容")
    store.record_event(ConversationEvent("user", "读取"))
    store.record_event(ConversationEvent("assistant", "", (call,)))
    store.record_event(ConversationEvent("tool_results", results=(result,)))
    store.record_event(ConversationEvent("user", "继续"))

    recovered = store.recover(good_id)
    assert isinstance(recovered.messages[2].blocks[0], ToolResultContent)
    assert isinstance(recovered.messages[2].blocks[1], TextContent)

    bad_id = store.create_session()
    store.record_event(ConversationEvent("user", "保留"))
    store.record_event(ConversationEvent("assistant", "调用", (call,)))
    store.record_event(ConversationEvent("tool_results", results=(ToolResult("wrong", result.name, True, "x"),)))
    assert len(store.recover(bad_id).messages) == 1


def test_list_orders_by_activity_and_cleanup_only_old_non_active(tmp_path: Path) -> None:
    now = datetime(2026, 1, 31, tzinfo=UTC)
    old = SessionManager(tmp_path, now=lambda: now - timedelta(days=31))
    old_id = old.create_session()
    old.record_event(ConversationEvent("user", "旧会话"))
    recent = manager(tmp_path, now)
    recent_id = recent.create_session()
    recent.record_event(ConversationEvent("user", "新会话"))
    (tmp_path / ".yucode" / "sessions" / "not-a-session.txt").write_text("保留", encoding="utf-8")

    summaries = recent.list_sessions()
    deleted = recent.cleanup_expired(recent_id)

    assert [item.session_id for item in summaries] == [recent_id, old_id]
    assert deleted == (old_id,)
    assert not (tmp_path / ".yucode" / "sessions" / f"{old_id}.jsonl").exists()
    assert (tmp_path / ".yucode" / "sessions" / f"{recent_id}.jsonl").exists()
    assert (tmp_path / ".yucode" / "sessions" / "not-a-session.txt").exists()


def test_rejects_invalid_session_id_and_empty_or_truncated_only_log(tmp_path: Path) -> None:
    store = manager(tmp_path)
    session_id = store.create_session()
    path = tmp_path / ".yucode" / "sessions" / f"{session_id}.jsonl"
    path.write_text('{"version":', encoding="utf-8")

    with pytest.raises(SessionError):
        store.recover("../outside")
    with pytest.raises(SessionError):
        store.recover(session_id)


def test_cleanup_removes_expired_empty_session_from_its_id_timestamp(tmp_path: Path) -> None:
    now = datetime(2026, 2, 15, tzinfo=UTC)
    store = manager(tmp_path, now)
    directory = tmp_path / ".yucode" / "sessions"
    directory.mkdir(parents=True)
    old_id = "20260101-000000-abcd"
    (directory / f"{old_id}.jsonl").touch()

    assert store.cleanup_expired() == (old_id,)
