"""单个 MCP Server 的 JSON-RPC 生命周期。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from yucode.config import MCPServerConfig
from yucode.mcp.transport import MCPTransport, StdioTransport, StreamableHttpTransport

REQUEST_TIMEOUT_SECONDS = 30.0
PROTOCOL_VERSION = "2025-06-18"


class MCPProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]


class MCPClientSession:
    def __init__(self, config: MCPServerConfig, transport: MCPTransport | None = None) -> None:
        self.config = config
        self._transport = transport or (StdioTransport(config) if config.transport == "stdio" else StreamableHttpTransport(config))
        self._next_id = 1

    async def start(self) -> tuple[RemoteTool, ...]:
        await self._transport.open()
        initialized = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "YuCode", "version": "0.1.0"},
        })
        version = initialized.get("protocolVersion")
        capabilities = initialized.get("capabilities")
        if not isinstance(version, str) or not isinstance(capabilities, Mapping) or not isinstance(capabilities.get("tools"), Mapping):
            raise MCPProtocolError("Server 未协商 tools 能力或协议版本。")
        if isinstance(self._transport, StreamableHttpTransport):
            self._transport.protocol_version = version
        await self._transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[RemoteTool] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            page = await self._request("tools/list", params)
            raw_tools = page.get("tools")
            if not isinstance(raw_tools, list):
                raise MCPProtocolError("tools/list 响应缺少 tools 列表。")
            for raw in raw_tools:
                if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("description", ""), str) or not isinstance(raw.get("inputSchema", {}), Mapping):
                    raise MCPProtocolError("远端工具定义无效。")
                tools.append(RemoteTool(raw["name"], raw.get("description", ""), raw.get("inputSchema", {})))
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return tuple(tools)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": dict(arguments)})

    async def _request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id, self._next_id = self._next_id, self._next_id + 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}
        response = await self._transport.request(payload, REQUEST_TIMEOUT_SECONDS)
        if response.get("id") != request_id:
            raise MCPProtocolError("JSON-RPC 响应 id 不匹配。")
        error = response.get("error")
        if isinstance(error, Mapping):
            raise MCPProtocolError(str(error.get("message", "MCP 请求失败。")))
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise MCPProtocolError("JSON-RPC 响应缺少 result。")
        return result

    async def close(self) -> None:
        await self._transport.close()
