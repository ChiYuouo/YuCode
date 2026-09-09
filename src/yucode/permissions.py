"""五层权限判断、规则加载与会话授权。"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from yucode.tools.base import Tool, ToolCall, ToolResult, ToolSafety
from yucode.tools.filesystem import WorkspacePathError, resolve_workspace_path, validate_workspace_glob
from yucode.workflow import ToolWorkflow


class PermissionMode(str, Enum):
    """当前会话的整体权限档位。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"


class RuleEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class RuleSource(str, Enum):
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"
    SESSION = "session"


class PermissionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalChoice(str, Enum):
    ONCE = "once"
    SESSION = "session"
    PERMANENT = "permanent"
    REJECT = "reject"


class TaskAuthorization(str, Enum):
    """本次用户输入允许产生的最高副作用级别。"""

    ANSWER_ONLY = "answer_only"
    READ_ONLY = "read_only"
    EXECUTE = "execute"


@dataclass(frozen=True)
class PermissionRule:
    tool_name: str
    pattern: str
    effect: RuleEffect
    source: RuleSource


@dataclass(frozen=True)
class RuleLayer:
    source: RuleSource
    rules: tuple[PermissionRule, ...]
    error: str | None = None


@dataclass(frozen=True)
class PermissionDecision:
    outcome: PermissionOutcome
    reason: str
    error_code: str | None = None
    source: RuleSource | None = None
    rule: PermissionRule | None = None


@dataclass(frozen=True)
class PermissionRequest:
    call: ToolCall
    summary: str
    impact: str


ApprovalCallback = Callable[[PermissionRequest], Awaitable[ApprovalChoice]]

_RULE_TEXT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.+)\)$")
_GLOB_CHARS = set("*?[")
_FILE_PATH_TOOLS = {"read_file", "write_file", "edit_file", "search_code"}
_EDIT_TOOLS = {"write_file", "edit_file"}
_RULE_SOURCES = (RuleSource.LOCAL, RuleSource.PROJECT, RuleSource.USER)
_EXECUTE_WORDS = (
    "创建", "新建", "写入", "写", "修改", "编辑", "删除", "执行", "运行", "实现", "修复", "重构", "更新", "添加", "验证",
    "create", "write", "edit", "modify", "delete", "run", "implement", "fix", "refactor", "update", "add", "verify",
)
_ANSWER_WORDS = (
    "解释", "说明", "介绍", "评审", "审查", "建议", "比较", "是什么", "为什么", "怎么", "是否",
    "explain", "review", "suggest", "compare", "what", "why", "how",
)

# 这些规则是固定硬边界，不读取任何配置，也没有关闭入口。
_DANGEROUS_COMMAND_PATTERNS = (
    re.compile(r"(?i)(?:^|[;|&]\s*)(?:rm|del|erase|rd|rmdir|remove-item)\b[^\n]*(?:\s/|\s[\\/]\*|\s[A-Za-z]:[\\/]?(?:\s|$)|\*\s*$)"),
    re.compile(r"(?i)\b(?:format(?:\.com)?|diskpart|clear-disk|initialize-disk|remove-partition)\b"),
    re.compile(r"(?i)\b(?:shutdown|restart-computer|stop-computer|reboot|halt)\b"),
    re.compile(r"(?i)(?:rm|del|erase|remove-item)\b[^\n]*(?:windows|system32|program\s+files|/etc|/usr|/bin)"),
    re.compile(r"(?i)\bgit\s+(?:clean\b[^\n]*-[^\n]*[fdx]|reset\s+--hard)"),
)


def classify_authorization(text: str, mode: str) -> TaskAuthorization:
    """保守地识别用户是否明确授权副作用。"""
    if mode == "plan":
        return TaskAuthorization.READ_ONLY
    normalized = text.lower()
    if any(word in normalized for word in _EXECUTE_WORDS):
        return TaskAuthorization.EXECUTE
    if any(word in normalized for word in _ANSWER_WORDS) or "?" in normalized or "？" in normalized:
        return TaskAuthorization.ANSWER_ONLY
    return TaskAuthorization.READ_ONLY


