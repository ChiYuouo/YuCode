"""MCP 远端工具的 YuCode 适配层。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from yucode.cancellation import Cancellation
from yucode.mcp.session import MCPClientSession, RemoteTool
from yucode.tools.base import ToolContext, ToolDefinition, ToolResult, ToolSafety


class MCPTool:
    safety = ToolSafety.SIDE_EFFECT

    def __init__(self, server_name: str, remote: RemoteTool, session: MCPClientSession) -> None:
        self._remote, self._session = remote, session
        self._definition = ToolDefinition(f"MCP__{server_name}__{remote.name}", remote.description, remote.input_schema)

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, arguments: Mapping[str, Any], _context: ToolContext, call_id: str, cancellation: Cancellation) -> ToolResult:
        if cancellation.is_cancelled:
            return ToolResult(call_id, self.definition.name, False, "用户已取消。", error_code="cancelled")
        try:
            result = await self._session.call_tool(self._remote.name, arguments)
        except Exception as error:
            return ToolResult(call_id, self.definition.name, False, f"MCP 工具调用失败：{error}", error_code="mcp_error")
        content = result.get("content", [])
        text = "\n".join(item.get("text", json.dumps(item, ensure_ascii=False)) if isinstance(item, Mapping) else str(item) for item in content) if isinstance(content, list) else ""
        failed = result.get("isError") is True
        return ToolResult(call_id, self.definition.name, not failed, "远端工具执行成功。" if not failed else "远端工具返回错误。", text, "mcp_tool_error" if failed else None)
