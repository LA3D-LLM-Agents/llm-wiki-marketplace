"""Check configured MCP startup and discovery without querying source data."""

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


@asynccontextmanager
async def transport(server, directory):
    if server.get("type") == "http":
        async with streamablehttp_client(server["url"]) as (read, write, _):
            yield read, write
    else:
        server = dict(server)
        environment = {**os.environ, **server.pop("env", {})}
        params = StdioServerParameters(**server, cwd=directory, env=environment)
        async with stdio_client(params) as streams:
            yield streams


async def main():
    plugin = Path(__file__).resolve().parents[1]
    config = json.loads((plugin / ".mcp.json").read_text())
    expected = {
        "fabric": {"fabric_find", "fabric_identify", "fabric_catalog"},
        "direct": {"resource_tools", "resource_call", "db_schema", "db_query"},
    }
    with tempfile.TemporaryDirectory(prefix="fabric smoke ") as directory:
        for name, server in config["mcpServers"].items():
            async with (
                transport(server, directory) as (read, write),
                ClientSession(read, write) as client,
            ):
                await client.initialize()
                assert {t.name for t in (await client.list_tools()).tools} == expected[name]
                if name == "fabric":
                    result = await client.call_tool("fabric_find", {})
                    assert not result.isError
                    assert result.structuredContent.get("resources")
            print(f"{name}: MCP startup and tools passed")


if __name__ == "__main__":
    asyncio.run(main())
