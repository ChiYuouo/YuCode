from pathlib import Path

from yucode.skills.loader import SkillLoader


def write_skill(root: Path, name: str, *, mode: str = "inline", history: str = "none") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {name} 说明\nallowedTools:\n  - read_file\nmode: {mode}\nhistory: {history}\n---\n执行 $ARGUMENTS\n",
        encoding="utf-8",
    )
    return path


def test_discovers_single_file_and_replaces_arguments(tmp_path: Path) -> None:
    project = tmp_path / ".yucode" / "skills"
    write_skill(project, "demo")
    catalog = SkillLoader(tmp_path, tmp_path / "user", tmp_path / "builtin").discover()
    skill = catalog.get("demo")
    assert skill is not None
    assert skill.render("A  B") == "执行 A  B"
    assert skill.render("") == "执行 "


def test_project_overrides_user_and_builtin(tmp_path: Path) -> None:
    user, builtin = tmp_path / "user", tmp_path / "builtin"
    write_skill(builtin, "demo")
    write_skill(user, "demo")
    write_skill(tmp_path / ".yucode" / "skills", "demo")
    catalog = SkillLoader(tmp_path, user, builtin).discover()
    assert catalog.get("demo").source.tier == "project"


def test_bad_skill_is_skipped_without_hiding_good_skill(tmp_path: Path) -> None:
    root = tmp_path / ".yucode" / "skills"
    write_skill(root, "good")
    (root / "bad.md").write_text("---\nname: bad\n---\n", encoding="utf-8")
    catalog = SkillLoader(tmp_path, tmp_path / "user", tmp_path / "builtin").discover()
    assert catalog.get("good") is not None
    assert catalog.get("bad") is None
    assert catalog.diagnostics


def test_directory_package_combines_prompt_and_does_not_discover_auxiliary_files(tmp_path: Path) -> None:
    package = tmp_path / ".yucode" / "skills" / "packed"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: packed\ndescription: 包\nallowedTools:\n  - read_file\nmode: inline\nhistory: none\n---\n主提示\n",
        encoding="utf-8",
    )
    (package / "prompt.md").write_text("补充提示", encoding="utf-8")
    catalog = SkillLoader(tmp_path, tmp_path / "user", tmp_path / "builtin").discover()
    assert tuple(catalog.definitions) == ("packed",)
    assert "补充提示" in catalog.get("packed").sop