class SensitiveDataRedactor:
    """替换常见凭据值，避免它们进入模型历史或用户可见文本。"""

    _ASSIGNMENT = re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|password|passwd|cookie)\b\s*[:=]\s*)([^\s\"']+)"
    )
    _BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{6,}")
    _OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")

    def redact(self, text: str) -> str:
        """返回不含明显凭据值的文本。"""
        redacted = self._ASSIGNMENT.sub(r"\1[已脱敏]", text)
        redacted = self._BEARER.sub(r"\1[已脱敏]", redacted)
        return self._OPENAI_KEY.sub("[已脱敏]", redacted)

    def redact_result(self, result: ToolResult) -> ToolResult:
        return replace(
            result,
            summary=self.redact(result.summary),
            content=self.redact(result.content),
            target=self.redact(result.target),
        )


class LocalRuleStore:
    """管理三层 YAML 规则和项目本地规则的原子写入。"""

    def __init__(
        self,
        root: Path,
        user_path: Path | None = None,
        project_path: Path | None = None,
        local_path: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.user_path = user_path or Path.home() / ".yucode" / "permissions.yaml"
        self.project_path = project_path or self.root / "yucode.permissions.yaml"
        self.local_path = local_path or self.root / "yucode.permissions.local.yaml"

    def load_layers(self) -> tuple[RuleLayer, ...]:
        return (
            self._load_file(RuleSource.USER, self.user_path),
            self._load_file(RuleSource.PROJECT, self.project_path),
            self._load_file(RuleSource.LOCAL, self.local_path),
        )

    def append_exact_allow(self, rule: PermissionRule) -> None:
        """仅向项目本地文件追加已验证的精确允许规则。"""
        try:
            raw: Any = {}
            if self.local_path.exists():
                raw = yaml.safe_load(self.local_path.read_text(encoding="utf-8"))
            if raw is None:
                raw = {}
            if not isinstance(raw, Mapping):
                raise ValueError("本地规则文件顶层必须是键值对象。")
            rules = raw.get("rules", [])
            if not isinstance(rules, list):
                raise ValueError("本地规则文件的 rules 必须是列表。")
            copied = dict(raw)
            copied["rules"] = [*rules, {"rule": f"{rule.tool_name}({rule.pattern})", "action": "allow"}]
            self.local_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.local_path.name}.", suffix=".tmp", dir=self.local_path.parent, text=True
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    yaml.safe_dump(copied, handle, allow_unicode=True, sort_keys=False)
                os.replace(temporary, self.local_path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise ValueError(f"无法保存项目本地权限规则：{error}") from error

    def _load_file(self, source: RuleSource, path: Path) -> RuleLayer:
        if not path.exists():
            return RuleLayer(source, ())
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if raw is None:
                raw = {}
            if not isinstance(raw, Mapping):
                raise ValueError("顶层必须是键值对象。")
            entries = raw.get("rules", [])
            if not isinstance(entries, list):
                raise ValueError("rules 必须是列表。")
            rules = tuple(_parse_rule(entry, source) for entry in entries)
            return RuleLayer(source, rules)
        except (OSError, ValueError, yaml.YAMLError) as error:
            return RuleLayer(source, (), f"{source.value} 规则配置无效：{error}")


class PermissionManager:
    """以固定顺序完成黑名单、沙箱、规则、模式和确认判断。"""

    def __init__(
        self,
        root: Path,
        mode: PermissionMode = PermissionMode.DEFAULT,
        store: LocalRuleStore | None = None,
        redactor: SensitiveDataRedactor | None = None,
    ) -> None:
        self.root = root.resolve()
        self._mode = mode
        self._last_do_mode = mode if mode is not PermissionMode.PLAN else None
        self._store = store or LocalRuleStore(self.root)
        self._redactor = redactor or SensitiveDataRedactor()
        self._layers = {layer.source: layer for layer in self._store.load_layers()}
        self._session_rules: list[PermissionRule] = []

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        """切换会话权限状态，并记住最近一次可执行档位。"""
        if mode is PermissionMode.PLAN and self._mode is not PermissionMode.PLAN:
            self._last_do_mode = self._mode
        elif mode is not PermissionMode.PLAN:
            self._last_do_mode = mode
        self._mode = mode

    def resume_do_mode(self) -> PermissionMode:
        """从 Plan 恢复进入前的 Do 状态，首次恢复为 default。"""
        if self._mode is not PermissionMode.PLAN:
            return self._mode
        restored = self._last_do_mode or PermissionMode.DEFAULT
        self.set_mode(restored)
        return restored

    def evaluate(
        self,
        call: ToolCall,
        tool: Tool,
        authorization: TaskAuthorization,
        workflow: ToolWorkflow,
    ) -> PermissionDecision:
        """按固定顺序返回当前工具调用的唯一权限结果。"""
        command = _command_argument(call)
        if call.name == "run_command" and command is not None and _is_dangerous_command(command):
            return _deny("危险操作黑名单拒绝该命令，不能由规则、模式或确认放开。", "dangerous_command")

        path_error = self._path_error(call)
        if path_error is not None:
            return _deny(path_error, "path_outside_workspace")

        subject = _subject(call, self.root)
        matched = self._match_rules(call.name, subject)
        if matched is not None and matched.outcome is PermissionOutcome.DENY:
            return matched

        workflow_issue = workflow.check(call)
        if workflow_issue is not None:
            return _deny(workflow_issue.reason, workflow_issue.error_code)

        if tool.safety is ToolSafety.READ_ONLY:
            return matched or PermissionDecision(PermissionOutcome.ALLOW, "只读调用未命中规则，允许执行。")
        if self._mode is PermissionMode.PLAN:
            return _deny("当前为 Plan 权限模式，只允许只读工具。", "permission_plan")
        if matched is not None:
            return matched
        if self._mode is PermissionMode.BYPASS_PERMISSIONS:
            return PermissionDecision(PermissionOutcome.ALLOW, "bypassPermissions 模式允许未命中副作用调用。")
        if self._mode is PermissionMode.ACCEPT_EDITS and call.name in _EDIT_TOOLS:
            return PermissionDecision(PermissionOutcome.ALLOW, "acceptEdits 模式允许项目内文件编辑。")
        return PermissionDecision(PermissionOutcome.ASK, "该调用未命中权限规则，需要用户确认。")

    def request_for(self, call: ToolCall) -> PermissionRequest:
        subject = _subject(call, self.root)
        safe_subject = self._redactor.redact(subject or "<参数无效>")
        if call.name == "run_command":
            impact = "将在项目目录中运行命令。"
        elif call.name in _EDIT_TOOLS:
            impact = "将写入或修改项目内文件。"
        else:
            impact = "将执行有副作用的工具调用。"
        return PermissionRequest(call, f"工具 {call.name}：{safe_subject}", impact)

    async def resolve_prompt(
        self, request: PermissionRequest, approve: ApprovalCallback | None
    ) -> PermissionDecision:
        if approve is None:
            return _deny("当前没有可用的用户确认界面，调用未执行。", "permission_rejected")
        choice = await approve(request)
        if choice is ApprovalChoice.ONCE:
            return PermissionDecision(PermissionOutcome.ALLOW, "用户仅允许本次调用。")
        if choice is ApprovalChoice.SESSION:
            self.record_session_allow(request.call)
            return PermissionDecision(PermissionOutcome.ALLOW, "用户允许本会话中的同一调用。")
        if choice is ApprovalChoice.PERMANENT:
            try:
                self.persist_local_allow(request.call)
            except ValueError as error:
                return _deny(str(error), "permission_config_error")
            return PermissionDecision(PermissionOutcome.ALLOW, "用户永久允许当前项目中的同一调用。")
        return _deny("用户拒绝该调用，未执行。可改用安全方案或请求新的确认。", "permission_rejected")

    def record_session_allow(self, call: ToolCall) -> None:
        self._session_rules.append(_exact_allow(call, self.root, RuleSource.SESSION))

    def persist_local_allow(self, call: ToolCall) -> None:
        rule = _exact_allow(call, self.root, RuleSource.LOCAL)
        self._store.append_exact_allow(rule)
        previous = self._layers.get(RuleSource.LOCAL, RuleLayer(RuleSource.LOCAL, ()))
        self._layers[RuleSource.LOCAL] = RuleLayer(RuleSource.LOCAL, (*previous.rules, rule))

    def _path_error(self, call: ToolCall) -> str | None:
        if call.name in _FILE_PATH_TOOLS:
            value = _path_argument(call)
            if value is None:
                return None
            try:
                resolve_workspace_path(self.root, value)
            except WorkspacePathError:
                return "路径超出项目目录范围。请改用项目内相对路径。"
        elif call.name == "find_files":
            pattern = call.arguments.get("pattern")
            if isinstance(pattern, str):
                try:
                    validate_workspace_glob(pattern)
                except WorkspacePathError:
                    return "查找路径超出项目目录范围。请改用项目内 glob。"
        return None

    def _match_rules(self, tool_name: str, subject: str) -> PermissionDecision | None:
        for source in (RuleSource.SESSION, *_RULE_SOURCES):
            if source is RuleSource.SESSION:
                rules = tuple(self._session_rules)
                error = None
            else:
                layer = self._layers.get(source, RuleLayer(source, ()))
                rules, error = layer.rules, layer.error
            matches = [rule for rule in rules if rule.tool_name == tool_name and _matches(rule.pattern, subject)]
            denied = next((rule for rule in matches if rule.effect is RuleEffect.DENY), None)
            if denied is not None:
                return _deny(f"{source.value} 规则拒绝：{tool_name}({denied.pattern})。", "rule_denied", source, denied)
            if matches:
                rule = matches[0]
                return PermissionDecision(PermissionOutcome.ALLOW, f"命中 {source.value} allow 规则。", source=source, rule=rule)
            if error is not None:
                return _deny(error, "permission_config_error", source)
        return None


def _parse_rule(value: Any, source: RuleSource) -> PermissionRule:
    if not isinstance(value, Mapping):
        raise ValueError("每条规则必须是键值对象。")
    raw_rule, raw_action = value.get("rule"), value.get("action")
    if not isinstance(raw_rule, str) or not isinstance(raw_action, str):
        raise ValueError("规则必须包含字符串 rule 和 action。")
    match = _RULE_TEXT.fullmatch(raw_rule.strip())
    if match is None:
        raise ValueError("rule 必须采用 工具名(模式) 格式。")
    try:
        effect = RuleEffect(raw_action)
    except ValueError as error:
        raise ValueError("action 只能是 allow 或 deny。") from error
    return PermissionRule(match.group(1), match.group(2), effect, source)


def _matches(pattern: str, subject: str) -> bool:
    return fnmatchcase(subject, pattern) if any(char in pattern for char in _GLOB_CHARS) else subject == pattern


def _command_argument(call: ToolCall) -> str | None:
    value = call.arguments.get("command")
    return value if isinstance(value, str) else None


def _path_argument(call: ToolCall) -> str | None:
    if call.name == "read_file":
        value = call.arguments.get("file_path", call.arguments.get("path"))
    else:
        value = call.arguments.get("path")
    return value if isinstance(value, str) else None


def _subject(call: ToolCall, root: Path) -> str:
    if call.name == "run_command":
        return _command_argument(call) or ""
    if call.name in _FILE_PATH_TOOLS:
        value = _path_argument(call)
        if value is None:
            return ""
        try:
            return resolve_workspace_path(root, value).relative_to(root.resolve()).as_posix()
        except WorkspacePathError:
            return value
    if call.name == "find_files":
        value = call.arguments.get("pattern")
    else:
        value = call.arguments.get("pattern")
    return value if isinstance(value, str) else ""


def _exact_allow(call: ToolCall, root: Path, source: RuleSource) -> PermissionRule:
    return PermissionRule(call.name, _subject(call, root), RuleEffect.ALLOW, source)


def _is_dangerous_command(command: str) -> bool:
    return any(pattern.search(command) is not None for pattern in _DANGEROUS_COMMAND_PATTERNS)


def _deny(
    reason: str,
    error_code: str,
    source: RuleSource | None = None,
    rule: PermissionRule | None = None,
) -> PermissionDecision:
    return PermissionDecision(PermissionOutcome.DENY, reason, error_code, source, rule)
