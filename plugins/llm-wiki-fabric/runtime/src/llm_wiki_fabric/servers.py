"""Two independently launched MCP servers: discovery and local direct access."""

import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import FileResponse, JSONResponse

from .agents import AgentRegistry
from .catalog import Catalog, FabricError

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
TRACKED = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
ANNOUNCE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
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
    catalog: Catalog,
    *,
    http=False,
    host="127.0.0.1",
    port=8000,
    public_host="fabric.crc.nd.edu",
    agents: AgentRegistry | None = None,
) -> FastMCP:
    agents = agents or AgentRegistry()

    def observe(result, session_id, resource_ids=()):
        if session_id:
            try:
                result["announcement"] = agents.observe(session_id, resource_ids)
            except (FabricError, sqlite3.Error):
                result["announcement"] = {
                    "state": "unavailable",
                    "message": "Discovery succeeded; activity was not recorded.",
                }
        return result

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
        instructions="If your project has an enrolled wiki card, call fabric_announce once and retain its session_id for fabric_find/fabric_identify. Announcements are optional; failure must not block discovery. Discover resources, then identify one. Query it through the separate local connector tools; discovery never queries resource data.",
    )

    @mcp.tool(annotations=TRACKED)
    @guarded
    def fabric_find(
        query: str = "", kind: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        """Search configured resource summaries by keywords; empty query lists all. kind is mcp or postgresql. Optional session_id records returned resource IDs and last seen; does not contact resources."""
        result = catalog.find(query, kind)
        return observe(result, session_id, [r["resource_id"] for r in result["resources"]])

    @mcp.tool(annotations=TRACKED)
    @guarded
    def fabric_identify(resource_id: str, session_id: str | None = None) -> dict[str, Any]:
        """Get connection metadata, semantic entrypoint and revision. Pass resource_id and revision to the local connector tools. Optional session_id records discovery and last seen."""
        result = catalog.public_identify(resource_id) if http else catalog.identify(resource_id)
        return observe(result, session_id, [resource_id])

    @mcp.tool(annotations=READ)
    def fabric_catalog() -> dict[str, Any]:
        """Get the complete public descriptor snapshot for connector startup: routing, dictionaries, permissions, freshness and revision. Contains no credentials."""
        return catalog.snapshot()

    @mcp.tool(annotations=READ)
    def fabric_graph() -> dict[str, Any]:
        """Get resource/capability and announced-agent nodes, with observed discovery edges. Includes public activity; no session tokens or direct-query telemetry."""
        return agents.graph_view(catalog.graph_view())

    @mcp.tool(annotations=ANNOUNCE)
    @guarded
    def fabric_announce(
        agent_id: str, card_url: str, session_id: str | None = None, client: str | None = None
    ) -> dict[str, Any]:
        """Announce your enrolled project wiki card. Returns a private session_id to reuse on fabric_find/fabric_identify. Optional session_id renews your existing announcement. Uses federation-index metadata; listing does not authenticate the caller. If unavailable, continue discovery without a session."""
        return agents.announce(agent_id, card_url, session_id, client)

    @mcp.tool(annotations=READ)
    def fabric_agents() -> dict[str, Any]:
        """List announced project agents and recent sessions (15-minute window, seven-day retention). Last seen is observed discovery/announcement time, not proof of a running agent. No private session tokens or query text."""
        return agents.snapshot()

    if http:
        dashboard = Path(__file__).parent / "dashboard"
        headers = {
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-cache",
        }

        @mcp.custom_route("/", methods=["GET"])
        async def dashboard_page(request):
            return FileResponse(dashboard / "index.html", headers=headers)

        @mcp.custom_route("/dashboard/{asset:path}", methods=["GET"])
        async def dashboard_asset(request):
            asset = request.path_params["asset"]
            if asset not in {"app.js", "style.css", "vendor/cytoscape.min.js"}:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return FileResponse(dashboard / asset, headers=headers)

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
