from pathlib import Path

from yucode.tools.base import ToolCall, ToolResult
from yucode.workflow import ToolWorkflow


def call(name: str, path: str = "a.txt") -> ToolCall:
    arguments = {"path": path}
    if name == "run_command":
        arguments = {"command": "Get-Content a.txt"}
    return ToolCall("call-1", name, arguments)


def test_requires_read_before_editing_or_overwriting_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    workflow = ToolWorkflow(tmp_path)

    assert workflow.check(call("edit_file")).error_code == "workflow_precondition"
    assert workflow.check(call("write_file")).error_code == "workflow_precondition"
    workflow.record(ToolResult("read-1", "read_file", True, "已读取", "old", target="a.txt"))
    assert workflow.check(call("edit_file")) is None
    assert workflow.check(call("write_file")) is None


def test_requires_specialized_tool_instead_of_file_command(tmp_path: Path) -> None:
    issue = ToolWorkflow(tmp_path).check(call("run_command"))

    assert issue is not None
    assert issue.error_code == "workflow_precondition"
    assert "专用" in issue.reason


def test_tracks_pending_verification_until_successful_read(tmp_path: Path) -> None:
    workflow = ToolWorkflow(tmp_path)
    workflow.record(ToolResult("write-1", "write_file", True, "已写入", target="new.txt"))
    assert workflow.pending_verifications == {"new.txt"}

    workflow.record(ToolResult("failed", "read_file", False, "读取失败", target="new.txt"))
    assert workflow.pending_verifications == {"new.txt"}

    workflow.record(ToolResult("read-1", "read_file", True, "已读取", target="new.txt"))
    assert workflow.pending_verifications == set()
