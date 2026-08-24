import asyncio
import json

import pytest

MINI_SERVER = """\
import json, sys
def read_message():
    line = sys.stdin.buffer.readline()
    return json.loads(line) if line else None
def write_message(message):
    sys.stdout.buffer.write(json.dumps(message).encode() + b"\\n")
    sys.stdout.buffer.flush()
def main():
    while True:
        message = read_message()
        if message is None:
            break
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            write_message({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mini-server", "version": "1.0.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            write_message({"jsonrpc": "2.0", "id": request_id, "result": {
                "tools": [{
                    "name": "echo_text",
                    "description": "echo input text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }],
            }})
        elif method == "tools/call":
            arguments = message.get("params", {}).get("arguments", {})
            write_message({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": f"echo:{arguments.get('text', '')}"}],
            }})
        else:
            write_message({"jsonrpc": "2.0", "id": request_id, "error": {
                "code": -32601, "message": f"unknown method: {method}",
            }})
if __name__ == "__main__":
    main()
"""


@pytest.fixture
def mini_server(tmp_path):
    server = tmp_path / "mini_server.py"
    server.write_text(MINI_SERVER, encoding="utf-8")
    return str(server)


@pytest.fixture
async def connected_client(mini_server):
    from agent.mcp_client import get_mcp_client

    client = await get_mcp_client()
    await client.connect_server("mini", {
        "type": "stdio",
        "command": "python",
        "args": [mini_server],
    })
    try:
        yield client
    finally:
        await client.disconnect()


def test_mcp_client_connects_and_calls_tool(mini_server):
    async def scenario():
        from agent.mcp_client import get_mcp_client

        client = await get_mcp_client()
        await client.connect_server("mini", {
            "type": "stdio",
            "command": "python",
            "args": [mini_server],
        })
        try:
            tools = client.get_all_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo_text"
            assert tools[0]["input_schema"]["required"] == ["text"]
            result = await client.call_tool("echo_text", {"text": "hello"})
            assert result["success"] is True
            assert result["content"] == "echo:hello"
        finally:
            await client.disconnect()

    asyncio.run(scenario())


def test_mcp_tools_registered_in_agent_registry(mini_server):
    async def scenario():
        from agent.mcp_client import get_mcp_client
        from agent.runtime.tooling import LocalAgentServices, build_tool_registry
        import tempfile

        client = await get_mcp_client()
        await client.connect_server("mini", {
            "type": "stdio",
            "command": "python",
            "args": [mini_server],
        })
        try:
            services = LocalAgentServices.create()
            services.workspaces.bind("sess", tempfile.mkdtemp())
            registry = build_tool_registry("sess", services)

            schemas = [item for item in registry.schemas() if item["name"] == "echo_text"]
            assert len(schemas) == 1
            assert "[MCP:mini]" in schemas[0]["description"]
            assert schemas[0]["input_schema"]["required"] == ["text"]

            # EXTERNAL 风险：未确认时要求确认（confirm 档），确认后执行成功
            unconfirmed = await registry.execute_async("echo_text", {"text": "x"})
            assert unconfirmed.requires_confirmation is True
            confirmed = await registry.execute_async("echo_text", {"text": "agent"}, confirmed=True)
            assert confirmed.success is True
            assert confirmed.output == "echo:agent"
        finally:
            await client.disconnect()

    asyncio.run(scenario())
