"""本地 Team 成员后端的探测、启动和唤醒。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import platform
import shutil
import subprocess
import os
import shlex
from typing import Awaitable, Callable, Protocol

from yucode.teams.models import TeamBackend, TeamMember


@dataclass(frozen=True)
class BackendAvailability:
    backend: TeamBackend
    available: bool
    reason: str


@dataclass(frozen=True)
class BackendHandle:
    backend: TeamBackend
    value: str


class TeamBackendError(RuntimeError):
    pass


class TeamBackendDriver(Protocol):
    backend: TeamBackend
    def availability(self) -> BackendAvailability: ...
    async def start(self, member: TeamMember, command: tuple[str, ...]) -> BackendHandle: ...
    async def wake(self, member: TeamMember) -> None: ...
    async def stop(self, member: TeamMember) -> None: ...


class BackendSelector:
    def __init__(self, drivers: tuple[TeamBackendDriver, ...]) -> None:
        self._drivers = {item.backend: item for item in drivers}

    def inspect(self, priority: tuple[str, ...]) -> tuple[BackendAvailability, ...]:
        return tuple(self._drivers[TeamBackend(item)].availability() for item in priority if TeamBackend(item) in self._drivers)

    def select(self, priority: tuple[str, ...], requested: TeamBackend | None = None) -> TeamBackendDriver:
        candidates = (requested,) if requested is not None else tuple(TeamBackend(item) for item in priority)
        reports: list[BackendAvailability] = []
        for backend in candidates:
            driver = self._drivers.get(backend)
            report = driver.availability() if driver is not None else BackendAvailability(backend, False, "后端未注册。")
            reports.append(report)
            if report.available and driver is not None:
                return driver
        detail = "；".join(f"{item.backend.value}：{item.reason}" for item in reports)
        if requested is not None:
            raise TeamBackendError(f"请求的成员后端不可用：{detail}")
        raise TeamBackendError(f"没有可用的成员后端：{detail}")


class TmuxBackend:
    backend = TeamBackend.TMUX
    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._runner = runner

    def availability(self) -> BackendAvailability:
        if shutil.which("tmux") is None:
            return BackendAvailability(self.backend, False, "未找到 tmux 可执行程序。")
        return BackendAvailability(self.backend, True, "已检测到 tmux。")

    async def start(self, member: TeamMember, command: tuple[str, ...]) -> BackendHandle:
        label = f"yucode-{member.agent_id}"
        action = ("split-window", "-d") if os.environ.get("TMUX") else ("new-session", "-d", "-s", label)
        result = await asyncio.to_thread(self._runner, ("tmux", *action, "-P", "-F", "#{pane_id}", "-c", str(member.workspace_root), *command), capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise TeamBackendError(f"无法启动 tmux 成员：{result.stderr.strip() or '未知错误'}")
        return BackendHandle(self.backend, result.stdout.strip())

    async def wake(self, member: TeamMember) -> None:
        if not member.backend_handle:
            raise TeamBackendError("tmux 成员没有 pane 标识。")
        result = await asyncio.to_thread(self._runner, ("tmux", "send-keys", "-t", member.backend_handle, "C-m"), capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise TeamBackendError(f"无法唤醒 tmux 成员：{result.stderr.strip()}")

    async def stop(self, member: TeamMember) -> None:
        if not member.backend_handle:
            return
        await asyncio.to_thread(self._runner, ("tmux", "kill-pane", "-t", member.backend_handle), capture_output=True, text=True, check=False)


class Iterm2Backend:
    backend = TeamBackend.ITERM2
    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._runner = runner
    def availability(self) -> BackendAvailability:
        if platform.system() != "Darwin":
            return BackendAvailability(self.backend, False, "iTerm2 仅支持 macOS。")
        if shutil.which("osascript") is None:
            return BackendAvailability(self.backend, False, "未找到 osascript。")
        return BackendAvailability(self.backend, True, "已检测到 macOS 与 osascript。")

    async def start(self, member: TeamMember, command: tuple[str, ...]) -> BackendHandle:
        shell_command = f"cd {shlex.quote(str(member.workspace_root))} && {shlex.join(command)}"
        escaped = shell_command.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "iTerm2" to tell (create window with default profile command "{escaped}") to return id of current session'
        result = await asyncio.to_thread(self._runner, ("osascript", "-e", script), capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise TeamBackendError(f"无法启动 iTerm2 成员：{result.stderr.strip() or '未返回 session ID'}")
        return BackendHandle(self.backend, result.stdout.strip())

    async def wake(self, member: TeamMember) -> None:
        if not member.backend_handle:
            raise TeamBackendError("iTerm2 成员没有 session ID。")
        script = f'tell application "iTerm2" to tell session id "{member.backend_handle}" to write text ""'
        result = await asyncio.to_thread(self._runner, ("osascript", "-e", script), capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise TeamBackendError(f"无法唤醒 iTerm2 成员：{result.stderr.strip()}")

    async def stop(self, member: TeamMember) -> None:
        if not member.backend_handle:
            return
        script = f'tell application "iTerm2" to tell session id "{member.backend_handle}" to terminate'
        await asyncio.to_thread(self._runner, ("osascript", "-e", script), capture_output=True, text=True, check=False)


class InProcessBackend:
    backend = TeamBackend.IN_PROCESS
    def __init__(self, starter: Callable[[TeamMember], Awaitable[None]] | None = None) -> None:
        self._starter = starter; self._tasks: dict[str, asyncio.Task[None]] = {}; self._wakeups: dict[str, asyncio.Event] = {}

    def availability(self) -> BackendAvailability:
        return BackendAvailability(self.backend, True, "可在当前进程中运行。")

    async def start(self, member: TeamMember, command: tuple[str, ...]) -> BackendHandle:
        if self._starter is None:
            raise TeamBackendError("进程内成员运行器尚未配置。")
        task = asyncio.create_task(self._starter(member))
        self._tasks[member.agent_id] = task; self._wakeups.setdefault(member.agent_id, asyncio.Event())
        return BackendHandle(self.backend, member.agent_id)

    async def wake(self, member: TeamMember) -> None:
        self._wakeups.setdefault(member.agent_id, asyncio.Event()).set()

    async def stop(self, member: TeamMember) -> None:
        task = self._tasks.get(member.agent_id)
        if task is not None:
            task.cancel()

    def consume_wakeup(self, member: TeamMember) -> asyncio.Event:
        return self._wakeups.setdefault(member.agent_id, asyncio.Event())
