from yucode.teams.backends import BackendSelector, InProcessBackend, Iterm2Backend, TmuxBackend, TeamBackendError
from yucode.teams.models import TeamBackend, TeamMember
import pytest
import asyncio
import subprocess


def test_selector_uses_first_available_backend() -> None:
    selector = BackendSelector((Iterm2Backend(), InProcessBackend()))
    assert selector.select(("iterm2", "in_process")).backend is TeamBackend.IN_PROCESS


def test_explicit_unavailable_backend_does_not_fallback() -> None:
    selector = BackendSelector((Iterm2Backend(), InProcessBackend()))
    with pytest.raises(TeamBackendError):
        selector.select(("iterm2", "in_process"), TeamBackend.ITERM2)


def test_automatic_selection_reports_when_no_backend_is_available() -> None:
    selector = BackendSelector((Iterm2Backend(),))
    with pytest.raises(TeamBackendError, match="没有可用.*iterm2"):
        selector.select(("iterm2",))


def test_in_process_driver_can_start_wake_and_stop(tmp_path) -> None:
    started = asyncio.Event()
    async def worker(_member):
        started.set()
        await asyncio.Event().wait()
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.IN_PROCESS)
    async def scenario():
        driver = InProcessBackend(worker)
        handle = await driver.start(member, ())
        await started.wait()
        await driver.wake(member)
        assert driver.consume_wakeup(member).is_set()
        await driver.stop(member)
        assert handle.value == "a1"
    asyncio.run(scenario())


def test_tmux_driver_starts_and_wakes_recorded_pane(tmp_path) -> None:
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "%9\n" if len(calls) == 1 else "", "")
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.TMUX)
    async def scenario():
        driver = TmuxBackend(run)
        handle = await driver.start(member, ("python", "-m", "yucode.cli"))
        await driver.wake(TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.TMUX, backend_handle=handle.value))
    asyncio.run(scenario())
    assert "#{pane_id}" in calls[0]
    assert calls[1][:3] == ("tmux", "send-keys", "-t")


def test_iterm_driver_uses_returned_session_id(tmp_path) -> None:
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "session-1\n", "")
    member = TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.ITERM2)
    async def scenario():
        driver = Iterm2Backend(run)
        handle = await driver.start(member, ("python", "-m", "yucode.cli"))
        assert handle.value == "session-1"
        await driver.wake(TeamMember("alice", "a1", "reader", tmp_path, TeamBackend.ITERM2, backend_handle=handle.value))
    asyncio.run(scenario())
    assert calls[0][0] == "osascript" and "session-1" in calls[1][-1]
