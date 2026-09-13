"""团队元数据的版本化、原子持久化。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import shutil

from yucode.teams.identity import TeamIdentityError, resolve_team_root, validate_name
from yucode.teams.models import AgentTeam, MemberState, TeamBackend, TeamMember
from yucode.teams.mailbox import _Lock


class TeamRepositoryError(ValueError):
    pass


class TeamRepository:
    def __init__(self, storage_root: Path) -> None:
        self._root = storage_root.resolve()

    def create(self, team: AgentTeam) -> AgentTeam:
        path = resolve_team_root(self._root, team.name)
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise TeamRepositoryError(f"团队已存在：{team.name}。")
        normalized = replace(team, root=path)
        try:
            with _Lock(path / "team.lock", 10.0, 0.02):
                self._atomic_json(path / "team.json", _encode(normalized))
        except Exception:
            # 新建阶段尚无用户数据，失败时仅移除仍为空的占位目录。
            try:
                path.rmdir()
            except OSError:
                pass
            raise
        return normalized

    def save(self, team: AgentTeam) -> AgentTeam:
        path = resolve_team_root(self._root, team.name)
        normalized = replace(team, root=path)
        path.mkdir(parents=True, exist_ok=True)
        with _Lock(path / "team.lock", 10.0, 0.02):
            self._atomic_json(path / "team.json", _encode(normalized))
        return normalized

    def load(self, name: str) -> AgentTeam:
        path = resolve_team_root(self._root, name)
        target = path / "team.json"
        if not target.is_file():
            raise TeamRepositoryError(f"找不到团队：{name}。")
        try:
            with _Lock(path / "team.lock", 10.0, 0.02):
                raw = json.loads(target.read_text(encoding="utf-8"))
                return _decode(raw, path)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, TeamIdentityError) as error:
            raise TeamRepositoryError(f"团队元数据无效：{error}") from error

    def update_member(self, name: str, member: TeamMember, updated_at: datetime) -> AgentTeam:
        path = resolve_team_root(self._root, name); target = path / "team.json"
        with _Lock(path / "team.lock", 10.0, 0.02):
            if not target.is_file():
                raise TeamRepositoryError(f"找不到团队：{name}。")
            try:
                team = _decode(json.loads(target.read_text(encoding="utf-8")), path)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise TeamRepositoryError(f"团队元数据无效：{error}") from error
            if member.name not in {item.name for item in team.members}:
                raise TeamRepositoryError(f"找不到成员：{member.name}。")
            changed = replace(team, members=tuple(member if item.name == member.name else item for item in team.members), updated_at=updated_at)
            self._atomic_json(target, _encode(changed))
            return changed

    def list(self) -> tuple[AgentTeam, ...]:
        if not self._root.is_dir():
            return ()
        items: list[AgentTeam] = []
        for path in self._root.iterdir():
            if not path.is_dir():
                continue
            try:
                items.append(self.load(path.name))
            except TeamRepositoryError:
                continue
        return tuple(sorted(items, key=lambda item: item.name))

    def delete_metadata(self, name: str) -> None:
        path = resolve_team_root(self._root, name)
        target = path / "team.json"
        if not target.is_file():
            raise TeamRepositoryError(f"找不到团队：{name}。")
        target.unlink()

    def delete(self, name: str) -> None:
        """仅删除经过名称解析且确属团队根下的单个团队目录。"""
        path = resolve_team_root(self._root, name)
        if not (path / "team.json").is_file():
            raise TeamRepositoryError(f"找不到团队：{name}。")
        shutil.rmtree(path)

    @staticmethod
    def _atomic_json(path: Path, value: dict) -> None:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            temp = Path(handle.name)
        temp.replace(path)


def _encode(team: AgentTeam) -> dict:
    return {
        "version": team.version, "name": team.name, "lead_id": team.lead_id,
        "created_at": team.created_at.isoformat(), "updated_at": team.updated_at.isoformat(),
        "members": [{
            "name": item.name, "agent_id": item.agent_id, "role": item.role,
            "workspace_root": str(item.workspace_root), "backend": item.backend.value,
            "requires_approval": item.requires_approval, "state": item.state.value,
            "worktree_slug": item.worktree_slug, "transcript_id": item.transcript_id,
            "backend_handle": item.backend_handle,
            "approval_request_id": item.approval_request_id, "approved_request_id": item.approved_request_id,
            "pending_prompt": item.pending_prompt,
            "pending_task_id": item.pending_task_id,
            "merged": item.merged,
            "plan_submitted_request_id": item.plan_submitted_request_id,
            "writable": item.writable,
        } for item in team.members],
    }


def _decode(raw: object, root: Path) -> AgentTeam:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("版本无效")
    name = validate_name(raw["name"], "团队名称")
    if resolve_team_root(root.parent, name) != root:
        raise ValueError("团队目录与元数据不一致")
    members_raw = raw.get("members")
    if not isinstance(members_raw, list):
        raise ValueError("members 必须是列表")
    members = tuple(_decode_member(item) for item in members_raw)
    if len({item.name for item in members}) != len(members):
        raise ValueError("成员名称重复")
    return AgentTeam(1, name, validate_name(raw["lead_id"], "负责人"), root, members,
                     datetime.fromisoformat(raw["created_at"]), datetime.fromisoformat(raw["updated_at"]))


def _decode_member(raw: object) -> TeamMember:
    if not isinstance(raw, dict):
        raise ValueError("成员必须是对象")
    writable = raw.get("writable", False)
    if not isinstance(writable, bool):
        raise ValueError("成员 writable 必须是布尔值")
    workspace = Path(raw["workspace_root"]).resolve()
    return TeamMember(
        validate_name(raw["name"], "成员名称"), validate_name(raw["agent_id"], "成员 ID"),
        raw["role"], workspace, TeamBackend(raw["backend"]), raw.get("requires_approval", False),
        MemberState(raw.get("state", "created")), raw.get("worktree_slug"), raw.get("transcript_id"), raw.get("backend_handle"),
        raw.get("approval_request_id"), raw.get("approved_request_id"), raw.get("pending_prompt"),
        raw.get("pending_task_id"),
        raw.get("merged", False),
        raw.get("plan_submitted_request_id"),
        writable,
    )
