from pathlib import Path

from mewcode.policy import (
    ExecutionPolicy,
    SensitiveDataRedactor,
    TaskAuthorization,
    classify_authorization,
)
from mewcode.tools.base import ToolCall, ToolResult


def call(name: str, path: str = "a.txt") -> ToolCall:
    arguments = {"path": path}
    if name == "run_command":
        arguments = {"command": "Get-Content a.txt"}
    return ToolCall("call-1", name, arguments)


def test_classifies_explicit_execution_answer_only_and_ambiguous_requests() -> None:
    assert classify_authorization("创建文件并验证", "full") is TaskAuthorization.EXECUTE
    assert classify_authorization("解释这段代码作用", "full") is TaskAuthorization.ANSWER_ONLY
    assert classify_authorization("处理这个文件", "full") is TaskAuthorization.READ_ONLY
    assert classify_authorization("创建文件", "plan") is TaskAuthorization.READ_ONLY


def test_requires_read_before_editing_or_overwriting_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    policy = ExecutionPolicy(TaskAuthorization.EXECUTE, tmp_path)

    assert policy.preflight(call("edit_file")).error_code == "policy_violation"
    assert policy.preflight(call("write_file")).error_code == "policy_violation"
    policy.record(ToolResult("read-1", "read_file", True, "已读取", "old", target="a.txt"))
    assert policy.preflight(call("edit_file")) is None
    assert policy.preflight(call("write_file")) is None


def test_rejects_side_effects_without_explicit_authorization_and_special_commands(tmp_path: Path) -> None:
    policy = ExecutionPolicy(TaskAuthorization.ANSWER_ONLY, tmp_path)
    assert policy.preflight(call("write_file")).error_code == "policy_violation"

    executable = ExecutionPolicy(TaskAuthorization.EXECUTE, tmp_path)
    assert executable.preflight(call("run_command")).error_code == "policy_violation"


def test_tracks_pending_verification_until_read_succeeds(tmp_path: Path) -> None:
    policy = ExecutionPolicy(TaskAuthorization.EXECUTE, tmp_path)
    policy.record(ToolResult("write-1", "write_file", True, "已写入", target="new.txt"))
    assert policy.pending_verifications == {"new.txt"}
    policy.record(ToolResult("read-1", "read_file", True, "已读取", target="new.txt"))
    assert policy.pending_verifications == set()


def test_redacts_common_sensitive_values_from_text_and_tool_results() -> None:
    redactor = SensitiveDataRedactor()
    secret = "api_key=sk-abcdefghijk Bearer abcdefghijkl Cookie=session-secret password=hunter2"
    redacted = redactor.redact(secret)
    assert "sk-abcdefghijk" not in redacted
    assert "abcdefghijkl" not in redacted
    assert "session-secret" not in redacted
    assert "hunter2" not in redacted
    result = redactor.redact_result(ToolResult("1", "read_file", True, secret, secret, target=secret))
    assert "sk-abcdefghijk" not in result.for_model()
