"""JSONL 会话存档、扫描、恢复和清理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from yucode.conversation import Conversation, ConversationEvent
from yucode.providers.base import Message, TextContent
from yucode.tools.base import ToolCall, ToolResult


SESSION_FORMAT_VERSION = 1
SESSION_RETENTION = timedelta(days=30)
_SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class SessionError(ValueError):
    """会话不存在或无法恢复。"""


class _RecordError(ValueError):
    """单行 JSONL 记录无效。"""


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str
    last_active_at: datetime
    message_count: int


@dataclass(frozen=True)
class RecoveredSession:
    summary: SessionSummary
    messages: tuple[Message, ...]
    last_active_at: datetime
    warnings: tuple[str, ...] = ()


class SessionManager:
    """管理当前项目 `.yucode/sessions` 内的会话文件。"""

    def __init__(self, workspace_root: Path, now: Callable[[], datetime] | None = None) -> None:
        self._root = workspace_root.resolve()
        self._directory = self._root / ".yucode" / "sessions"
        self._now = now or (lambda: datetime.now(UTC))
        self._active_session_id: str | None = None

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    def create_session(self) -> str:
        """创建并激活一个空 JSONL 文件。"""
        self._directory.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            session_id = f"{_as_utc(self._now()):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
            path = self._path_for(session_id)
            try:
                path.touch(exist_ok=False)
            except FileExistsError:
                continue
            self._active_session_id = session_id
            return session_id
        raise SessionError("无法创建唯一会话 ID，请稍后重试。")

    def record_event(self, event: ConversationEvent) -> None:
        """将一条已提交的对话事件追加到当前会话。"""
        if self._active_session_id is None:
            raise SessionError("尚未创建当前会话。")
        path = self._path_for(self._active_session_id)
        record = {
            "version": SESSION_FORMAT_VERSION,
            "timestamp": _as_utc(self._now()).isoformat(),
            "type": event.kind,
            "payload": _encode_event(event),
        }
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()

    def activate_session(self, session_id: str) -> None:
        """将后续追加写入已验证的历史会话。"""
        path = self._path_for(_validated_session_id(session_id))
        if not path.is_file():
            raise SessionError("找不到指定会话。")
        self._active_session_id = session_id

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        """从每个 JSONL 文件直接派生可显示概要。"""
        if not self._directory.is_dir():
            return ()
        summaries: list[SessionSummary] = []
        for path in self._directory.glob("*.jsonl"):
            session_id = path.stem
            if not _SESSION_ID_RE.fullmatch(session_id):
                continue
            try:
                recovered = self._recover_path(session_id, path)
            except SessionError:
                continue
            summaries.append(recovered.summary)
        return tuple(sorted(summaries, key=lambda item: item.last_active_at, reverse=True))

    def recover(self, session_id: str) -> RecoveredSession:
        """恢复一个会话；单行损坏不会影响其余可信记录。"""
        path = self._path_for(_validated_session_id(session_id))
        if not path.is_file():
            raise SessionError("找不到指定会话。")
        return self._recover_path(session_id, path)

    def cleanup_expired(self, active_session_id: str | None = None) -> tuple[str, ...]:
        """删除超过 30 天且非当前活动会话的可信存档。"""
        active = active_session_id or self._active_session_id
        now = _as_utc(self._now())
        deleted: list[str] = []
        if not self._directory.is_dir():
            return ()
        for path in self._directory.glob("*.jsonl"):
            session_id = path.stem
            if session_id == active or not _SESSION_ID_RE.fullmatch(session_id):
                continue
            try:
                recovered = self._recover_path(session_id, path)
            except SessionError:
                last_active = _session_id_time(session_id)
            else:
                last_active = recovered.last_active_at
            if now - last_active > SESSION_RETENTION:
                path.unlink()
                deleted.append(session_id)
        return tuple(deleted)

    def _recover_path(self, session_id: str, path: Path) -> RecoveredSession:
        warnings: list[str] = []
        records: list[tuple[datetime, ConversationEvent, int]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise SessionError(f"无法读取会话：{error}") from error
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(_decode_record(line, number))
            except _RecordError as error:
                warnings.append(f"已跳过会话第 {number} 行：{error}")

        conversation = Conversation()
        pending: tuple[datetime, ConversationEvent, int] | None = None
        last_active: datetime | None = None
        for timestamp, event, number in records:
            if pending is not None:
                pending_time, pending_event, pending_number = pending
                if event.kind != "tool_results" or not _calls_match(pending_event.calls, event.results):
                    warnings.append(f"会话第 {pending_number} 行的工具调用没有匹配结果，已截断后续历史。")
                    break
                conversation.append_assistant(pending_event.text, pending_event.calls)
                conversation.append_tool_results(event.results)
                last_active = timestamp
                pending = None
                continue
            if event.kind == "assistant" and event.calls:
                pending = (timestamp, event, number)
                continue
            _replay(conversation, event)
            last_active = timestamp

        if pending is not None:
            warnings.append(f"会话第 {pending[2]} 行的工具调用没有匹配结果，已截断后续历史。")
        if not conversation.messages or last_active is None:
            raise SessionError("会话没有可恢复的有效消息。")
        title = _title_from_messages(conversation.messages)
        summary = SessionSummary(session_id, title, last_active, len(conversation.messages))
        return RecoveredSession(summary, conversation.messages, last_active, tuple(warnings))

    def _path_for(self, session_id: str) -> Path:
        _validated_session_id(session_id)
        return self._directory / f"{session_id}.jsonl"


def _encode_event(event: ConversationEvent) -> dict[str, Any]:
    if event.kind in {"user", "partial_assistant"}:
        return {"text": event.text}
    if event.kind == "assistant":
        return {"text": event.text, "calls": [_encode_call(call) for call in event.calls]}
    if event.kind == "tool_results":
        return {"results": [_encode_result(result) for result in event.results]}
    raise ValueError(f"未知会话事件：{event.kind}")


def _decode_record(line: str, number: int) -> tuple[datetime, ConversationEvent, int]:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise _RecordError("不是有效 JSON") from error
    if not isinstance(raw, Mapping) or raw.get("version") != SESSION_FORMAT_VERSION:
        raise _RecordError("格式版本无效")
    timestamp = raw.get("timestamp")
    event_type = raw.get("type")
    payload = raw.get("payload")
    if not isinstance(timestamp, str) or not isinstance(event_type, str) or not isinstance(payload, Mapping):
        raise _RecordError("缺少必要字段")
    try:
        parsed_time = _as_utc(datetime.fromisoformat(timestamp))
    except ValueError as error:
        raise _RecordError("时间格式无效") from error
    try:
        event = _decode_event(event_type, payload)
    except (TypeError, ValueError) as error:
        raise _RecordError(f"载荷无效：{error}") from error
    return parsed_time, event, number


def _decode_event(event_type: str, payload: Mapping[str, Any]) -> ConversationEvent:
    if event_type in {"user", "partial_assistant"}:
        return ConversationEvent(event_type, _required_text(payload, "text"))
    if event_type == "assistant":
        calls = payload.get("calls", [])
        if not isinstance(calls, list):
            raise ValueError("calls 必须是列表")
        return ConversationEvent("assistant", _required_text(payload, "text", allow_empty=True), tuple(_decode_call(item) for item in calls))
    if event_type == "tool_results":
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise ValueError("results 必须是非空列表")
        return ConversationEvent("tool_results", results=tuple(_decode_result(item) for item in results))
    raise ValueError("事件类型无效")


def _encode_call(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}


def _decode_call(raw: Any) -> ToolCall:
    if not isinstance(raw, Mapping):
        raise ValueError("工具调用必须是对象")
    arguments = raw.get("arguments")
    if not isinstance(arguments, Mapping):
        raise ValueError("工具调用参数必须是对象")
    return ToolCall(_required_text(raw, "id"), _required_text(raw, "name"), dict(arguments))


def _encode_result(result: ToolResult) -> dict[str, Any]:
    return {
        "call_id": result.call_id,
        "name": result.name,
        "success": result.success,
        "summary": result.summary,
        "content": result.content,
        "error_code": result.error_code,
        "target": result.target,
    }


def _decode_result(raw: Any) -> ToolResult:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("success"), bool):
        raise ValueError("工具结果格式无效")
    error_code = raw.get("error_code")
    if error_code is not None and not isinstance(error_code, str):
        raise ValueError("error_code 必须是字符串或 null")
    return ToolResult(
        _required_text(raw, "call_id"),
        _required_text(raw, "name"),
        raw["success"],
        _required_text(raw, "summary", allow_empty=True),
        _required_text(raw, "content", allow_empty=True),
        error_code,
        _required_text(raw, "target", allow_empty=True),
    )


def _required_text(raw: Mapping[str, Any], field: str, allow_empty: bool = False) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{field} 必须是{'非空' if not allow_empty else ''}字符串")
    return value


def _replay(conversation: Conversation, event: ConversationEvent) -> None:
    if event.kind == "user":
        conversation.append_user(event.text)
    elif event.kind == "assistant":
        conversation.append_assistant(event.text, event.calls)
    elif event.kind == "partial_assistant":
        conversation.append_partial_assistant(event.text)
    elif event.kind == "tool_results":
        conversation.append_tool_results(event.results)


def _calls_match(calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> bool:
    return len(calls) == len(results) and {call.id for call in calls} == {result.call_id for result in results}


def _title_from_messages(messages: tuple[Message, ...]) -> str:
    for message in messages:
        if message.role != "user":
            continue
        for block in message.blocks:
            if isinstance(block, TextContent) and block.text.strip():
                normalized = " ".join(block.text.split())
                return normalized[:60]
    return "（无用户标题）"


def _validated_session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise SessionError("会话 ID 格式无效。")
    return session_id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _session_id_time(session_id: str) -> datetime:
    return datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
