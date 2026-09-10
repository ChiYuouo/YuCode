from pathlib import Path

from yucode.instructions import INSTRUCTION_NAME, InstructionLoader


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_loads_three_instruction_levels_in_priority_order(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    write(tmp_path / INSTRUCTION_NAME, "根项目")
    write(tmp_path / ".yucode" / INSTRUCTION_NAME, "项目配置")
    write(user_home / ".yucode" / INSTRUCTION_NAME, "用户配置")

    result = InstructionLoader(user_home).load(tmp_path)

    assert result.content == "根项目\n\n项目配置\n\n用户配置"
    assert result.warnings == ()


def test_expands_relative_include(tmp_path: Path) -> None:
    write(tmp_path / INSTRUCTION_NAME, "根规则\n@include rules/detail.md\n结尾")
    write(tmp_path / "rules" / "detail.md", "细节规则")

    result = InstructionLoader(tmp_path / "user").load(tmp_path)

    assert result.content == "根规则\n细节规则\n结尾"


def test_skips_cycles_missing_depth_and_project_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-instruction.md"
    outside.write_text("不得读取", encoding="utf-8")
    write(tmp_path / INSTRUCTION_NAME, "@include a.md\n@include missing.md\n@include ../outside-instruction.md")
    write(tmp_path / "a.md", "A\n@include b.md")
    write(tmp_path / "b.md", "B\n@include a.md")

    result = InstructionLoader(tmp_path / "user").load(tmp_path)

    assert "A" in result.content and "B" in result.content
    assert "不得读取" not in result.content
    assert any("循环" in warning for warning in result.warnings)
    assert any("找不到" in warning for warning in result.warnings)
    assert any("越界" in warning for warning in result.warnings)


def test_skips_include_beyond_configured_depth(tmp_path: Path) -> None:
    write(tmp_path / INSTRUCTION_NAME, "@include a.md")
    write(tmp_path / "a.md", "A\n@include b.md")
    write(tmp_path / "b.md", "B")

    result = InstructionLoader(tmp_path / "user", max_include_depth=0).load(tmp_path)

    assert result.content == ""
    assert any("最大嵌套深度" in warning for warning in result.warnings)


def test_user_include_cannot_escape_user_config_root(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    write(user_home / ".yucode" / INSTRUCTION_NAME, "@include ../private.md")
    write(user_home / "private.md", "不得读取")

    result = InstructionLoader(user_home).load(tmp_path)

    assert result.content == ""
    assert any("越界" in warning for warning in result.warnings)


def test_missing_entries_are_ignored(tmp_path: Path) -> None:
    result = InstructionLoader(tmp_path / "user").load(tmp_path)

    assert result.content == ""
    assert result.warnings == ()
