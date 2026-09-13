from datetime import UTC, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from yucode.teams.mailbox import MailboxError, MailboxStore, _Lock
from yucode.teams.models import AgentTeam, MessageKind, TeamBackend, TeamMember


def test_mailbox_send_read_and_mark(tmp_path: Path) -> None:
    now = datetime.now(UTC); root = tmp_path / "demo"; root.mkdir()
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.IN_PROCESS)
    team = AgentTeam(1, "demo", "lead", root, (member,), now, now)
    store = MailboxStore()
    sent = store.send(team, "lead", ("alice",), "请读 README", kind=MessageKind.TEXT)
    assert sent[0].summary == "请读 README"
    assert store.unread_for(team, member)[0].sender == "lead"
    store.mark_read(team, member, (sent[0].message_id,))
    assert store.unread_for(team, member) == ()


def test_concurrent_senders_do_not_lose_messages(tmp_path: Path) -> None:
    now = datetime.now(UTC); root = tmp_path / "demo"; root.mkdir()
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.IN_PROCESS)
    team = AgentTeam(1, "demo", "lead", root, (member,), now, now)
    store = MailboxStore(lock_timeout_seconds=10, retry_seconds=0.005)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: store.send(team, "lead", ("alice",), f"message-{index}"), range(40)))
    assert len(store.unread_for(team, member)) == 40


def test_broadcast_excludes_sender_and_reaches_other_members_and_lead(tmp_path: Path) -> None:
    now = datetime.now(UTC); root = tmp_path / "demo"; root.mkdir()
    alice = TeamMember("alice", "a1", "dev", tmp_path, TeamBackend.IN_PROCESS)
    bob = TeamMember("bob", "b1", "reviewer", tmp_path, TeamBackend.IN_PROCESS)
    lead = TeamMember("lead", "lead", "lead", tmp_path, TeamBackend.IN_PROCESS)
    team = AgentTeam(1, "demo", "lead", root, (alice, bob), now, now)
    store = MailboxStore()
    sent = store.send(team, "alice", (), "完成", kind=MessageKind.BROADCAST)
    assert {item.recipients[0] for item in sent} == {"bob", "lead"}
    assert store.unread_for(team, alice) == ()
    assert len(store.unread_for(team, bob)) == len(store.unread_for(team, lead)) == 1


def test_unknown_recipient_is_rejected_without_creating_mailbox(tmp_path: Path) -> None:
    now = datetime.now(UTC); root = tmp_path / "demo"; root.mkdir()
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.IN_PROCESS)
    team = AgentTeam(1, "demo", "lead", root, (member,), now, now)
    with pytest.raises(MailboxError, match="收件人"):
        MailboxStore().send(team, "lead", ("mallory",), "越界")
    assert not (root / "members" / "mallory").exists()


def test_stale_lock_is_reclaimed_and_old_owner_cannot_remove_new_lock(tmp_path: Path) -> None:
    now = datetime.now(UTC); root = tmp_path / "demo"; root.mkdir()
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.IN_PROCESS)
    team = AgentTeam(1, "demo", "lead", root, (member,), now, now)
    lock_path = root / "members" / "alice" / "mailbox.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(f"old\n{(now - timedelta(minutes=1)).isoformat()}", encoding="utf-8")
    assert MailboxStore(lock_timeout_seconds=0.05, retry_seconds=0.001).send(team, "lead", ("alice",), "ok")

    owner = _Lock(lock_path, 1, 0.001)
    owner.__enter__()
    lock_path.write_text(f"new-owner\n{datetime.now(UTC).isoformat()}", encoding="utf-8")
    owner.__exit__()
    assert lock_path.is_file()
