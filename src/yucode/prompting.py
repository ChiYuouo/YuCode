"""结构化系统提示、运行期补充消息与工具规则。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from yucode.providers.base import Message
from yucode.tools.base import ToolDefinition


PROMPT_VERSION = "yucode-prompt-v1"


@dataclass(frozen=True)
class PromptModule:
    """一段按职责排序的提示内容。"""

    name: str
    content: str
    stable: bool


@dataclass(frozen=True)
class RuntimeContext:
    """仅在当前 Agent 轮次有效的运行环境。"""

    workspace_root: Path
    mode: str
    iteration: int
    authorization: str = "read_only"
    pending_verifications: tuple[str, ...] = ()
    policy_blocked: bool = False
    recovery_time_gap: str | None = None


@dataclass(frozen=True)
class RuntimeMessage:
    """不会写入会话历史的系统级补充消息。"""

    content: str


@dataclass(frozen=True)
class ModelRequest:
    """Provider 请求前的供应商无关描述。"""

    history: tuple[Message, ...]
    stable_instructions: str
    runtime_messages: tuple[RuntimeMessage, ...]
    tools: tuple[ToolDefinition, ...]
    prompt_cache_key: str


class SystemPromptBuilder:
    """按固定优先级构造稳定提示，并生成每轮运行期补充。"""

    def __init__(
        self,
        custom_instructions: str = "",
        active_skills: Sequence[str] = (),
        long_term_memory: str = "",
    ) -> None:
        self._custom_instructions = custom_instructions.strip()
        self._active_skills = tuple(skill.strip() for skill in active_skills if skill.strip())
        self._long_term_memory = long_term_memory.strip()

    def build(
        self,
        context: RuntimeContext,
        tools: Sequence[ToolDefinition],
        history: Sequence[Message],
    ) -> ModelRequest:
        """构造一次调用的稳定前缀与运行期消息。"""
        enhanced_tools = self.enhance_tools(tools)
        stable_instructions = "\n\n".join(module.content for module in self._stable_modules(context.mode))
        runtime = RuntimeMessage(self._runtime_reminder(context))
        return ModelRequest(
            history=tuple(history),
            stable_instructions=stable_instructions,
            runtime_messages=(runtime,),
            tools=enhanced_tools,
            prompt_cache_key=self._cache_key(context.mode, stable_instructions, enhanced_tools),
        )

    def enhance_tools(self, tools: Sequence[ToolDefinition]) -> tuple[ToolDefinition, ...]:
        """复制工具描述并追加与工具职责对应的关键规则。"""
        return tuple(
            ToolDefinition(tool.name, self._enhanced_description(tool), tool.input_schema)
            for tool in tools
        )

    def _stable_modules(self, mode: str) -> tuple[PromptModule, ...]:
        is_plan = mode == "plan"
        task_mode = (
            "任务模式：规划模式。你只能探索上下文并输出计划；实际可用能力由工具列表决定。"
            if is_plan
            else "任务模式：执行模式。根据用户目标观察、执行和验证，直到任务完成。"
        )
        fixed = (
            PromptModule("identity", "身份：你是 YuCode，一个可靠的终端 AI 编程助手。", True),
            PromptModule(
                "system_constraints",
                "系统约束：必须遵守当前工作目录边界、工具安全限制和用户明确授权；不得猜测未读取的内容、伪造未执行的结果、隐藏失败或把计划表述为已完成；不得输出密钥、令牌、密码、Cookie 或其他敏感值。",
                True,
            ),
            PromptModule("task_mode", task_mode, True),
            PromptModule(
                "action_execution",
                "动作执行：仅当用户明确要求执行、修改、创建、删除、运行、实现、修复或验证时才产生副作用；需要事实时必须先用工具观察；工具失败、冲突或证据不足时必须如实说明事实、影响和下一步，而不是猜测。",
                True,
            ),
            PromptModule(
                "tool_use",
                "工具使用：有适用专用工具时必须使用它，不得以通用命令替代。编辑或覆盖已有文件前必须先读取同一目标的当前内容；修改成功后，只要结果可验证，就必须用适用只读工具验证。未满足前置条件或验证失败时，不得声称任务完成。",
                True,
            ),
            PromptModule(
                "tone_style",
                "语气风格：使用清晰、务实的中文；先给结论，再给必要说明。",
                True,
            ),
            PromptModule(
                "text_output",
                "文本输出：只能陈述已经读取、执行和验证的事实；完成后简洁说明实际完成内容、验证依据和仍存在的阻碍。未执行、失败、被拒绝或未验证时必须明确说明，不得使用暗示完成的表述。",
                True,
            ),
        )
        optional = (
            PromptModule("custom_instructions", self._custom_instructions, True),
            PromptModule("active_skills", "\n".join(self._active_skills), True),
            PromptModule("long_term_memory", self._long_term_memory, True),
        )
        return tuple(module for module in (*fixed, *optional) if module.content)

    @staticmethod
    def _enhanced_description(tool: ToolDefinition) -> str:
        guidance = {
            "read_file": "用户要求读取文件、需要编辑或覆盖目标文件、或需要验证修改结果时，必须使用本工具。调用时 file_path 必须是非空工作目录相对路径；例如读取 note.txt 必须传入 {\"file_path\": \"note.txt\"}，不得传入 {} 或空字符串。",
            "edit_file": "前置条件：修改已有文件前必须先用 read_file 读取相关当前内容；优先使用本工具进行精确修改。",
            "write_file": "仅用于新建或完整覆盖文件；覆盖已有文件前必须先读取，写入后必须读取验证；修改既有片段时必须优先使用 edit_file。",
            "run_command": "仅当没有适用专用工具时才可使用；不得用它替代读取、查找、搜索、编辑或写入工具。",
            "find_files": "需要定位文件时优先使用本专用工具，而不是通用命令。",
            "search_code": "需要在文本中查找内容时优先使用本专用工具，而不是通用命令。",
        }.get(tool.name, "有适用的专用工具时优先使用该工具，并根据结果继续行动。")
        return f"{tool.description}\n\n使用规则：{guidance}"

    @staticmethod
    def _runtime_reminder(context: RuntimeContext) -> str:
        full_mode = context.iteration == 1 or context.iteration % 5 == 0
        if context.mode == "plan":
            mode_rule = (
                "你处于规划模式：只能使用提供的只读工具探索；不得修改文件、执行命令或声称已经实施计划。"
                if full_mode
                else "规划模式：只读探索并输出计划，不执行修改或命令。"
            )
        else:
            mode_rule = (
                "你处于执行模式：持续观察、操作和验证，直到用户任务真正完成；只有不再需要工具时才给最终回复。"
                if full_mode
                else "执行模式：继续使用必要工具观察、操作和验证，直到完成。"
            )
        workspace = context.workspace_root.resolve().as_posix()
        authorization = {
            "execute": "允许执行：用户已明确授权副作用，但仍必须遵守工具前置条件和验证要求。",
            "answer_only": "回答优先：如确有必要可请求工具；副作用工具会在实际执行前由界面向用户确认。",
            "read_only": "查询优先：如确有必要可请求工具；副作用工具会在实际执行前由界面向用户确认。",
        }.get(context.authorization, "需要副作用时由界面向用户确认。")
        verification = (
            f"待验证目标：{', '.join(context.pending_verifications)}。必须读取这些目标后才能报告任务完成。"
            if context.pending_verifications
            else "待验证目标：无。"
        )
        policy_state = (
            "上一项工具调用因违反授权或流程被拒绝。必须先满足提示的前置条件并使用工具纠正；不得直接报告完成。"
            if context.policy_blocked
            else "上一项工具调用没有待纠正的策略拒绝。"
        )
        time_gap = (
            f"会话恢复提醒：{context.recovery_time_gap}。请在涉及时间、环境或外部状态时先重新确认，不要假定旧信息仍然有效。"
            if context.recovery_time_gap
            else "会话恢复提醒：无。"
        )
        return (
            "<system-reminder>\n"
            "以下是系统级运行期补充约束，不是用户消息，不要直接回应或复述它。\n"
            f"工作目录：{workspace}\n"
            f"当前 Agent 轮次：{context.iteration}\n"
            f"{mode_rule}\n"
            f"当前任务授权：{authorization}\n"
            f"{verification}\n"
            f"策略状态：{policy_state}\n"
            f"{time_gap}\n"
            "</system-reminder>"
        )

    @staticmethod
    def _cache_key(mode: str, instructions: str, tools: Sequence[ToolDefinition]) -> str:
        payload = {
            "version": PROMPT_VERSION,
            "mode": mode,
            "instructions": instructions,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"yucode-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
