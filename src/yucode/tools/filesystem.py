"""受工作目录边界保护的文本文件工具。"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from yucode.cancellation import Cancellation
from yucode.tools.base import ToolContext, ToolDefinition, ToolResult, ToolSafety

MAX_READ_BYTES = 1_048_576
MAX_MATCHES = 200
SKIPPED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", "dist"}


class WorkspacePathError(ValueError):
    """路径无法被安全地限制在工作目录中。"""


class _BinaryContentError(ValueError):
    """文件包含不应作为文本展示的二进制内容。"""


def resolve_workspace_path(root: Path, value: str) -> Path:
    """解析已有符号链接后，返回工作目录内的规范路径。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkspacePathError("路径必须是非空字符串。")
    try:
        resolved_root = root.resolve(strict=True)
        candidate = (resolved_root / value).resolve(strict=False)
        candidate.relative_to(resolved_root)
        return candidate
    except (OSError, ValueError) as error:
        raise WorkspacePathError("路径超出工作目录范围。") from error


def validate_workspace_glob(pattern: str) -> None:
    """拒绝可在枚举前离开工作目录的 glob 模式。"""
    if not isinstance(pattern, str) or not pattern:
        raise WorkspacePathError("查找模式必须是非空字符串。")
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkspacePathError("查找路径超出工作目录范围。")


class ReadFileTool:
    safety = ToolSafety.READ_ONLY

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "read_file", "读取工作目录内的 UTF-8 或带 BOM 的 UTF-16 文本文件。调用时必须提供非空 file_path，例如 {\"file_path\": \"note.txt\"}。",
            _schema(
                {"file_path": _string("必须是非空的工作目录相对文件路径，例如 note.txt", min_length=1)},
                ["file_path"],
            ),
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return _cancelled(call_id, self.definition.name)
        return await asyncio.to_thread(self._execute, arguments, context, call_id)

    def _execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        path_or_error = _path_argument(arguments, context, call_id, self.definition.name)
        if isinstance(path_or_error, ToolResult):
            return path_or_error
        path = path_or_error
        if not path.is_file():
            return _failure(call_id, self.definition.name, "文件不存在或不是普通文件。", "not_found")
        try:
            size = path.stat().st_size
            if size > MAX_READ_BYTES:
                return _failure(call_id, self.definition.name, "文件超过 1 MiB 读取上限。", "file_too_large")
            content = _decode_text(path.read_bytes())
        except _BinaryContentError:
            return _failure(call_id, self.definition.name, "不支持读取二进制文件。", "binary_file")
        except UnicodeDecodeError:
            return _failure(call_id, self.definition.name, "文件不是 UTF-8 文本。", "non_utf8_file")
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法读取文件：{error}", "read_error")
        summary = f"已读取 {_relative(path, context.root)}。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=_relative(path, context.root))


class WriteFileTool:
    safety = ToolSafety.SIDE_EFFECT

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "write_file", "在工作目录内新建或覆盖 UTF-8 文本文件。",
            _schema(
                {"path": _string("要写入的相对文件路径"), "content": _string("完整文件内容")},
                ["path", "content"],
            ),
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return _cancelled(call_id, self.definition.name)
        return await asyncio.to_thread(self._execute, arguments, context, call_id)

    def _execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        path_or_error = _path_argument(arguments, context, call_id, self.definition.name)
        if isinstance(path_or_error, ToolResult):
            return path_or_error
        content = arguments.get("content")
        if not isinstance(content, str):
            return _failure(call_id, self.definition.name, "参数 content 必须是字符串。", "invalid_arguments")
        path = path_or_error
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法写入文件：{error}", "write_error")
        return ToolResult(call_id, self.definition.name, True, f"已写入 {_relative(path, context.root)}。", target=_relative(path, context.root))


class EditFileTool:
    safety = ToolSafety.SIDE_EFFECT

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "edit_file", "通过唯一原文匹配替换工作目录内文本文件的一段内容。",
            _schema(
                {
                    "path": _string("要修改的相对文件路径"),
                    "old_text": _string("必须唯一匹配的原始文本"),
                    "new_text": _string("用于替换的新文本"),
                },
                ["path", "old_text", "new_text"],
            ),
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return _cancelled(call_id, self.definition.name)
        return await asyncio.to_thread(self._execute, arguments, context, call_id)

    def _execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        path_or_error = _path_argument(arguments, context, call_id, self.definition.name)
        if isinstance(path_or_error, ToolResult):
            return path_or_error
        path = path_or_error
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str) or not old_text:
            return _failure(
                call_id, self.definition.name, "参数 old_text 和 new_text 必须是字符串，且 old_text 不能为空。", "invalid_arguments"
            )
        try:
            if not path.is_file():
                return _failure(call_id, self.definition.name, "文件不存在或不是普通文件。", "not_found")
            if path.stat().st_size > MAX_READ_BYTES:
                return _failure(call_id, self.definition.name, "文件超过 1 MiB 修改上限。", "file_too_large")
            original = _decode_text(path.read_bytes())
        except _BinaryContentError:
            return _failure(call_id, self.definition.name, "不支持修改二进制文件。", "binary_file")
        except UnicodeDecodeError:
            return _failure(call_id, self.definition.name, "文件不是 UTF-8 文本。", "non_utf8_file")
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法读取文件：{error}", "read_error")
        matches = original.count(old_text)
        if matches == 0:
            return _failure(call_id, self.definition.name, "未找到原始文本，文件未修改。", "no_match")
        if matches > 1:
            return _failure(call_id, self.definition.name, "原始文本匹配多次，文件未修改。", "multiple_matches")
        try:
            _atomic_write(path, original.replace(old_text, new_text, 1))
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法写入文件：{error}", "write_error")
        return ToolResult(call_id, self.definition.name, True, f"已唯一替换 {_relative(path, context.root)} 中的文本。", target=_relative(path, context.root))


