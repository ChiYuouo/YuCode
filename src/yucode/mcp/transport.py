"""MCP 的 stdio 与 Streamable HTTP 传输。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from typing import Any, Protocol

import httpx

from yucode.config import MCPServerConfig


class MCPTransportError(RuntimeError):
    """传输或协议载荷不可继续使用。"""


class MCPTransport(Protocol):
    async def open(self) -> None: ...
    async def request(self, message: Mapping[str, Any], timeout: float) -> Mapping[str, Any]: ...
    async def notify(self, message: Mapping[str, Any]) -> None: ...
    async def close(self) -> None: ...


class StdioTransport:
    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None

    async def open(self) -> None:
        environment = dict(__import__("os").environ)
        environment.update(self._config.env)
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._config.command, *self._config.args, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment,
            )
        except OSError as error:
            raise MCPTransportError(f"无法启动 stdio Server：{error}") from error
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr = asyncio.create_task(self._drain_stderr())

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        while line := await self._process.stdout.readline():
            try:
                payload = json.loads(line)
                request_id = payload.get("id")
                pending = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
                if pending and not pending.done():
                    pending.set_result(payload)
            except (json.JSONDecodeError, AttributeError):
                continue
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPTransportError("stdio Server 已关闭。"))
        self._pending.clear()

    async def _drain_stderr(self) -> None:
        assert self._process and self._process.stderr
        while await self._process.stderr.readline():
            pass

    async def _write(self, message: Mapping[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPTransportError("stdio Server 尚未启动。")
        self._process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        await self._process.stdin.drain()

    async def request(self, message: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            raise MCPTransportError("请求缺少整数 id。")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(message)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            raise MCPTransportError("MCP 请求超时。") from error
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, message: Mapping[str, Any]) -> None:
        await self._write(message)

    async def close(self) -> None:
        process, self._process = self._process, None
        if not process:
            return
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader, self._stderr):
            if task:
                task.cancel()


class StreamableHttpTransport:
    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self.protocol_version: str | None = None

    async def open(self) -> None:
        self._client = httpx.AsyncClient(headers=dict(self._config.headers))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    async def request(self, message: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        if not self._client or not self._config.url:
            raise MCPTransportError("HTTP Server 尚未启动。")
        try:
            response = await self._client.post(self._config.url, json=message, headers=self._headers(), timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise MCPTransportError(f"HTTP MCP 请求失败：{error}") from error
        self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            data = "\n".join(line[5:].strip() for line in response.text.splitlines() if line.startswith("data:"))
            if not data:
                raise MCPTransportError("HTTP SSE 响应不含消息。")
            return json.loads(data)
        return response.json()

    async def notify(self, message: Mapping[str, Any]) -> None:
        if not self._client or not self._config.url:
            raise MCPTransportError("HTTP Server 尚未启动。")
        await self._client.post(self._config.url, json=message, headers=self._headers(), timeout=30)

    async def close(self) -> None:
        if self._client and self._config.url and self._session_id:
            try:
                await self._client.delete(self._config.url, headers=self._headers(), timeout=2)
            except httpx.HTTPError:
                pass
        if self._client:
            await self._client.aclose()
        self._client = None
