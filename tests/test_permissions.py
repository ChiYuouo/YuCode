from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mewcode.permissions import (
    ApprovalChoice,
    LocalRuleStore,
    PermissionManager,
    PermissionMode,
    PermissionOutcome,
    RuleSource,
    SensitiveDataRedactor,
    TaskAuthorization,
    classify_authorization,
)
from mewcode.tools.base import ToolCall, ToolDefinition, ToolResult, ToolSafety
from mewcode.workflow import ToolWorkflow


class FakeTool:
    def __init__(self, name: str, safety: ToolSafety = ToolSafety.SIDE_EFFECT) -> None:
        self.safety = safety
        self.definition = ToolDefinition(name, name, {"type": "object"})


def call(name: str, value: str) -> ToolCall:
    arguments = {"command": value} if name == "run_command" else {"path": value}
    return ToolCall("call-1", name, arguments)


def manager(tmp_path: Path, mode=PermissionMode.DEFAULT, *, user=None, project=None, local=None) -> PermissionManager:
    store = LocalRuleStore(tmp_path, user, project, local)
    return PermissionManager(tmp_path, mode, store)


def evaluate(
    permissions: PermissionManager,
    target: ToolCall,
    tool: FakeTool,
    authorization: TaskAuthorization = TaskAuthorization.EXECUTE,
    workflow: ToolWorkflow | None = None,
):
    return permissions.evaluate(
        target,
        tool,
        authorization,
        workflow or ToolWorkflow(permissions.root),
    )


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "Remove-Item -Recurse -Force C:\\",
        "format C:",
        "diskpart",
        "shutdown /s",
        "git clean -fdx",
        "git reset --hard",
    ],
)
def test_dangerous_commands_are_never_overridden(command: str, tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text('rules:\n  - rule: "run_command(*)"\n    action: allow\n', encoding="utf-8")
    permissions = manager(tmp_path, PermissionMode.BYPASS_PERMISSIONS, local=local)

    result = evaluate(permissions, call("run_command", command), FakeTool("run_command"))

    assert result.outcome is PermissionOutcome.DENY
    assert result.error_code == "dangerous_command"


def test_file_path_escape_and_symlink_escape_are_denied(tmp_path: Path) -> None:
    outside = tmp_path.parent / "permission-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    permissions = manager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)

    assert evaluate(permissions, call("write_file", "../permission-outside.txt"), FakeTool("write_file")).error_code == "path_outside_workspace"
    assert evaluate(permissions, call("write_file", "inside.txt"), FakeTool("write_file")).outcome is PermissionOutcome.ALLOW

    link = tmp_path / "outside-link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")
    assert evaluate(permissions, call("edit_file", "outside-link"), FakeTool("edit_file")).error_code == "path_outside_workspace"


def test_rules_use_priority_and_deny_wins_within_a_layer(tmp_path: Path) -> None:
    user = tmp_path / "user.yaml"
    project = tmp_path / "project.yaml"
    local = tmp_path / "local.yaml"
    user.write_text('rules:\n  - rule: "write_file(a.txt)"\n    action: allow\n', encoding="utf-8")
    project.write_text('rules:\n  - rule: "write_file(a.txt)"\n    action: deny\n', encoding="utf-8")
    local.write_text('rules:\n  - rule: "write_file(a.txt)"\n    action: allow\n', encoding="utf-8")
    permissions = manager(tmp_path, user=user, project=project, local=local)
    target = call("write_file", "a.txt")

    assert evaluate(permissions, target, FakeTool("write_file")).source is RuleSource.LOCAL
    permissions.record_session_allow(target)
    assert evaluate(permissions, target, FakeTool("write_file")).source is RuleSource.SESSION

    local.write_text(
        'rules:\n  - rule: "write_file(a.txt)"\n    action: allow\n  - rule: "write_file(*)"\n    action: deny\n',
        encoding="utf-8",
    )
    conflict_permissions = manager(tmp_path, user=user, project=project, local=local)
    conflict = evaluate(conflict_permissions, target, FakeTool("write_file"))
    assert conflict.outcome is PermissionOutcome.DENY
    assert conflict.source is RuleSource.LOCAL


