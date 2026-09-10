"""加载分层项目指令，并安全展开 ``@include``。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


INSTRUCTION_NAME = "YUCODE.md"
MAX_INCLUDE_DEPTH = 5
_INCLUDE_RE = re.compile(r"^\s*@include\s+(.+?)\s*$")


@dataclass(frozen=True)
class LoadedInstructions:
    """已按优先级拼接的指令与可展示警告。"""

    content: str = ""
    warnings: tuple[str, ...] = ()


class InstructionLoader:
    """从项目根、项目配置和用户配置读取手写指令。"""

    def __init__(self, user_home: Path | None = None, max_include_depth: int = MAX_INCLUDE_DEPTH) -> None:
        if max_include_depth < 0:
            raise ValueError("include 最大深度不能小于 0。")
        self._user_home = user_home
        self._max_include_depth = max_include_depth

    def load(self, workspace_root: Path) -> LoadedInstructions:
        """按高到低优先级加载三份入口文件。"""
        root = workspace_root.resolve()
        user_root = (self._user_home or Path.home()).resolve() / ".yucode"
        entries = (
            (root / INSTRUCTION_NAME, root),
            (root / ".yucode" / INSTRUCTION_NAME, root),
            (user_root / INSTRUCTION_NAME, user_root),
        )
        warnings: list[str] = []
        contents: list[str] = []
        for entry, allowed_root in entries:
            if not entry.is_file():
                continue
            expanded = self._expand(entry, allowed_root, 0, set(), warnings)
            if expanded.strip():
                contents.append(expanded.strip())
        return LoadedInstructions("\n\n".join(contents), tuple(warnings))

    def _expand(
        self,
        path: Path,
        allowed_root: Path,
        depth: int,
        visited: set[Path],
        warnings: list[str],
    ) -> str:
        resolved = path.resolve()
        if not _is_within(resolved, allowed_root):
            warnings.append(f"已跳过越界指令引用：{path}")
            return ""
        if depth > self._max_include_depth:
            warnings.append(f"已跳过超过最大嵌套深度的指令引用：{path}")
            return ""
        if resolved in visited:
            warnings.append(f"已跳过循环指令引用：{path}")
            return ""
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as error:
            warnings.append(f"无法读取指令文件 {path}：{error}")
            return ""

        visited.add(resolved)
        lines: list[str] = []
        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if match is None:
                lines.append(line)
                continue
            target = (resolved.parent / match.group(1)).resolve()
            if not _is_within(target, allowed_root):
                warnings.append(f"已跳过越界指令引用：{match.group(1)}")
                continue
            if not target.is_file():
                warnings.append(f"找不到被引用的指令文件：{match.group(1)}")
                continue
            included = self._expand(target, allowed_root, depth + 1, visited, warnings)
            if included:
                lines.append(included)
        visited.remove(resolved)
        return "\n".join(lines)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
