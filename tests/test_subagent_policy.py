from pathlib import Path

from yucode.subagents.loader import AgentDefinitionLoader
from yucode.subagents.policy import ToolPolicy, build_filtered_view
from yucode.tools.registry import ToolRegistry


def test_policy_intersects_all_limits_and_removes_agent(tmp_path: Path) -> None:
    root = tmp_path / "agents"; root.mkdir()
    (root / "role.md").write_text("---\nname: role\ndescription: r\ntools: [read_file, write_file, Agent]\ndisallowedTools: [write_file]\n---\n提示", encoding="utf-8")
    definition = AgentDefinitionLoader(tmp_path, builtin_root=root).discover().get("role")
    names = ToolPolicy(frozenset({"find_files"}), frozenset({"read_file"})).allowed_names(
        ("read_file", "write_file", "find_files", "Agent"), definition, background=True
    )
    assert names == frozenset({"read_file"})
    assert ToolPolicy().rejection_reason("write_file", names)


def test_filtered_view_only_exposes_allowed_tools(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path)
    view = build_filtered_view(registry, {"read_file"})
    assert [item.name for item in view.definitions] == ["read_file"]
    assert view.get("write_file") is None
