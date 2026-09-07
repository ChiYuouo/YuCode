from __future__ import annotations

import subprocess
from pathlib import Path

from mewcode.tools.base import ToolCall, ToolContext
from mewcode.tools.command import RunCommandTool
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.filesystem import EditFileTool, FindFilesTool, ReadFileTool, SearchCodeTool, WriteFileTool
from mewcode.tools.registry import ToolRegistry


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(tmp_path)


def test_read_write_and_unique_edit(tmp_path: Path) -> None:
    write = WriteFileTool()
    read = ReadFileTool()
    edit = EditFileTool()

    assert write.execute({"path": "nested/a.txt", "content": "第一版"}, context(tmp_path), "1").success
    assert write.execute({"path": "nested/a.txt", "content": "old old"}, context(tmp_path), "2").success
    assert read.execute({"path": "nested/a.txt"}, context(tmp_path), "3").content == "old old"
    failed = edit.execute({"path": "nested/a.txt", "old_text": "old", "new_text": "new"}, context(tmp_path), "4")
    assert failed.error_code == "multiple_matches"
    assert (tmp_path / "nested/a.txt").read_text(encoding="utf-8") == "old old"
    assert edit.execute({"path": "nested/a.txt", "old_text": "old old", "new_text": "new"}, context(tmp_path), "5").success
    assert (tmp_path / "nested/a.txt").read_text(encoding="utf-8") == "new"


def test_file_tools_reject_workspace_escape_and_binary(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    result = ReadFileTool().execute({"path": "../outside.txt"}, context(tmp_path), "1")
    assert result.error_code == "path_outside_workspace"
    (tmp_path / "binary.bin").write_bytes(b"a\0b")
    assert ReadFileTool().execute({"path": "binary.bin"}, context(tmp_path), "2").error_code == "binary_file"


def test_find_and_search_skip_generated_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("class Provider: pass\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("class Provider: pass\n", encoding="utf-8")
    found = FindFilesTool().execute({"pattern": "**/*.py"}, context(tmp_path), "1")
    searched = SearchCodeTool().execute({"pattern": "Provider"}, context(tmp_path), "2")
    assert found.content == "src/app.py"
    assert "src/app.py:1" in searched.content
    assert "hidden.py" not in searched.content


def test_search_reports_invalid_regex(tmp_path: Path) -> None:
    result = SearchCodeTool().execute({"pattern": "["}, context(tmp_path), "1")
    assert result.error_code == "invalid_pattern"


def test_registry_registers_all_tools_and_executor_handles_rejection(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    assert {definition.name for definition in registry.definitions} == {
        "read_file", "write_file", "edit_file", "run_command", "find_files", "search_code"
    }
    result = ToolExecutor(registry).execute(ToolCall("1", "run_command", {"command": "Get-Location"}), lambda _: False)
    assert result.error_code == "user_rejected"


def test_command_collects_output_and_nonzero_exit(tmp_path: Path) -> None:
    tool = RunCommandTool()
    ok = tool.execute({"command": "Write-Output hello"}, context(tmp_path), "1")
    failed = tool.execute({"command": "Write-Error bad; exit 7"}, context(tmp_path), "2")
    assert ok.success and "hello" in ok.content
    assert failed.error_code == "nonzero_exit"


def test_command_timeout_is_structured(tmp_path: Path, monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("powershell", 30)

    monkeypatch.setattr("mewcode.tools.command.subprocess.run", timeout)
    result = RunCommandTool().execute({"command": "Start-Sleep 31"}, context(tmp_path), "1")
    assert result.error_code == "timeout"
