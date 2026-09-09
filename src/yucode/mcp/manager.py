"""多 MCP Server 的启动、缓存与关闭。"""

from __future__ import annotations

from dataclasses import dataclass

from yucode.config import MCPConfigIssue, MCPServerConfig
from yucode.mcp.session import MCPClientSession
from yucode.mcp.tool import MCPTool
from yucode.tools.registry import ToolRegistry


@dataclass(frozen=True)
class MCPStartupWarning:
    server_name: str
    reason: str


class MCPManager:
    def __init__(self, servers: tuple[MCPServerConfig, ...] = (), issues: tuple[MCPConfigIssue, ...] = ()) -> None:
        self._servers, self._issues = servers, issues
        self._sessions: dict[str, MCPClientSession] = {}

    async def start(self, registry: ToolRegistry) -> tuple[MCPStartupWarning, ...]:
        warnings = [MCPStartupWarning(issue.server_name, issue.reason) for issue in self._issues]
        for config in self._servers:
            session = MCPClientSession(config)
            try:
                tools = await session.start()
                adapted = [MCPTool(config.name, tool, session) for tool in tools]
                registry.register_many(adapted)
                self._sessions[config.name] = session
            except Exception as error:
                await session.close()
                warnings.append(MCPStartupWarning(config.name, str(error)))
        return tuple(warnings)

    async def close(self) -> None:
        sessions, self._sessions = tuple(self._sessions.values()), {}
        for session in sessions:
            try:
                await session.close()
            except Exception:
                pass
