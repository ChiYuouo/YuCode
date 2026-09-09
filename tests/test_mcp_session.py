import asyncio

from yucode.config import MCPServerConfig
from yucode.mcp.session import MCPClientSession


class FakeTransport:
    def __init__(self) -> None:
        self.requests = []

    async def open(self) -> None:
        pass

    async def notify(self, message) -> None:
        self.requests.append(message)

    async def request(self, message, _timeout):
        self.requests.append(message)
        method = message["method"]
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]}}
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": message["params"]["arguments"]["text"]}]}}

    async def close(self) -> None:
        pass


def test_session_initializes_lists_and_calls_tool() -> None:
    async def scenario():
        transport = FakeTransport()
        session = MCPClientSession(MCPServerConfig("demo", "stdio", command="fake"), transport)
        tools = await session.start()
        result = await session.call_tool("echo", {"text": "你好"})
        return transport.requests, tools, result
    requests, tools, result = asyncio.run(scenario())
    assert [request["method"] for request in requests] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert tools[0].name == "echo"
    assert result["content"][0]["text"] == "你好"
