"""Two independently launched MCP servers: discovery and local direct access."""

from functools import wraps
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from .catalog import Catalog, FabricError

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
REMOTE_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def guarded(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FabricError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

    return wrapped


def discovery_server(
    catalog: Catalog, *, http=False, host="127.0.0.1", port=8000, public_host="fabric.crc.nd.edu"
) -> FastMCP:
    mcp = FastMCP(
        "llm-wiki-fabric",
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[f"https://{public_host}"],
        ),
        instructions="Discover resources, then identify one. Query it through the separate local connector tools; discovery never queries resource data.",
    )

    @mcp.tool(annotations=READ)
    @guarded
    def fabric_find(query: str = "", kind: str | None = None) -> dict[str, Any]:
        """Search configured resource summaries by keywords; empty query lists all. kind is mcp or postgresql. Does not contact resources."""
        return catalog.find(query, kind)

    @mcp.tool(annotations=READ)
    @guarded
    def fabric_identify(resource_id: str) -> dict[str, Any]:
        """Get connection metadata, semantic entrypoint and revision. Pass resource_id and revision to the local connector tools."""
        return catalog.public_identify(resource_id) if http else catalog.identify(resource_id)

    @mcp.tool(annotations=READ)
    def fabric_catalog() -> dict[str, Any]:
        """Get the complete public descriptor snapshot for connector startup: routing, dictionaries, permissions, freshness and revision. Contains no credentials."""
        return catalog.snapshot()

    if http:

        @mcp.custom_route("/healthz", methods=["GET"])
        async def health(request):
            return JSONResponse({"status": "ready", "revision": catalog.revision})

    return mcp


def connector_server(catalog: Catalog) -> FastMCP:
    from .connectors import DirectClients

    clients = DirectClients(catalog)
    mcp = FastMCP(
        "llm-wiki-direct",
        instructions="Local direct access to resources selected through fabric discovery. Get resource_id and revision from fabric_identify. Read ontology or schema before querying data.",
    )

    @mcp.tool(annotations=REMOTE_READ)
    async def resource_tools(resource_id: str, revision: str) -> dict[str, Any]:
        """Discover allowed remote MCP tools and input schemas directly from the selected resource, after fabric_identify."""
        try:
            return await clients.mcp_request(resource_id, revision)
        except FabricError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

    @mcp.tool(annotations=REMOTE_READ)
    async def resource_call(
        resource_id: str, revision: str, tool_name: str, arguments: dict
    ) -> dict[str, Any]:
        """Call an allowed read tool directly on the selected MCP resource. Discover its schema via resource_tools first. Use download_ontology for PAD semantics."""
        try:
            return await clients.mcp_request(resource_id, revision, tool_name, arguments)
        except FabricError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

    @mcp.tool(annotations=READ)
    @guarded
    def db_schema(resource_id: str, revision: str) -> dict[str, Any]:
        """Inspect allowed research columns and foreign keys directly in PostgreSQL. No data rows or application-account tables are returned."""
        return clients.schema(resource_id, revision)

    @mcp.tool(annotations=READ)
    @guarded
    def db_query(
        resource_id: str,
        revision: str,
        query: str,
        parameters: dict | None = None,
        max_rows: int = 100,
    ) -> dict[str, Any]:
        """Run a single read-only PostgreSQL SELECT on approved research tables. Named parameters use %(name)s. Inspect db_schema first. Default 100 rows; maximum 1000; 10-second statement timeout. Check truncated before claiming completeness."""
        return clients.query(resource_id, revision, query, parameters, max_rows)

    return mcp
