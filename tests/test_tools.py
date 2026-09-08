from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from yucode.cancellation import Cancellation
from yucode.permissions import ApprovalChoice, PermissionManager, PermissionMode, TaskAuthorization
from yucode.tools.base import ToolCall, ToolContext, ToolDefinition, ToolResult, ToolSafety
from yucode.tools.command import RunCommandTool
from yucode.tools.executor import ToolExecutor
from yucode.tools.filesystem import EditFileTool, FindFilesTool, ReadFileTool, SearchCodeTool, WriteFileTool
from yucode.tools.registry import ToolRegistry
from yucode.workflow import ToolWorkflow


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(tmp_path)


def run_tool(tool, arguments, tmp_path: Path, call_id="1"):
    return asyncio.run(tool.execute(arguments, context(tmp_path), call_id, Cancellation()))


def test_read_write_and_unique_edit(tmp_path: Path) -> None:
    write = WriteFileTool()
    read = ReadFileTool()
    edit = EditFileTool()

    assert run_tool(write, {"path": "nested/a.txt", "content": "第一版"}, tmp_path, "1").success
    assert run_tool(write, {"path": "nested/a.txt", "content": "old old"}, tmp_path, "2").success
    assert run_tool(read, {"path": "nested/a.txt"}, tmp_path, "3").content == "old old"
    failed = run_tool(edit, {"path": "nested/a.txt", "old_text": "old", "new_text": "new"}, tmp_path, "4")
    assert failed.error_code == "multiple_matches"
    assert (tmp_path / "nested/a.txt").read_text(encoding="utf-8") == "old old"
    assert run_tool(edit, {"path": "nested/a.txt", "old_text": "old old", "new_text": "new"}, tmp_path, "5").success
    assert (tmp_path / "nested/a.txt").read_text(encoding="utf-8") == "new"