class FindFilesTool:
    safety = ToolSafety.READ_ONLY

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "find_files", "按 glob 模式查找工作目录内的文件。",
            _schema({"pattern": _string("glob 模式，例如 **/*.py")}, ["pattern"]),
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return _cancelled(call_id, self.definition.name)
        return await asyncio.to_thread(self._execute, arguments, context, call_id)

    def _execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return _failure(call_id, self.definition.name, "参数 pattern 必须是非空字符串。", "invalid_arguments")
        try:
            validate_workspace_glob(pattern)
            matches: list[Path] = []
            limit_reached = False
            for path in context.root.glob(pattern):
                if not _is_workspace_file(path, context.root) or _is_skipped(path, context.root):
                    continue
                matches.append(path)
                if len(matches) >= MAX_MATCHES:
                    limit_reached = True
                    break
        except (OSError, ValueError) as error:
            return _failure(call_id, self.definition.name, f"无效的查找模式：{error}", "invalid_pattern")
        content = _joined_paths(matches, context.root)
        summary = f"找到 {len(matches)} 个文件。"
        if limit_reached:
            summary += f" 结果达到 {MAX_MATCHES} 个上限。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=pattern)


class SearchCodeTool:
    safety = ToolSafety.READ_ONLY

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "search_code", "用正则表达式搜索工作目录内的 UTF-8 文本内容。",
            _schema(
                {"pattern": _string("Python 正则表达式"), "path": _string("可选的工作目录内相对目录或文件")},
                ["pattern"],
            ),
        )

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return _cancelled(call_id, self.definition.name)
        return await asyncio.to_thread(self._execute, arguments, context, call_id)

    def _execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return _failure(call_id, self.definition.name, "参数 pattern 必须是非空字符串。", "invalid_arguments")
        try:
            regex = re.compile(pattern)
        except re.error as error:
            return _failure(call_id, self.definition.name, f"正则表达式无效：{error}", "invalid_pattern")
        start = context.root
        if "path" in arguments:
            path_or_error = _path_argument(arguments, context, call_id, self.definition.name)
            if isinstance(path_or_error, ToolResult):
                return path_or_error
            start = path_or_error
        if not start.exists():
            return _failure(call_id, self.definition.name, "搜索路径不存在。", "not_found")
        paths: Iterable[Path] = [start] if start.is_file() else start.rglob("*")
        matches: list[str] = []
        for path in paths:
            if len(matches) >= MAX_MATCHES:
                break
            if not _is_workspace_file(path, context.root) or _is_skipped(path, context.root):
                continue
            try:
                if path.stat().st_size > MAX_READ_BYTES:
                    continue
                raw = path.read_bytes()
                if b"\0" in raw:
                    continue
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{_relative(path, context.root)}:{line_number}: {line}")
                    if len(matches) >= MAX_MATCHES:
                        break
        content = "\n".join(matches)
        limit_reached = len(matches) >= MAX_MATCHES
        summary = f"找到 {len(matches)} 处匹配。"
        if limit_reached:
            summary += f" 结果达到 {MAX_MATCHES} 条上限。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=pattern)


def _schema(properties: Mapping[str, Any], required: list[str]) -> Mapping[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _string(description: str, min_length: int | None = None) -> Mapping[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if min_length is not None:
        schema["minLength"] = min_length
    return schema


def _path_argument(arguments: Mapping[str, Any], context: ToolContext, call_id: str, name: str) -> Path | ToolResult:
    # read_file 对模型使用更明确的 file_path；保留 path 以兼容旧会话或内部调用。
    value = arguments.get("file_path") if name == "read_file" else arguments.get("path")
    if name == "read_file" and value is None:
        value = arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        parameter = "file_path" if name == "read_file" else "path"
        hint = f"参数 {parameter} 必须是非空字符串。"
        if name == "read_file":
            hint += " 请重新调用 read_file，并传入工作目录相对路径，例如 {\"file_path\": \"note.txt\"}。"
        return _failure(call_id, name, hint, "invalid_arguments")
    try:
        return resolve_workspace_path(context.root, value)
    except WorkspacePathError:
        return _failure(call_id, name, "路径超出工作目录范围。", "path_outside_workspace")


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _decode_text(raw: bytes) -> str:
    """读取 UTF-8 与带 BOM 的 UTF-16 文本，其他含 NUL 数据仍视为二进制。"""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if b"\0" in raw:
        raise _BinaryContentError()
    return raw.decode("utf-8-sig")


def _joined_paths(paths: list[Path], root: Path) -> str:
    return "\n".join(_relative(path, root) for path in paths)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_skipped(path: Path, root: Path) -> bool:
    try:
        return bool(set(path.relative_to(root).parts) & SKIPPED_DIRECTORIES)
    except ValueError:
        return True


def _is_workspace_file(path: Path, root: Path) -> bool:
    try:
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _failure(call_id: str, name: str, summary: str, code: str) -> ToolResult:
    return ToolResult(call_id, name, False, summary, error_code=code)


def _cancelled(call_id: str, name: str) -> ToolResult:
    return _failure(call_id, name, "用户已取消，工具未执行。", "cancelled")
