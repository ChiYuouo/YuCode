from pathlib import Path

from yucode.subagents.loader import AgentDefinitionLoader


def _write(root: Path, name: str, description: str = "角色") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n提示词", encoding="utf-8")


def test_loader_uses_project_over_user_builtin_and_plugin(tmp_path: Path) -> None:
    project, user, builtin, plugin = (tmp_path / "project", tmp_path / "user", tmp_path / "builtin", tmp_path / "plugin")
    _write(project / ".yucode" / "agents", "same", "project")
    _write(user, "same", "user")
    _write(builtin, "same", "builtin")
    _write(plugin, "same", "plugin")
    catalog = AgentDefinitionLoader(project, (plugin,), user, builtin).discover()
    assert catalog.get("same").description == "project"


def test_loader_reports_invalid_definition_and_loads_builtin(tmp_path: Path) -> None:
    root = tmp_path / "builtin"
    _write(root, "valid")
    (root / "bad.md").write_text("---\nname: bad\n---\n", encoding="utf-8")
    catalog = AgentDefinitionLoader(tmp_path, builtin_root=root).discover()
    assert catalog.get("valid") is not None
    assert len(catalog.diagnostics) == 1
    assert "description" in catalog.diagnostics[0].message