def test_exact_and_glob_rules_match_only_their_target(tmp_path: Path) -> None:
    project = tmp_path / "project.yaml"
    project.write_text(
        'rules:\n  - rule: "run_command(git *)"\n    action: allow\n  - rule: "write_file(.env)"\n    action: deny\n',
        encoding="utf-8",
    )
    permissions = manager(tmp_path, project=project)

    assert evaluate(permissions, call("run_command", "git status"), FakeTool("run_command")).outcome is PermissionOutcome.ALLOW
    assert evaluate(permissions, call("run_command", "python -V"), FakeTool("run_command")).outcome is PermissionOutcome.ASK
    assert evaluate(permissions, call("write_file", ".env"), FakeTool("write_file")).error_code == "rule_denied"
    assert evaluate(permissions, call("write_file", ".env.example"), FakeTool("write_file")).outcome is PermissionOutcome.ASK


def test_modes_only_change_unmatched_side_effects(tmp_path: Path) -> None:
    write = FakeTool("write_file")
    command = FakeTool("run_command")
    default = manager(tmp_path, PermissionMode.DEFAULT)
    accept = manager(tmp_path, PermissionMode.ACCEPT_EDITS)
    plan = manager(tmp_path, PermissionMode.PLAN)
    bypass = manager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)
    assert evaluate(default, call("write_file", "a.txt"), write).outcome is PermissionOutcome.ASK
    assert evaluate(accept, call("write_file", "a.txt"), write).outcome is PermissionOutcome.ALLOW
    assert evaluate(accept, call("run_command", "Get-Location"), command).outcome is PermissionOutcome.ASK
    assert evaluate(plan, call("write_file", "a.txt"), write).error_code == "permission_plan"
    assert evaluate(bypass, call("write_file", "a.txt"), write).outcome is PermissionOutcome.ALLOW
    assert evaluate(plan, call("read_file", "a.txt"), FakeTool("read_file", ToolSafety.READ_ONLY)).outcome is PermissionOutcome.ALLOW
    local = tmp_path / "local.yaml"
    local.write_text('rules:\n  - rule: "write_file(a.txt)"\n    action: allow\n', encoding="utf-8")
    plan_with_allow = manager(tmp_path, PermissionMode.PLAN, local=local)
    assert evaluate(plan_with_allow, call("write_file", "a.txt"), write).error_code == "permission_plan"


def test_plan_restores_last_do_mode_or_default(tmp_path: Path) -> None:
    permissions = manager(tmp_path, PermissionMode.ACCEPT_EDITS)
    permissions.set_mode(PermissionMode.PLAN)
    assert permissions.resume_do_mode() is PermissionMode.ACCEPT_EDITS

    permissions.set_mode(PermissionMode.BYPASS_PERMISSIONS)
    permissions.set_mode(PermissionMode.PLAN)
    assert permissions.resume_do_mode() is PermissionMode.BYPASS_PERMISSIONS

    first_plan = manager(tmp_path / "fresh", PermissionMode.PLAN)
    assert first_plan.resume_do_mode() is PermissionMode.DEFAULT
    assert first_plan.resume_do_mode() is PermissionMode.DEFAULT


def test_invalid_rule_file_denies_but_higher_session_rule_can_override(tmp_path: Path) -> None:
    project = tmp_path / "project.yaml"
    project.write_text("rules: not-a-list\n", encoding="utf-8")
    permissions = manager(tmp_path, project=project)
    target = call("write_file", "a.txt")
    assert evaluate(permissions, target, FakeTool("write_file")).error_code == "permission_config_error"
    permissions.record_session_allow(target)
    assert evaluate(permissions, target, FakeTool("write_file")).outcome is PermissionOutcome.ALLOW


