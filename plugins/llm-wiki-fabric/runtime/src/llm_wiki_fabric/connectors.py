"""Local direct clients. This module is never imported by the discovery server."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import sqlglot
from httpx import HTTPStatusError, TimeoutException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlglot import exp

from .catalog import Catalog, FabricError, MCPConnection, Resource, SQLConnection

MAX_ROWS = 1000
MAX_BYTES = 256_000
TIMEOUT_MS = 10_000
SAFE_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "LENGTH",
    "CHAR_LENGTH",
    "LEFT",
    "RIGHT",
    "SUBSTRING",
    "TRIM",
    "ROUND",
    "ABS",
    "CAST",
    "TRY_CAST",
    "EXTRACT",
    "DATE_TRUNC",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "ARRAY_AGG",
    "STRING_AGG",
    "GROUP_CONCAT",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "GREATEST",
    "LEAST",
    "ARRAY_SIZE",
    "JSON_EXTRACT",
    "JSON_EXTRACT_SCALAR",
    "CASE",
    "IF",
    "ARRAY",
    "CONCAT",
    "CONCAT_WS",
}


def remote_failure(error: Exception) -> FabricError:
    """Unwrap task-group errors without reflecting private HTTP diagnostics."""
    leaves = []

    def collect(exc):
        if isinstance(exc, BaseExceptionGroup):
            for child in exc.exceptions:
                collect(child)
        else:
            leaves.append(exc)

    collect(error)
    for exc in leaves:
        if isinstance(exc, FabricError):
            return exc
        if isinstance(exc, (TimeoutError, TimeoutException)):
            return FabricError("timeout", "Remote MCP request timed out")
        if isinstance(exc, HTTPStatusError) and exc.response.status_code in (401, 403):
            return FabricError(
                "authentication_failed",
                "Remote MCP access was denied; check the authentication profile",
            )
    return FabricError(
        "connection_failed",
        "Cannot complete the remote MCP session; check endpoint and availability",
    )


def credentials(profile: str, path: Path | None = None) -> dict:
    if profile == "anonymous":
        return {}
    path = path or Path(
        os.environ.get(
            "FABRIC_CREDENTIALS", str(Path.home() / ".config/llm-wiki-fabric/credentials.json")
        )
    )
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise FabricError(
                "credentials_permissions",
                "Credential file must be readable only by its owner (chmod 600)",
            )
        value = json.loads(path.read_text())[profile]
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError):
        raise FabricError(
            "missing_credentials", "Configure the resource's local authentication profile"
        ) from None


def normalize_query(query: str, allowed_tables: list[str]) -> str:
    """Defense in depth on top of a restricted login and read-only transaction.

    Execute the parsed/serialized tree, never the unchecked original string.
    Deliberately limit SQL to a documented read-only research subset.
    """
    if len(query) > 20_000:
        raise FabricError("invalid_query", "SQL exceeds the 20000-character limit")
    try:
        statements = sqlglot.parse(query, read="postgres")
    except sqlglot.errors.SqlglotError:
        raise FabricError("invalid_query", "Cannot parse the query as PostgreSQL SELECT") from None
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise FabricError("read_only", "Only a single SELECT query is permitted")
    tree = statements[0]
    forbidden = (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock)
    if any(isinstance(n, forbidden) for n in tree.walk()):
        raise FabricError("read_only", "Mutations and locking clauses are not permitted")
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if (
            not isinstance(table.this, exp.Identifier)
            or table.catalog
            or table.db not in ("", "public")
            or (table.name not in allowed_tables and not (table.name in ctes and not table.db))
        ):
            raise FabricError(
                "table_not_allowed", "Query references a table outside the research allowlist"
            )
    for function in tree.find_all(exp.Func):
        if function.sql_name() not in SAFE_FUNCTIONS:
            raise FabricError(
                "function_not_allowed",
                "Query uses a function outside the supported research SQL subset",
            )
    # Qualified function calls could shadow an approved built-in.
    if any(isinstance(n, exp.Dot) and isinstance(n.expression, exp.Func) for n in tree.walk()):
        raise FabricError("function_not_allowed", "Qualified function calls are not permitted")
    return tree.sql(dialect="postgres")


class DirectClients:
    def __init__(self, catalog: Catalog, credential_path: Path | None = None):
        self.catalog = catalog
        self.credential_path = credential_path

    def resource(self, resource_id: str, revision: str, kind: str) -> Resource:
        r = self.catalog.get(resource_id, revision)
        if r.connection.kind != kind:
            raise FabricError("wrong_resource_kind", f"This operation requires a {kind} resource")
        return r

    def provenance(self, resource_id: str) -> dict:
        return {
            "resource_id": resource_id,
            "revision": self.catalog.revision,
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "route": "direct",
        }

    async def mcp_request(
        self,
        resource_id: str,
        revision: str,
        tool_name: str | None = None,
        arguments: dict | None = None,
    ) -> dict:
        r = self.resource(resource_id, revision, "mcp")
        c = r.connection
        assert isinstance(c, MCPConnection)
        if tool_name is not None and tool_name not in r.allowed_tools:
            raise FabricError(
                "tool_not_allowed", "Tool is not in this resource's read-only allowlist"
            )
        auth = credentials(c.auth_profile, self.credential_path)
        headers = {"Authorization": f"Bearer {auth['token']}"} if auth.get("token") else None
        try:
            async with asyncio.timeout(45):
                async with streamablehttp_client(
                    c.url, headers=headers, timeout=15, sse_read_timeout=30
                ) as (read, write, _):
                    async with ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=30)
                    ) as session:
                        await session.initialize()
                        if tool_name is None:
                            # MCP listings may paginate, independently of PAD data pagination.
                            available, cursor, seen = [], None, set()
                            for _ in range(20):
                                page = await session.list_tools(cursor=cursor)
                                available.extend(
                                    t.model_dump(mode="json", exclude_none=True)
                                    for t in page.tools
                                    if t.name in r.allowed_tools
                                )
                                cursor = page.nextCursor
                                if cursor is None:
                                    break
                                if cursor in seen:
                                    raise FabricError(
                                        "remote_error", "Remote tool listing repeated a cursor"
                                    )
                                seen.add(cursor)
                            else:
                                raise FabricError(
                                    "remote_error", "Remote tool listing exceeded the page limit"
                                )
                            return {**self.provenance(resource_id), "tools": available}
                        result = await session.call_tool(tool_name, arguments or {})
                        if result.isError:
                            raise FabricError(
                                "remote_tool_error", "The selected resource reported a tool error"
                            )
                        values = []
                        for content in result.content:
                            if content.type == "text":
                                try:
                                    values.append(json.loads(content.text))
                                except ValueError:
                                    values.append(content.text)
                        value = values[0] if len(values) == 1 else values
                        if isinstance(value, dict) and value.get("success") is False:
                            raise FabricError(
                                "remote_tool_error",
                                "The selected resource could not complete the read",
                            )
                        if len(json.dumps(value).encode()) > MAX_BYTES:
                            raise FabricError(
                                "response_too_large",
                                "Use narrower parameters or pagination; response exceeds 256 KB",
                            )
                        return {**self.provenance(resource_id), "tool": tool_name, "result": value}
        except FabricError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            raise FabricError("timeout", "Remote MCP request timed out") from None
        except Exception as exc:
            # Do not reflect HTTP headers, auth material, or remote exception bodies.
            raise remote_failure(exc) from None

    def connect(self, resource: Resource):
        c = resource.connection
        assert isinstance(c, SQLConnection)
        auth = credentials(c.auth_profile, self.credential_path)
        if not auth.get("user") or not auth.get("password"):
            raise FabricError("missing_credentials", "Database profile needs user and password")
        return psycopg.connect(
            host=c.host,
            port=c.port,
            dbname=c.database,
            user=auth["user"],
            password=auth["password"],
            connect_timeout=5,
            application_name="llm-wiki-fabric-direct",
            options=f"-c default_transaction_read_only=on -c statement_timeout={TIMEOUT_MS} -c search_path=pg_catalog,public",
        )

    @staticmethod
    def sql_error(exc: psycopg.Error):
        code = exc.sqlstate or ""
        category = "query_failed"
        if code.startswith("28") or "password authentication failed" in str(exc).lower():
            category = "authentication_failed"
        elif code == "57014":
            category = "timeout"
        elif code in ("42501", "25006"):
            category = "permission_denied"
        elif isinstance(exc, psycopg.OperationalError):
            category = "connection_failed"
        raise FabricError(
            category,
            f"Database operation failed ({code or 'connection'}); check configured access and query",
        ) from None

    def schema(self, resource_id: str, revision: str) -> dict:
        r = self.resource(resource_id, revision, "postgresql")
        try:
            with self.connect(r) as conn:
                columns = conn.execute(
                    """
                    SELECT table_name, column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name = ANY(%s)
                    ORDER BY table_name, ordinal_position
                """,
                    (r.allowed_tables,),
                ).fetchall()
                relationships = conn.execute(
                    """
                    SELECT a.relname, b.relname, pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class a ON a.oid=c.conrelid
                    JOIN pg_class b ON b.oid=c.confrelid
                    WHERE c.contype='f' AND c.connamespace='public'::regnamespace
                      AND a.relname = ANY(%s) AND b.relname = ANY(%s)
                    ORDER BY a.relname,b.relname
                """,
                    (r.allowed_tables, r.allowed_tables),
                ).fetchall()
            return {
                **self.provenance(resource_id),
                "schema": "public",
                "data_dictionary": r.data_dictionary,
                "columns": [
                    dict(zip(["table", "column", "type", "nullable"], row)) for row in columns
                ],
                "foreign_keys": [
                    dict(zip(["table", "references", "definition"], row)) for row in relationships
                ],
                "notes": [
                    "Publication-source and publication-disease memberships are many-to-many; count distinct publication IDs.",
                    "corpus_excluded is a stored corpus flag; needs_review is a separate review flag.",
                    "Clinical trial status is a stored snapshot; report last_synced_at.",
                ],
            }
        except psycopg.Error as exc:
            self.sql_error(exc)

    def query(
        self,
        resource_id: str,
        revision: str,
        query: str,
        parameters: dict | None = None,
        max_rows: int = 100,
    ) -> dict:
        r = self.resource(resource_id, revision, "postgresql")
        if not 1 <= max_rows <= MAX_ROWS:
            raise FabricError("invalid_limit", "max_rows must be between 1 and 1000")
        sql = normalize_query(query, r.allowed_tables)
        try:
            with self.connect(r) as conn:
                with conn.cursor(name="fabric_result") as cur:
                    cur.execute(sql, parameters or {})
                    rows = cur.fetchmany(max_rows + 1)
                    columns = [column.name for column in cur.description]
                    truncated = len(rows) > max_rows
                    output, size = [], 0
                    for row in rows[:max_rows]:
                        row = json.loads(json.dumps(list(row), default=str))
                        size += len(json.dumps(row).encode())
                        if size > MAX_BYTES:
                            truncated = True
                            break
                        output.append(row)
            return {
                **self.provenance(resource_id),
                "columns": columns,
                "rows": output,
                "returned": len(output),
                "truncated": truncated,
                "limits": {
                    "max_rows": max_rows,
                    "max_bytes": MAX_BYTES,
                    "statement_timeout_ms": TIMEOUT_MS,
                },
            }
        except psycopg.Error as exc:
            self.sql_error(exc)
