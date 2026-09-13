"""以成员邮箱 JSONL 实现的可靠异步协作消息。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4
from tempfile import NamedTemporaryFile

from yucode.teams.identity import resolve_member_root
from yucode.teams.models import AgentTeam, MessageKind, TeamMember, TeamMessage


class MailboxError(ValueError):
    pass


class MailboxStore:
    def __init__(self, lock_timeout_seconds: float = 10.0, retry_seconds: float = 0.02, now=None) -> None:
        self._timeout = lock_timeout_seconds; self._retry = retry_seconds; self._now = now or (lambda: datetime.now(UTC))

    def send(self, team: AgentTeam, sender: str, recipients: tuple[str, ...], body: str, *,
             kind: MessageKind = MessageKind.TEXT, summary: str | None = None, protocol: dict[str, str] | None = None) -> tuple[TeamMessage, ...]:
        if sender not in {team.lead_id, *(item.name for item in team.members)}:
            raise MailboxError("发件人未在当前团队登记。")
        if not isinstance(body, str) or not body.strip():
            raise MailboxError("消息正文必须是非空字符串。")
        names = tuple(item.name for item in team.members)
        allowed = (*names, team.lead_id)
        selected = tuple(name for name in allowed if name != sender) if kind is MessageKind.BROADCAST else recipients
        if not selected or not set(selected).issubset(set(allowed)):
            raise MailboxError("收件人必须是当前团队成员或 Lead。")
        if len(set(selected)) != len(selected):
            raise MailboxError("收件人不能重复。")
        timestamp = self._now()
        result: list[TeamMessage] = []
        for recipient in selected:
            message = TeamMessage(uuid4().hex, sender, (recipient,), kind, body, summary or body.strip()[:120], timestamp,
                                  frozenset(), dict(protocol or {}))
            path = self._path(team, recipient)
            path.parent.mkdir(parents=True, exist_ok=True)
            with _Lock(path.with_suffix(".lock"), self._timeout, self._retry):
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(_encode(message), ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
            result.append(message)
        return tuple(result)

    def unread_for(self, team: AgentTeam, member: TeamMember) -> tuple[TeamMessage, ...]:
        return tuple(item for item in self._read(team, member.name) if member.name not in item.read_by)

    def mark_read(self, team: AgentTeam, member: TeamMember, ids: tuple[str, ...]) -> None:
        path = self._path(team, member.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(path.with_suffix(".lock"), self._timeout, self._retry):
            items = self._read_path(path)
            selected = set(ids)
            changed = tuple(TeamMessage(item.message_id, item.sender, item.recipients, item.kind, item.body, item.summary,
                                        item.created_at, item.read_by | {member.name} if item.message_id in selected else item.read_by, item.protocol) for item in items)
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
                handle.write("".join(json.dumps(_encode(item), ensure_ascii=False, separators=(",", ":")) + "\n" for item in changed))
                temporary = Path(handle.name)
            temporary.replace(path)

    def _read(self, team: AgentTeam, member_name: str) -> tuple[TeamMessage, ...]:
        path = self._path(team, member_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _Lock(path.with_suffix(".lock"), self._timeout, self._retry):
            return self._read_path(path)

    @staticmethod
    def _read_path(path: Path) -> tuple[TeamMessage, ...]:
        if not path.is_file():
            return ()
        try:
            return tuple(_decode(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise MailboxError(f"邮箱数据无效：{error}") from error

    @staticmethod
    def _path(team: AgentTeam, member_name: str) -> Path:
        return resolve_member_root(team.root, member_name) / "mailbox.jsonl"


class _Lock:
    def __init__(self, path: Path, timeout: float, retry: float) -> None:
        self._path = path; self._timeout = timeout; self._retry = retry; self._token = secrets.token_hex(16)

    def __enter__(self):
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                with self._path.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(self._token + "\n" + datetime.now(UTC).isoformat())
                return self
            except (FileExistsError, PermissionError):
                pass
            if self._expired():
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    time.sleep(self._retry)
                continue
            if time.monotonic() >= deadline:
                raise MailboxError("邮箱正被其他成员使用，请稍后重试。")
            time.sleep(self._retry)

    def _expired(self) -> bool:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            # 独占创建与写入之间可能短暂看到空文件；那是活跃锁，不是过期锁。
            if len(lines) < 2:
                return False
            created = datetime.fromisoformat(lines[1])
            return datetime.now(UTC) - created > timedelta(seconds=self._timeout)
        except (OSError, ValueError):
            return False

    def __exit__(self, *_):
        for _ in range(100):
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
                if not lines or lines[0] != self._token:
                    return
                self._path.unlink(missing_ok=True)
                return
            except PermissionError:
                time.sleep(self._retry)
            except (OSError, IndexError):
                return


def _encode(item: TeamMessage) -> dict:
    return {"message_id": item.message_id, "sender": item.sender, "recipients": list(item.recipients), "kind": item.kind.value,
            "body": item.body, "summary": item.summary, "created_at": item.created_at.isoformat(),
            "read_by": sorted(item.read_by), "protocol": dict(item.protocol)}


def _decode(line: str) -> TeamMessage:
    raw = json.loads(line)
    if not isinstance(raw, dict):
        raise ValueError("消息不是对象")
    required = ("message_id", "sender", "body", "summary", "created_at")
    if not all(isinstance(raw.get(key), str) for key in required):
        raise ValueError("消息字段无效")
    recipients = raw.get("recipients"); read_by = raw.get("read_by", []); protocol = raw.get("protocol", {})
    if not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients):
        raise ValueError("收件人无效")
    if (not isinstance(read_by, list) or not all(isinstance(item, str) for item in read_by)
            or not isinstance(protocol, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in protocol.items())):
        raise ValueError("消息状态无效")
    return TeamMessage(raw["message_id"], raw["sender"], tuple(recipients), MessageKind(raw.get("kind", "text")), raw["body"], raw["summary"],
                       datetime.fromisoformat(raw["created_at"]), frozenset(read_by), protocol)
