import asyncio
import sys

from yucode.config import MCPServerConfig
from yucode.mcp.transport import StdioTransport


def test_stdio_transport_routes_json_rpc_response_by_id() -> None:
    script = (
        "import sys,json; "
        "line=sys.stdin.readline(); msg=json.loads(line); "
        "print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'ok':True}}), flush=True)"
    )

    async def scenario():
        transport = StdioTransport(MCPServerConfig("test", "stdio", command=sys.executable, args=("-c", script)))
        await transport.open()
        try:
            return await transport.request({"jsonrpc": "2.0", "id": 7, "method": "ping"}, 1)
        finally:
            await transport.close()

    assert asyncio.run(scenario())["result"] == {"ok": True}
