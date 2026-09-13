from pathlib import Path
import pytest

from yucode.teams.identity import TeamIdentityError, resolve_member_root, resolve_team_root, team_storage_root, validate_name


def test_team_paths_stay_inside_controlled_root(tmp_path: Path) -> None:
    team = resolve_team_root(tmp_path, "demo")
    assert team == tmp_path.resolve() / "demo"
    assert resolve_member_root(team, "alice") == team / "members" / "alice"


def test_default_team_storage_is_project_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "old-user-data"))
    legacy_root = tmp_path / ".mewcode" / "teams" / "legacy"
    legacy_root.mkdir(parents=True)
    (legacy_root / "team.json").write_text("{}", encoding="utf-8")

    assert team_storage_root(tmp_path) == tmp_path.resolve() / ".yucode" / "teams"
    assert not team_storage_root(tmp_path).exists()
    assert (legacy_root / "team.json").is_file()


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\\b", "空格", "a b"])
def test_rejects_unsafe_team_names(value: str) -> None:
    with pytest.raises(TeamIdentityError):
        validate_name(value)
