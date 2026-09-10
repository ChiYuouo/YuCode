"""长期记忆笔记、索引与后台提取任务。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml

from yucode.cancellation import Cancellation
from yucode.model_output import ModelOutputParseError, parse_json_object
from yucode.prompting import ModelRequest
from yucode.providers.base import Message, Provider


INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
MEMORY_TIMEOUT_SECONDS = 30
_INDEX_NAME = "index.md"


class MemoryScope(str, Enum):
    USER = "user"
    PROJECT = "project"


class MemoryKind(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION = "correction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryIndex:
    scope: MemoryScope
    content: str


@dataclass(frozen=True)
class MemoryNote:
    note_id: str
    scope: MemoryScope
    kind: MemoryKind
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryAction:
    operation: Literal["create", "update", "ignore"]
    scope: MemoryScope
    kind: MemoryKind
    title: str = ""
    content: str = ""
    note_id: str | None = None


class MemoryManager:
    """维护用户/项目笔记，并在后台安全地调用记忆提取模型。"""

    def __init__(self, provider: Provider, workspace_root: Path, user_home: Path | None = None) -> None:
        self._provider = provider
        self._project_dir = workspace_root.resolve() / ".yucode" / "memory"
        self._user_dir = ((user_home or Path.home()).resolve() / ".yucode" / "memory")
        self._tasks: set[asyncio.Task[None]] = set()
        self._diagnostics: list[str] = []

    def load_indexes(self) -> tuple[MemoryIndex, ...]:
        """读取两级索引；缺失索引时由已有笔记重建。"""
        indexes: list[MemoryIndex] = []
        for scope in (MemoryScope.USER, MemoryScope.PROJECT):
            directory = self._directory(scope)
            index_path = directory / _INDEX_NAME
            if index_path.is_file():
                try:
                    content = index_path.read_text(encoding="utf-8").strip()
                except OSError as error:
                    self._diagnostics.append(f"无法读取{_scope_label(scope)}记忆索引：{error}")
                    content = ""
            else:
                notes = self._read_notes(scope)
                content = self._rebuild_index(scope, notes) if notes else ""
            if content:
                indexes.append(MemoryIndex(scope, content))
        return tuple(indexes)

    def apply_actions(self, actions: Iterable[MemoryAction]) -> None:
        """验证后的动作写入笔记，并重建受限索引。"""
        touched: set[MemoryScope] = set()
        notes_by_scope = {scope: {note.note_id: note for note in self._read_notes(scope)} for scope in MemoryScope}
        now = datetime.now(UTC)
        for action in actions:
            if action.operation == "ignore":
                continue
            if action.operation == "create":
                if not action.title.strip() or not action.content.strip():
                    self._diagnostics.append("已忽略缺少标题或正文的自动笔记。")
                    continue
                note = MemoryNote(uuid4().hex, action.scope, action.kind, action.title.strip(), action.content.strip(), now, now)
                notes_by_scope[action.scope][note.note_id] = note
                self._write_note(note)
                touched.add(action.scope)
                continue
            if action.operation == "update":
                if action.note_id is None or action.note_id not in notes_by_scope[action.scope]:
                    self._diagnostics.append("已忽略目标不存在的自动笔记更新。")
                    continue
                original = notes_by_scope[action.scope][action.note_id]
                if not action.title.strip() or not action.content.strip():
                    self._diagnostics.append("已忽略缺少标题或正文的自动笔记更新。")
                    continue
                note = MemoryNote(original.note_id, original.scope, action.kind, action.title.strip(), action.content.strip(), original.created_at, now)
                notes_by_scope[action.scope][note.note_id] = note
                self._write_note(note)
                touched.add(action.scope)
        for scope in touched:
            self._rebuild_index(scope, notes_by_scope[scope].values())

    def schedule_update(self, completed_turn: Sequence[Message]) -> None:
        """安排后台记忆提取，立即返回给主 Agent。"""
        task = asyncio.create_task(self._update_from_turn(tuple(completed_turn)))
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)

    def drain_diagnostics(self) -> tuple[str, ...]:
        """取走待显示的中文诊断。"""
        values = tuple(self._diagnostics)
        self._diagnostics.clear()
        return values

    async def wait_for_pending_updates(self, timeout: float = 1.0) -> None:
        """退出前有限等待后台任务，超时任务会被取消。"""
        pending = tuple(self._tasks)
        if not pending:
            return
        _, still_pending = await asyncio.wait(pending, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)

    async def _update_from_turn(self, completed_turn: tuple[Message, ...]) -> None:
        try:
            output = await asyncio.wait_for(self._collect(_memory_request(completed_turn, self.load_indexes())), MEMORY_TIMEOUT_SECONDS)
            actions = _parse_actions(output)
            self.apply_actions(actions)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._diagnostics.append("自动笔记更新超时，当前对话不受影响。")
        except Exception as error:
            self._diagnostics.append(f"自动笔记更新失败，当前对话不受影响：{error}")

    async def _collect(self, request: ModelRequest) -> str:
        output = ""
        async for event in self._provider.stream(request, Cancellation()):
            if event.kind == "text":
                output += event.content
            elif event.kind == "tool_call":
                raise ValueError("自动笔记模型不得调用工具。")
        return output

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as error:
            self._diagnostics.append(f"自动笔记后台任务异常：{error}")

    def _directory(self, scope: MemoryScope) -> Path:
        return self._user_dir if scope is MemoryScope.USER else self._project_dir

    def _read_notes(self, scope: MemoryScope) -> tuple[MemoryNote, ...]:
        directory = self._directory(scope)
        if not directory.is_dir():
            return ()
        notes: list[MemoryNote] = []
        for path in directory.glob("*.md"):
            if path.name == _INDEX_NAME:
                continue
            try:
                note = _parse_note(path, scope)
            except (OSError, ValueError) as error:
                self._diagnostics.append(f"已跳过无效{_scope_label(scope)}记忆笔记 {path.name}：{error}")
                continue
            notes.append(note)
        return tuple(notes)

    def _write_note(self, note: MemoryNote) -> None:
        directory = self._directory(note.scope)
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": note.note_id,
            "scope": note.scope.value,
            "kind": note.kind.value,
            "title": note.title,
            "created_at": note.created_at.isoformat(),
            "updated_at": note.updated_at.isoformat(),
        }
        content = f"---\n{yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()}\n---\n\n{note.content}\n"
        (directory / f"{note.note_id}.md").write_text(content, encoding="utf-8")

    def _rebuild_index(self, scope: MemoryScope, notes: Iterable[MemoryNote]) -> str:
        values = tuple(sorted(notes, key=lambda note: note.updated_at, reverse=True))
        content = _render_index(values)
        if not _within_index_limit(content):
            self._diagnostics.append(f"{_scope_label(scope)}记忆索引已按容量限制缩减。")
            content = _limited_index(values)
        directory = self._directory(scope)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _INDEX_NAME).write_text(content, encoding="utf-8")
        return content


def _memory_request(turn: Sequence[Message], indexes: Sequence[MemoryIndex]) -> ModelRequest:
    index_text = "\n\n".join(f"[{_scope_label(index.scope)}索引]\n{index.content}" for index in indexes) or "（暂无已有记忆）"
    prompt = (
        "请从本轮对话中提取值得长期保留的事实。只输出 JSON 对象，不要 Markdown 或解释。"
        "格式：{\"actions\":[{\"operation\":\"create|update|ignore\",\"scope\":\"user|project\","
        "\"kind\":\"user_preference|correction|project_knowledge|reference\",\"note_id\":\"更新时必填\","
        "\"title\":\"简短标题\",\"content\":\"可读正文\"}]}。"
        "仅保存明确、稳定且未来有用的信息；重复内容必须选择 update 或 ignore。\n\n"
        f"现有索引：\n{index_text}\n\n本轮对话：\n{_messages_text(turn)}"
    )
    return ModelRequest(
        history=(Message("user", prompt),),
        stable_instructions="你是 YuCode 的记忆整理器。不得调用工具，只能返回符合格式的 JSON。",
        runtime_messages=(),
        tools=(),
        prompt_cache_key="yucode-memory-v1",
    )


def _parse_actions(output: str) -> tuple[MemoryAction, ...]:
    try:
        raw = parse_json_object(output)
    except ModelOutputParseError as error:
        raise ValueError("模型未返回有效 JSON") from error
    if not isinstance(raw.get("actions"), list):
        raise ValueError("模型返回缺少 actions 列表")
    actions: list[MemoryAction] = []
    for value in raw["actions"]:
        if not isinstance(value, dict):
            raise ValueError("actions 项必须是对象")
        try:
            operation = value["operation"]
            scope = MemoryScope(value["scope"])
            kind = MemoryKind(value["kind"])
        except (KeyError, ValueError) as error:
            raise ValueError("actions 项的操作、范围或类别无效") from error
        if operation not in {"create", "update", "ignore"}:
            raise ValueError("actions 项的操作无效")
        title = value.get("title", "")
        content = value.get("content", "")
        note_id = value.get("note_id")
        if not isinstance(title, str) or not isinstance(content, str) or (note_id is not None and not isinstance(note_id, str)):
            raise ValueError("actions 项的文本字段无效")
        actions.append(MemoryAction(operation, scope, kind, title, content, note_id))
    return tuple(actions)


def _parse_note(path: Path, expected_scope: MemoryScope) -> MemoryNote:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("缺少 frontmatter")
    _, raw_metadata, body = text.split("---", 2)
    metadata = yaml.safe_load(raw_metadata)
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter 无效")
    try:
        scope = MemoryScope(metadata["scope"])
        kind = MemoryKind(metadata["kind"])
        note_id = metadata["id"]
        title = metadata["title"]
        created_at = _parse_time(metadata["created_at"])
        updated_at = _parse_time(metadata["updated_at"])
    except (KeyError, ValueError) as error:
        raise ValueError("frontmatter 字段无效") from error
    if scope is not expected_scope or not all(isinstance(value, str) and value for value in (note_id, title)):
        raise ValueError("笔记范围或文本字段无效")
    content = body.strip()
    if not content:
        raise ValueError("笔记正文为空")
    return MemoryNote(note_id, scope, kind, title, content, created_at, updated_at)


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("时间字段无效")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _render_index(notes: Sequence[MemoryNote]) -> str:
    lines = ["# 自动记忆索引"]
    for kind in MemoryKind:
        group = [note for note in notes if note.kind is kind]
        if not group:
            continue
        lines.extend(("", f"## {_kind_label(kind)}"))
        lines.extend(_index_line(note) for note in group)
    return "\n".join(lines).strip() + "\n"


def _limited_index(notes: Sequence[MemoryNote]) -> str:
    selected: list[MemoryNote] = []
    for note in notes:
        candidate = _render_index((*selected, note))
        if len(candidate.splitlines()) > INDEX_MAX_LINES or len(candidate.encode("utf-8")) > INDEX_MAX_BYTES:
            continue
        selected.append(note)
    return _render_index(selected)


def _index_line(note: MemoryNote) -> str:
    summary = " ".join(note.content.split())[:180]
    title = " ".join(note.title.split())[:80]
    return f"- [{note.note_id}] {title}：{summary}"


def _within_index_limit(content: str) -> bool:
    return len(content.splitlines()) <= INDEX_MAX_LINES and len(content.encode("utf-8")) <= INDEX_MAX_BYTES


def _messages_text(messages: Sequence[Message]) -> str:
    values: list[str] = []
    for message in messages:
        parts: list[str] = []
        for block in message.blocks:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif hasattr(block, "result"):
                parts.append(block.result.for_model())
            elif hasattr(block, "call"):
                parts.append(f"工具调用：{block.call.name}")
        values.append(f"{message.role}: {' '.join(parts)}")
    return "\n".join(values)


def _scope_label(scope: MemoryScope) -> str:
    return "用户级" if scope is MemoryScope.USER else "项目级"


def _kind_label(kind: MemoryKind) -> str:
    return {
        MemoryKind.USER_PREFERENCE: "用户偏好",
        MemoryKind.CORRECTION: "纠正反馈",
        MemoryKind.PROJECT_KNOWLEDGE: "项目知识",
        MemoryKind.REFERENCE: "参考资料",
    }[kind]
