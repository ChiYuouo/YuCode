from pathlib import Path

from yucode.prompting import RuntimeContext, SystemPromptBuilder
from yucode.providers.base import Message
from yucode.tools.base import ToolDefinition


def tools() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition("read_file", "读取文件。", {"type": "object"}),
        ToolDefinition("edit_file", "编辑文件。", {"type": "object"}),
        ToolDefinition("run_command", "执行命令。", {"type": "object"}),
    )


def build(iteration=1, mode="full", history=()):
    return SystemPromptBuilder().build(
        RuntimeContext(Path("C:/workspace"), mode, iteration), tools(), history
    )


def test_fixed_modules_have_priority_order_and_single_blank_separator() -> None:
    request = build()
    sections = request.stable_instructions.split("\n\n")
    assert [section.split("：", 1)[0] for section in sections] == [
        "身份", "系统约束", "任务模式", "动作执行", "工具使用", "语气风格", "文本输出",
    ]
    assert "工作目录：C:/workspace" not in request.stable_instructions
    assert "\n\n\n" not in request.stable_instructions


def test_optional_modules_follow_fixed_modules_without_empty_placeholders() -> None:
    builder = SystemPromptBuilder("自定义", ("Skill A", "Skill B"), "记忆")
    request = builder.build(RuntimeContext(Path("C:/workspace"), "full", 1), tools(), ())
    sections = request.stable_instructions.split("\n\n")
    assert sections[-3:] == ["自定义", "Skill A\nSkill B", "记忆"]
    assert all(section for section in sections)


def test_runtime_reminder_is_separate_and_uses_system_tag() -> None:
    request = build(2, "plan", (Message("user", "分析"),))
    assert request.history == (Message("user", "分析"),)
    reminder = request.runtime_messages[0].content
    assert reminder.startswith("<system-reminder>\n")
    assert reminder.endswith("\n</system-reminder>")
    assert "工作目录：C:/workspace" in reminder
    assert "规划模式：只读探索" in reminder
    assert reminder not in request.stable_instructions


def test_plan_reminder_is_full_on_first_and_every_fifth_iteration() -> None:
    reminders = {iteration: build(iteration, "plan").runtime_messages[0].content for iteration in range(1, 12)}
    for iteration in (1, 5, 10):
        assert "不得修改文件、执行命令" in reminders[iteration]
    for iteration in (2, 3, 4, 6, 7, 8, 9, 11):
        assert "只读探索并输出计划" in reminders[iteration]


def test_stable_prompt_and_cache_key_ignore_history_and_runtime_context() -> None:
    first = build(1, history=(Message("user", "第一个任务"),))
    second = SystemPromptBuilder().build(
        RuntimeContext(Path("C:/another-workspace"), "full", 2), tools(), (Message("user", "另一个任务"),)
    )
    assert first.stable_instructions == second.stable_instructions
    assert first.prompt_cache_key == second.prompt_cache_key
    assert first.runtime_messages != second.runtime_messages


def test_tool_descriptions_are_enhanced_without_changing_schema_or_original() -> None:
    original = tools()
    enhanced = SystemPromptBuilder().enhance_tools(original)
    assert [tool.name for tool in enhanced] == [tool.name for tool in original]
    assert [tool.input_schema for tool in enhanced] == [tool.input_schema for tool in original]
    assert original[1].description == "编辑文件。"
    assert '{"file_path": "note.txt"}' in enhanced[0].description
    assert "必须先用 read_file" in enhanced[1].description
    assert "不得用它替代" in enhanced[2].description


def test_strict_rules_and_runtime_authorization_use_unambiguous_language() -> None:
    request = SystemPromptBuilder().build(
        RuntimeContext(Path("C:/workspace"), "full", 1, "answer_only", ("a.txt",), True),
        tools(),
        (),
    )
    assert "不得猜测" in request.stable_instructions
    assert "必须使用它" in request.stable_instructions
    assert "必须用适用只读工具验证" in request.stable_instructions
    reminder = request.runtime_messages[0].content
    assert "回答优先" in reminder
    assert "待验证目标：a.txt" in reminder
    assert "违反授权或流程被拒绝" in reminder
