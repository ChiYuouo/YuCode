from datetime import UTC, datetime
from pathlib import Path

from yucode.teams.models import AgentTeam, TeamBackend, TeamMember
from yucode.teams.repository import TeamRepository


def test_team_metadata_round_trip(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    repository = TeamRepository(tmp_path / "teams")
    team = AgentTeam(1, "demo", "lead", Path(), (TeamMember("alice", "agent1", "reader", tmp_path, TeamBackend.IN_PROCESS),), now, now)
    saved = repository.create(team)
    loaded = repository.load("demo")
    assert loaded.name == "demo"
    assert loaded.members[0].name == "alice"
    assert saved.root.joinpath("team.json").is_file()