def test_file_tools_reject_workspace_escape_and_binary(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = run_tool(ReadFileTool(), {"path": "../outside.txt"}, tmp_path)
    assert result.error_code == "path_outside_workspace"
    (tmp_path / "binary.bin").write_bytes(b"a\0b")
    assert run_tool(ReadFileTool(), {"path": "binary.bin"}, tmp_path, "2").error_code == "binary_file"


def test_utf16_text_can_be_read_then_safely_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "hello.txt"
    path.write_bytes("1\r\n".encode("utf-16"))
    registry = ToolRegistry(tmp_path)
    executor = ToolExecutor(
        registry,
        PermissionManager(tmp_path, PermissionMode.ACCEPT_EDITS),
    )
    workflow = ToolWorkflow(tmp_path)

    async def overwrite() -> tuple[ToolResult, ToolResult]:
        read = await executor.execute(
            ToolCall("read", "read_file", {"file_path": "hello.txt"}),
            Cancellation(),
            TaskAuthorization.EXECUTE,
            workflow,
        )
        write = await executor.execute(
            ToolCall("write", "write_file", {"path": "hello.txt", "content": "2"}),
            Cancellation(),
            TaskAuthorization.EXECUTE,
            workflow,
        )
        return read, write

    read, write = asyncio.run(overwrite())

    assert read.success and read.content == "1\r\n"
    assert write.success
    assert path.read_text(encoding="utf-8") == "2"


def test_file_tools_reject_symlink_escape_and_parent_glob(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-link-target.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    assert run_tool(ReadFileTool(), {"path": "outside-link.txt"}, tmp_path).error_code == "path_outside_workspace"
    assert run_tool(FindFilesTool(), {"pattern": "../*.txt"}, tmp_path).error_code == "invalid_pattern"


def test_read_file_requires_nonempty_path_and_explains_correction(tmp_path: Path) -> None:
    definition = ReadFileTool().definition
    assert definition.input_schema["properties"]["file_path"]["minLength"] == 1

    result = run_tool(ReadFileTool(), {}, tmp_path)

    assert result.error_code == "invalid_arguments"
    assert '{"file_path": "note.txt"}' in result.summary


def test_find_and_search_skip_generated_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("class Provider: pass\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("class Provider: pass\n", encoding="utf-8")
    found = run_tool(FindFilesTool(), {"pattern": "**/*.py"}, tmp_path, "1")
    searched = run_tool(SearchCodeTool(), {"pattern": "Provider"}, tmp_path, "2")
    assert found.content == "src/app.py"
    assert "src/app.py:1" in searched.content
    assert "hidden.py" not in searched.content


def test_search_reports_invalid_regex(tmp_path: Path) -> None:
    result = run_tool(SearchCodeTool(), {"pattern": "["}, tmp_path)
    assert result.error_code == "invalid_pattern"


def test_registry_registers_and_filters_tools_by_safety(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    assert {definition.name for definition in registry.definitions} == {
        "read_file", "write_file", "edit_file", "run_command", "find_files", "search_code"
    }
    assert {definition.name for definition in registry.read_only_definitions} == {
        "read_file", "find_files", "search_code"
    }


def test_executor_handles_async_command_rejection(tmp_path: Path) -> None:
    async def reject(_) -> ApprovalChoice:
        return ApprovalChoice.REJECT

    result = asyncio.run(
        ToolExecutor(ToolRegistry(tmp_path)).execute(
            ToolCall("1", "run_command", {"command": "Get-Location"}),
            Cancellation(),
            TaskAuthorization.EXECUTE,
            ToolWorkflow(tmp_path),
            reject,
        )
    )
    assert result.error_code == "permission_rejected"


def test_executor_never_starts_dangerous_call_even_in_bypass_mode(tmp_path: Path) -> None:
    class ProbeTool:
        safety = ToolSafety.SIDE_EFFECT
        definition = ToolDefinition("run_command", "probe", {"type": "object"})

        async def execute(self, *_args):
            raise AssertionError("危险命令不应进入真实工具")

    registry = ToolRegistry(tmp_path, (ProbeTool(),))
    result = asyncio.run(
        ToolExecutor(registry, PermissionManager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)).execute(
            ToolCall("1", "run_command", {"command": "git reset --hard"}),
            Cancellation(),
            TaskAuthorization.EXECUTE,
            ToolWorkflow(tmp_path),
        )
    )
    assert result.error_code == "dangerous_command"


def test_command_collects_output_and_nonzero_exit(tmp_path: Path) -> None:
    ok = run_tool(RunCommandTool(), {"command": "Write-Output hello"}, tmp_path, "1")
    failed = run_tool(RunCommandTool(), {"command": "Write-Error bad; exit 7"}, tmp_path, "2")
    assert ok.success and "hello" in ok.content
    assert failed.error_code == "nonzero_exit"


def test_command_timeout_is_structured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("yucode.tools.command.COMMAND_TIMEOUT_SECONDS", 0.05)
    result = run_tool(RunCommandTool(), {"command": "Start-Sleep 2"}, tmp_path)
    assert result.error_code == "timeout"


def test_command_cancellation_terminates_process(tmp_path: Path) -> None:
    async def scenario() -> ToolResult:
        cancellation = Cancellation()
        task = asyncio.create_task(
            RunCommandTool().execute(
                {"command": "Start-Sleep 2; Set-Content late.txt done"},
                context(tmp_path),
                "1",
                cancellation,
            )
        )
        await asyncio.sleep(0.1)
        cancellation.cancel()
        return await task

    result = asyncio.run(scenario())
    assert result.error_code == "cancelled"
    assert not (tmp_path / "late.txt").exists()


def test_executor_batches_reads_and_preserves_barriers_and_result_order(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], list[ToolResult]]:
        events: list[str] = []
        both_reads_started = asyncio.Event()
        started: set[str] = set()

        class FakeTool:
            def __init__(self, name: str, safety: ToolSafety) -> None:
                self.safety = safety
                self.definition = ToolDefinition(name, name, {"type": "object"})

            async def execute(self, _arguments, _context, call_id, _cancellation):
                events.append(f"start:{self.definition.name}")
                if self.definition.name in {"read_a", "read_b"}:
                    started.add(self.definition.name)
                    if len(started) == 2:
                        both_reads_started.set()
                    await asyncio.wait_for(both_reads_started.wait(), 0.5)
                events.append(f"end:{self.definition.name}")
                return ToolResult(call_id, self.definition.name, True, "ok")

        tools = (
            FakeTool("read_a", ToolSafety.READ_ONLY),
            FakeTool("read_b", ToolSafety.READ_ONLY),
            FakeTool("write_c", ToolSafety.SIDE_EFFECT),
            FakeTool("read_d", ToolSafety.READ_ONLY),
        )
        calls = [ToolCall(str(i), tool.definition.name, {}) for i, tool in enumerate(tools)]
        manager = PermissionManager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)
        results = await ToolExecutor(ToolRegistry(tmp_path, tools), manager).execute_many(
            calls,
            Cancellation(),
            TaskAuthorization.EXECUTE,
            ToolWorkflow(tmp_path),
        )
        return events, results

    events, results = asyncio.run(scenario())
    assert events.index("start:write_c") > events.index("end:read_a")
    assert events.index("start:write_c") > events.index("end:read_b")
    assert events.index("start:read_d") > events.index("end:write_c")
    assert [result.call_id for result in results] == ["0", "1", "2", "3"]


def test_executor_does_not_start_later_batch_after_cancel(tmp_path: Path) -> None:
    async def scenario() -> list[ToolResult]:
        cancellation = Cancellation()

        class CancellingTool:
            safety = ToolSafety.SIDE_EFFECT
            definition = ToolDefinition("cancel_now", "cancel", {"type": "object"})

            async def execute(self, _arguments, _context, call_id, token):
                token.cancel()
                return ToolResult(call_id, "cancel_now", False, "cancelled", error_code="cancelled")

        class LaterTool:
            safety = ToolSafety.SIDE_EFFECT
            definition = ToolDefinition("later", "later", {"type": "object"})

            async def execute(self, *_args):
                raise AssertionError("后续批次不应启动")

        registry = ToolRegistry(tmp_path, (CancellingTool(), LaterTool()))
        calls = [ToolCall("1", "cancel_now", {}), ToolCall("2", "later", {})]
        manager = PermissionManager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)
        return await ToolExecutor(registry, manager).execute_many(
            calls,
            cancellation,
            TaskAuthorization.EXECUTE,
            ToolWorkflow(tmp_path),
        )

    results = asyncio.run(scenario())
    assert [result.error_code for result in results] == ["cancelled", "cancelled"]
