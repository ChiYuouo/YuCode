import asyncio

from yucode.config import MCPServerConfig
from yucode.mcp.manager import MCPManager
from yucode.mcp.session import RemoteTool
from yucode.tools.registry import ToolRegistry


def test_manager_registers_prefixed_tools_and_isolates_failure(tmp_path, monkeypatch) -> None:
    closed = []

    class FakeSession:
        def __init__(self, config):
            self.config = config

        async def start(self):
            if self.config.name == "bad":
                raise RuntimeError("无法连接")
            return (RemoteTool("echo", "回显", {"type": "object"}),)

        async def close(self):
            closed.append(self.config.name)

    monkeypatch.setattr("yucode.mcp.manager.MCPClientSession", FakeSession)

    async def scenario():
        manager = MCPManager((MCPServerConfig("good", "stdio", command="x"), MCPServerConfig("bad", "stdio", command="x")))
        registry = ToolRegistry(tmp_path)
        warnings = await manager.start(registry)
        await manager.close()
        return registry, warnings

    registry, warnings = asyncio.run(scenario())
    assert registry.get("MCP__good__echo") is not None
    assert warnings[0].server_name == "bad"
    assert set(closed) == {"good", "bad"}
