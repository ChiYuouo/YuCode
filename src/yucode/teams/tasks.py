"""团队共享任务及其简单依赖图。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from yucode.teams.models import AgentTeam, TeamTask, TeamTaskState
from yucode.teams.mailbox import _Lock


class TeamTaskError(ValueError):
    pass


class TeamTaskStore:
    def __init__(self, now=None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def list(self, team: AgentTeam) -> tuple[TeamTask, ...]:
        path = self._path(team)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(path.with_suffix(".lock"), 10.0, 0.02):
            return self._read(path)

    @staticmethod
    def _read(path: Path) -> tuple[TeamTask, ...]:
        if not path.is_file():
            return ()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("任务文件不是列表")
            return tuple(_decode(item) for item in raw)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
            raise TeamTaskError(f"团队任务数据无效：{error}") from error

    def create(self, team: AgentTeam, title: str, detail: str, *, assignee: str | None = None,
               dependencies: tuple[str, ...] = ()) -> TeamTask:
        if not isinstance(title, str) or not title.strip():
            raise TeamTaskError("任务标题必须是非空字符串。")
        if not isinstance(detail, str):
            raise TeamTaskError("任务详情必须是字符串。")
        path = self._path(team); path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(path.with_suffix(".lock"), 10.0, 0.02):
            existing = self._read(path)
            self._validate(team, existing, dependencies, assignee)
            now = self._now()
            task = TeamTask(uuid4().hex, title.strip(), detail, assignee, dependencies,
                            TeamTaskState.READY if not dependencies else TeamTaskState.BLOCKED, now, now)
            self._save_path(path, (*existing, task))
            return task

    def get(self, team: AgentTeam, task_id: str) -> TeamTask:
        return self._find(self.list(team), task_id)

    def blocks(self, team: AgentTeam, task_id: str) -> tuple[str, ...]:
        return tuple(item.task_id for item in self.list(team) if task_id in item.dependencies)

    def update(self, team: AgentTeam, task_id: str, *, state: TeamTaskState | None = None,
               assignee: str | None = None, dependencies: tuple[str, ...] | None = None) -> TeamTask:
        path = self._path(team); path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(path.with_suffix(".lock"), 10.0, 0.02):
            items = list(self._read(path))
            current = self._find(items, task_id)
            next_deps = current.dependencies if dependencies is None else dependencies
            next_assignee = current.assignee if assignee is None else assignee
            self._validate(team, tuple(item for item in items if item.task_id != task_id), next_deps, next_assignee, task_id)
            if state is not None:
                self._validate_transition(current.state, state)
            changed = replace(current, state=state or current.state, assignee=next_assignee,
                              dependencies=next_deps, updated_at=self._now())
            items[items.index(current)] = changed
            normalized = self._recalculate(tuple(items))
            self._save_path(path, normalized)
            return self._find(normalized, task_id)

    def _validate(self, team: AgentTeam, items: tuple[TeamTask, ...], dependencies: tuple[str, ...],
                  assignee: str | None, task_id: str | None = None) -> None:
        if not isinstance(dependencies, tuple) or not all(isinstance(item, str) and item for item in dependencies):
            raise TeamTaskError("dependencies 必须是任务标识列表。")
        if len(set(dependencies)) != len(dependencies):
            raise TeamTaskError("任务依赖不能重复。")
        if task_id is not None and task_id in dependencies:
            raise TeamTaskError("任务不能依赖自身。")
        known = {item.task_id for item in items}
        if not set(dependencies).issubset(known):
            raise TeamTaskError("任务依赖中包含不存在的任务。")
        if assignee is not None and assignee not in {item.name for item in team.members}:
            raise TeamTaskError("任务负责人必须是当前团队成员。")
        candidate = {item.task_id: item.dependencies for item in items}
        if task_id is not None:
            candidate[task_id] = dependencies
        if _has_cycle(candidate):
            raise TeamTaskError("任务依赖不能形成循环。")

    @staticmethod
    def _validate_transition(previous: TeamTaskState, target: TeamTaskState) -> None:
        allowed = {
            TeamTaskState.BLOCKED: {TeamTaskState.CANCELLED},
            TeamTaskState.READY: {TeamTaskState.IN_PROGRESS, TeamTaskState.CANCELLED},
            TeamTaskState.IN_PROGRESS: {TeamTaskState.COMPLETED, TeamTaskState.CANCELLED, TeamTaskState.READY},
            TeamTaskState.COMPLETED: set(), TeamTaskState.CANCELLED: set(),
        }
        if target is previous:
            return
        if target not in allowed[previous]:
            raise TeamTaskError(f"任务状态不能从 {previous.value} 变为 {target.value}。")

    def _recalculate(self, items: tuple[TeamTask, ...]) -> tuple[TeamTask, ...]:
        states = {item.task_id: item.state for item in items}
        result: list[TeamTask] = []
        for item in items:
            if item.state in {TeamTaskState.COMPLETED, TeamTaskState.CANCELLED, TeamTaskState.IN_PROGRESS}:
                result.append(item)
                continue
            ready = all(states[dependency] is TeamTaskState.COMPLETED for dependency in item.dependencies)
            result.append(replace(item, state=TeamTaskState.READY if ready else TeamTaskState.BLOCKED))
        return tuple(result)

    def _save_path(self, path: Path, items: tuple[TeamTask, ...]) -> None:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
            json.dump([_encode(item) for item in items], handle, ensure_ascii=False, separators=(",", ":"))
            temporary = Path(handle.name)
        temporary.replace(path)

    @staticmethod
    def _path(team: AgentTeam) -> Path:
        return team.root.resolve() / "tasks.json"

    @staticmethod
    def _find(items: tuple[TeamTask, ...] | list[TeamTask], task_id: str) -> TeamTask:
        for item in items:
            if item.task_id == task_id:
                return item
        raise TeamTaskError(f"找不到任务：{task_id}。")


def _has_cycle(edges: dict[str, tuple[str, ...]]) -> bool:
    visiting: set[str] = set(); seen: set[str] = set()
    def visit(value: str) -> bool:
        if value in visiting:
            return True
        if value in seen:
            return False
        visiting.add(value)
        result = any(visit(item) for item in edges.get(value, ()) if item in edges)
        visiting.remove(value); seen.add(value)
        return result
    return any(visit(item) for item in edges)


def _encode(item: TeamTask) -> dict:
    return {"task_id": item.task_id, "title": item.title, "detail": item.detail, "assignee": item.assignee,
            "dependencies": list(item.dependencies), "state": item.state.value,
            "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}


def _decode(raw: object) -> TeamTask:
    if not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) for key in ("task_id", "title", "detail", "state", "created_at", "updated_at")):
        raise ValueError("任务字段无效")
    dependencies = raw.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValueError("任务依赖无效")
    assignee = raw.get("assignee")
    if assignee is not None and not isinstance(assignee, str):
        raise ValueError("负责人无效")
    return TeamTask(raw["task_id"], raw["title"], raw["detail"], assignee, tuple(dependencies), TeamTaskState(raw["state"]),
                    datetime.fromisoformat(raw["created_at"]), datetime.fromisoformat(raw["updated_at"]))