def test_approval_scopes_persist_only_local_exact_rule_and_redact_summary(tmp_path: Path) -> None:
    local = tmp_path / "mewcode.permissions.local.yaml"
    permissions = manager(tmp_path, local=local)
    target = call("write_file", "a.txt")

    async def choose(choice: ApprovalChoice):
        return await permissions.resolve_prompt(permissions.request_for(target), lambda _: _choice(choice))

    assert asyncio.run(choose(ApprovalChoice.ONCE)).outcome is PermissionOutcome.ALLOW
    assert evaluate(permissions, target, FakeTool("write_file")).outcome is PermissionOutcome.ASK
    assert asyncio.run(choose(ApprovalChoice.SESSION)).outcome is PermissionOutcome.ALLOW
    assert evaluate(permissions, target, FakeTool("write_file")).source is RuleSource.SESSION

    other = call("write_file", "other.txt")
    assert asyncio.run(permissions.resolve_prompt(permissions.request_for(other), lambda _: _choice(ApprovalChoice.PERMANENT))).outcome is PermissionOutcome.ALLOW
    content = local.read_text(encoding="utf-8")
    assert "write_file(other.txt)" in content
    assert evaluate(permissions, other, FakeTool("write_file")).source is RuleSource.LOCAL

    secret = ToolCall("secret", "run_command", {"command": "Write-Output password=hunter2"})
    assert "hunter2" not in permissions.request_for(secret).summary


async def _choice(choice: ApprovalChoice) -> ApprovalChoice:
    return choice


def test_classifies_execution_intent_and_redacts_sensitive_values() -> None:
    assert classify_authorization("写一个数字 1 到 hello.txt", "full") is TaskAuthorization.EXECUTE
    assert classify_authorization("请评审这段代码", "full") is TaskAuthorization.ANSWER_ONLY
    assert classify_authorization("处理这个文件", "full") is TaskAuthorization.READ_ONLY
    assert classify_authorization("创建文件", "plan") is TaskAuthorization.READ_ONLY

    redactor = SensitiveDataRedactor()
    secret = "api_key=sk-abcdefghijk Bearer abcdefghijkl Cookie=session-secret password=hunter2"
    result = redactor.redact_result(ToolResult("1", "read_file", True, secret, secret, target=secret))
    assert all(value not in result.for_model() for value in ("sk-abcdefghijk", "abcdefghijkl", "session-secret", "hunter2"))


def test_unified_decision_distinguishes_intent_workflow_and_mode(tmp_path: Path) -> None:
    target = call("write_file", "existing.txt")
    (tmp_path / "existing.txt").write_text("old", encoding="utf-8")
    accept = manager(tmp_path, PermissionMode.ACCEPT_EDITS)
    workflow = ToolWorkflow(tmp_path)

    no_intent = evaluate(accept, target, FakeTool("write_file"), TaskAuthorization.ANSWER_ONLY, workflow)
    assert no_intent.error_code == "task_not_authorized"

    missing_read = evaluate(accept, target, FakeTool("write_file"), TaskAuthorization.EXECUTE, workflow)
    assert missing_read.error_code == "workflow_precondition"

    workflow.record(ToolResult("read", "read_file", True, "已读取", target="existing.txt"))
    allowed = evaluate(accept, target, FakeTool("write_file"), TaskAuthorization.EXECUTE, workflow)
    assert allowed.outcome is PermissionOutcome.ALLOW


@pytest.mark.parametrize("allow_source", ["accept_edits", "bypass", "session", "local"])
def test_allow_sources_cannot_skip_workflow_precondition(tmp_path: Path, allow_source: str) -> None:
    target = call("write_file", "existing.txt")
    (tmp_path / "existing.txt").write_text("old", encoding="utf-8")
    local = tmp_path / "local.yaml"

    if allow_source == "accept_edits":
        permissions = manager(tmp_path, PermissionMode.ACCEPT_EDITS)
    elif allow_source == "bypass":
        permissions = manager(tmp_path, PermissionMode.BYPASS_PERMISSIONS)
    elif allow_source == "session":
        permissions = manager(tmp_path)
        permissions.record_session_allow(target)
    else:
        local.write_text(
            'rules:\n  - rule: "write_file(existing.txt)"\n    action: allow\n',
            encoding="utf-8",
        )
        permissions = manager(tmp_path, local=local)

    result = evaluate(
        permissions,
        target,
        FakeTool("write_file"),
        TaskAuthorization.EXECUTE,
        ToolWorkflow(tmp_path),
    )

    assert result.outcome is PermissionOutcome.DENY
    assert result.error_code == "workflow_precondition"
