"""Skill 专属工具、系统工具和白名单工具视图。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import time
from typing import Any

import httpx

from yucode.cancellation import Cancellation
from yucode.skills.models import ActiveSkill, SkillSnapshot, SkillToolManifest
from yucode.skills.runtime import SkillRuntime
from yucode.tools.base import Tool, ToolCatalog, ToolContext, ToolDefinition, ToolResult, ToolSafety, ToolView


SCRIPT_TIMEOUT_SECONDS = 30.0
MAX_REFERENCE_BYTES = 1_048_576


class ScriptSkillTool:
    def __init__(self, manifest: SkillToolManifest) -> None:
        self._manifest = manifest
        self._definition = ToolDefinition(manifest.name, manifest.description, manifest.input_schema)

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def safety(self) -> ToolSafety:
        return self._manifest.safety

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext, call_id: str, cancellation: Cancellation
    ) -> ToolResult:
        if cancellation.is_cancelled:
            return _failure(call_id, self.definition.name, "用户已取消。", "cancelled")
        command = tuple(_resolve_command_part(part, self._manifest.package_root) for part in self._manifest.command)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(context.root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return _failure(call_id, self.definition.name, f"无法启动 Skill 专属工具：{error}", "script_start_failed")
        payload = json.dumps({"arguments": dict(arguments)}, ensure_ascii=False).encode("utf-8")
        try:
            communicate = asyncio.create_task(process.communicate(payload))
            deadline = time.monotonic() + SCRIPT_TIMEOUT_SECONDS
            while not communicate.done():
                if cancellation.is_cancelled:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    return _failure(call_id, self.definition.name, "用户已取消。", "cancelled")
                if time.monotonic() >= deadline:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    communicate.cancel()
                    return _failure(call_id, self.definition.name, "Skill 专属工具执行超时。", "timeout")
                await asyncio.sleep(0.05)
            stdout, stderr = await communicate
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            return _failure(call_id, self.definition.name, "Skill 专属工具执行超时。", "timeout")
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            return _failure(call_id, self.definition.name, f"Skill 专属工具执行失败：{detail or process.returncode}", "script_failed")
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return _failure(call_id, self.definition.name, "Skill 专属工具没有返回有效 JSON。", "invalid_script_output")
        if not isinstance(result, Mapping) or not isinstance(result.get("success"), bool) or not isinstance(result.get("summary"), str):
            return _failure(call_id, self.definition.name, "Skill 专属工具返回格式不完整。", "invalid_script_output")
        content = result.get("content", "")
        if not isinstance(content, str):
            return _failure(call_id, self.definition.name, "Skill 专属工具返回的 content 必须是字符串。", "invalid_script_output")
        error_code = result.get("error_code")
        return ToolResult(call_id, self.definition.name, result["success"], result["summary"], content, error_code if isinstance(error_code, str) else None)


class SkillReferenceTool:
    safety = ToolSafety.READ_ONLY

    def __init__(self, active: Sequence[ActiveSkill]) -> None:
        self._active = {item.definition.name: item.definition for item in active if item.definition.resource_root is not None}
        self._definition = ToolDefinition(
            "read_skill_reference",
            "读取已激活目录 Skill 的 references/ 中的文本资源。",
            {
                "type": "object",
                "properties": {"skill": {"type": "string"}, "path": {"type": "string"}},
                "required": ["skill", "path"],
                "additionalProperties": False,
            },
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, Any], _context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        skill = arguments.get("skill")
        relative = arguments.get("path")
        if not isinstance(skill, str) or not isinstance(relative, str) or not relative:
            return _failure(call_id, self.definition.name, "参数必须包含 skill 和非空 path。", "invalid_arguments")
        definition = self._active.get(skill)
        if definition is None or definition.resource_root is None:
            return _failure(call_id, self.definition.name, "该 Skill 没有可读取的参考资源。", "unknown_skill")
        try:
            target = (definition.resource_root / relative).resolve()
            target.relative_to(definition.resource_root.resolve())
            if not target.is_file() or target.is_symlink():
                raise ValueError
            if target.stat().st_size > MAX_REFERENCE_BYTES:
                return _failure(call_id, self.definition.name, "参考资源超过大小限制。", "file_too_large")
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            return _failure(call_id, self.definition.name, "参考资源不存在、不是文本或越出能力包目录。", "invalid_reference")
        if cancellation.is_cancelled:
            return _failure(call_id, self.definition.name, "用户已取消。", "cancelled")
        return ToolResult(call_id, self.definition.name, True, f"已读取 {skill} 的参考资源。", content)


class LoadSkillTool:
    safety = ToolSafety.READ_ONLY

    def __init__(self, runtime: SkillRuntime) -> None:
        self._runtime = runtime
        self._definition = ToolDefinition(
            "LoadSkill",
            "按名称加载 Skill 的完整 SOP 和专属工具。需要时传入 arguments。",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "arguments": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, Any], _context: ToolContext, call_id: str, _cancellation: Cancellation) -> ToolResult:
        name, values = arguments.get("name"), arguments.get("arguments", "")
        if not isinstance(name, str) or not name.strip() or not isinstance(values, str):
            return _failure(call_id, self.definition.name, "name 必须是非空字符串，arguments 必须是字符串。", "invalid_arguments")
        try:
            active = self._runtime.load(name, values)
        except ValueError as error:
            return _failure(call_id, self.definition.name, str(error), "skill_not_found")
        return ToolResult(call_id, self.definition.name, True, f"已加载 Skill：{active.definition.name}。下一轮会使用其完整指令和工具范围。")


class InstallSkillTool:
    safety = ToolSafety.SIDE_EFFECT

    def __init__(self, runtime: SkillRuntime) -> None:
        self._runtime = runtime
        self._definition = ToolDefinition(
            "InstallSkill",
            "从用户明确提供或授权的直接 URL 安装一个第三方 Skill。",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False},
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, Any], _context: ToolContext, call_id: str, _cancellation: Cancellation) -> ToolResult:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            return _failure(call_id, self.definition.name, "url 必须是非空字符串。", "invalid_arguments")
        try:
            result = await self._runtime.install(url)
        except (ValueError, OSError, httpx.HTTPError) as error:
            return _failure(call_id, self.definition.name, f"安装 Skill 失败：{error}", "install_failed")
        active = self._runtime.load(result.name)
        action = "已存在，已复用并激活" if result.already_installed else "已安装并激活"
        return ToolResult(call_id, self.definition.name, True, f"Skill {active.definition.name}{action}。无需重启即可使用 /skill:{active.definition.name}。")


def build_skill_view(
    catalog: ToolCatalog,
    snapshot: SkillSnapshot,
    system_tools: Sequence[Tool] = (),
) -> ToolView:
    """从一次 Skill 快照构造当前模型可见的工具集合。"""
    base = {definition.name: catalog.get(definition.name) for definition in catalog.definitions}
    base = {name: tool for name, tool in base.items() if tool is not None}
    selected: dict[str, Tool] = {}
    if not snapshot.active:
        selected.update(base)
    else:
        allowed = set().union(*(item.definition.allowed_tools for item in snapshot.active))
        selected.update({name: base[name] for name in allowed if name in base})
        private: dict[str, Tool] = {}
        for item in snapshot.active:
            for manifest in item.definition.private_tools:
                private[manifest.name] = ScriptSkillTool(manifest)
        selected.update(private)
        if any(item.definition.resource_root is not None for item in snapshot.active):
            resource = SkillReferenceTool(snapshot.active)
            selected[resource.definition.name] = resource
    for tool in system_tools:
        selected[tool.definition.name] = tool
    return ToolView(catalog.context, selected)


def _resolve_command_part(value: str, root: Path) -> str:
    if value.startswith(".") or Path(value).suffix.lower() in {".py", ".ps1", ".sh", ".bat", ".cmd"}:
        return str((root / value).resolve())
    return value


def _failure(call_id: str, name: str, summary: str, code: str) -> ToolResult:
    return ToolResult(call_id, name, False, summary, error_code=code)
