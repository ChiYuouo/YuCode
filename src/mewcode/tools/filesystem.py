"""受工作目录边界保护的文本文件工具。"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from mewcode.tools.base import ToolContext, ToolDefinition, ToolResult

MAX_READ_BYTES = 1_048_576
MAX_RESULT_CHARS = 12_000
MAX_MATCHES = 200
SKIPPED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", "dist"}


class ReadFileTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "read_file", "读取工作目录内的 UTF-8 文本文件。",
            _schema({"path": _string("要读取的相对文件路径")}, ["path"]),
        )

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
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
            raw = path.read_bytes()
            if b"\0" in raw:
                return _failure(call_id, self.definition.name, "不支持读取二进制文件。", "binary_file")
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _failure(call_id, self.definition.name, "文件不是 UTF-8 文本。", "non_utf8_file")
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法读取文件：{error}", "read_error")
        content, truncated = _truncate(content)
        summary = f"已读取 {_relative(path, context.root)}。"
        if truncated:
            summary += " 内容已截断。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=_relative(path, context.root))


class WriteFileTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "write_file", "在工作目录内新建或覆盖 UTF-8 文本文件。",
            _schema(
                {"path": _string("要写入的相对文件路径"), "content": _string("完整文件内容")},
                ["path", "content"],
            ),
        )

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
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

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
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
            original = path.read_text(encoding="utf-8")
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
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "find_files", "按 glob 模式查找工作目录内的文件。",
            _schema({"pattern": _string("glob 模式，例如 **/*.py")}, ["pattern"]),
        )

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return _failure(call_id, self.definition.name, "参数 pattern 必须是非空字符串。", "invalid_arguments")
        try:
            matches = [
                path
                for path in context.root.glob(pattern)
                if _is_workspace_file(path, context.root) and not _is_skipped(path, context.root)
            ]
        except (OSError, ValueError) as error:
            return _failure(call_id, self.definition.name, f"无效的查找模式：{error}", "invalid_pattern")
        content, truncated = _joined_paths(matches, context.root)
        summary = f"找到 {min(len(matches), MAX_MATCHES)} 个文件。"
        if truncated:
            summary += " 结果已截断。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=pattern)


class SearchCodeTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "search_code", "用正则表达式搜索工作目录内的 UTF-8 文本内容。",
            _schema(
                {"pattern": _string("Python 正则表达式"), "path": _string("可选的工作目录内相对目录或文件")},
                ["pattern"],
            ),
        )

    def execute(self, arguments: Mapping[str, Any], context: ToolContext, call_id: str) -> ToolResult:
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
        content, truncated = _truncate("\n".join(matches))
        limit_reached = len(matches) >= MAX_MATCHES
        summary = f"找到 {len(matches)} 处匹配。"
        if truncated or limit_reached:
            summary += " 结果已截断。"
        return ToolResult(call_id, self.definition.name, True, summary, content, target=pattern)


def _schema(properties: Mapping[str, Any], required: list[str]) -> Mapping[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _string(description: str) -> Mapping[str, str]:
    return {"type": "string", "description": description}


def _path_argument(arguments: Mapping[str, Any], context: ToolContext, call_id: str, name: str) -> Path | ToolResult:
    value = arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        return _failure(call_id, name, "参数 path 必须是非空字符串。", "invalid_arguments")
    try:
        root = context.root.resolve(strict=True)
        candidate = (root / value).resolve(strict=False)
        candidate.relative_to(root)
        return candidate
    except (OSError, ValueError):
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


def _truncate(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_RESULT_CHARS:
        return content, False
    return content[:MAX_RESULT_CHARS] + "\n…（结果已截断）", True


def _joined_paths(paths: list[Path], root: Path) -> tuple[str, bool]:
    selected = paths[:MAX_MATCHES]
    content, output_truncated = _truncate("\n".join(_relative(path, root) for path in selected))
    return content, len(paths) > MAX_MATCHES or output_truncated


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
