"""Import a public discovery snapshot while retaining operator routing and permissions."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import Field

from .catalog import Catalog, CatalogConfig, FabricError, Identifier, StrictModel


def fetch_snapshot(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise FabricError(
            "invalid_discovery_url", "Discovery snapshot requires a credential-free HTTPS URL"
        )

    async def retrieve():
        async with asyncio.timeout(20):
            async with streamablehttp_client(url, timeout=15) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("fabric_catalog", {})
                    if result.isError or not isinstance(result.structuredContent, dict):
                        raise ValueError("Invalid catalog tool result")
                    snapshot = result.structuredContent
                    if len(json.dumps(snapshot).encode()) > 1_000_000:
                        raise ValueError("Snapshot too large")
                    return snapshot

    try:
        return asyncio.run(retrieve())
    except Exception:
        raise FabricError(
            "discovery_unavailable", "Cannot load catalog through the approved fabric MCP server"
        ) from None


def merge_snapshot(local, snapshot):
    public = snapshot["catalog"]
    revision = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    if revision != snapshot["revision"]:
        raise ValueError("Invalid snapshot hash")
    resources = []
    for record in public["resources"]:
        approved = local.resources.get(record["id"])
        if approved is None:
            continue
        record = dict(record)
        connection = record["connection"]
        if connection["kind"] != approved.connection.kind:
            raise ValueError("Resource kind changed")
        if connection["kind"] == "mcp":
            if (
                connection["url"] != approved.connection.url
                or connection["transport"] != approved.connection.transport
            ):
                raise ValueError("Endpoint change needs local approval")
        elif connection != {"kind": "postgresql", "requires_local_profile": True}:
            raise ValueError("Remote SQL routing is prohibited")
        record["connection"] = approved.connection.model_dump()
        for field in ["allowed_tools", "allowed_tables"]:
            record[field] = sorted(set(record[field]) & set(getattr(approved, field)))
        resources.append(record)
    local.config = CatalogConfig.model_validate(
        {"schema_version": public["schema_version"], "resources": resources}
    )
    local.descriptor_status = snapshot.get("descriptors", {})
    local._build()
    # Public revision is the contract; local authorization may be narrower.
    local.revision = revision
    return local


def remote_catalog(local, url):
    try:
        return merge_snapshot(local, fetch_snapshot(url))
    except (ValueError, KeyError, TypeError):
        raise FabricError(
            "discovery_unavailable", "Cannot load a valid approved discovery snapshot"
        ) from None


class ClientPolicy(StrictModel):
    schema_version: Literal[1]
    trusted_catalogs: list[str] = Field(min_length=1)
    allowed_mcp_hosts: list[str]
    allowed_ssh_hosts: list[str]
    allowed_tools: list[Identifier]
    allowed_tables: list[Identifier]


def policy_catalog(path, url):
    try:
        policy = ClientPolicy.model_validate(yaml.safe_load(Path(path).read_text()))
        if url not in policy.trusted_catalogs:
            raise ValueError("Catalog is not approved")
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        raise FabricError(
            "invalid_policy", "Configure a valid policy approving the catalog URL"
        ) from None
    try:
        return catalog_from_policy(policy, fetch_snapshot(url))
    except (ValueError, KeyError, TypeError):
        raise FabricError(
            "invalid_snapshot", "Published resource access is invalid or exceeds local policy"
        ) from None


def catalog_from_policy(policy, snapshot):
    public = snapshot["catalog"]
    revision = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    if revision != snapshot["revision"]:
        raise ValueError("Invalid snapshot hash")
    resources = []
    for entry in public["resources"]:
        record = dict(entry)
        connection = dict(record["connection"])
        if connection["kind"] == "mcp":
            if urlsplit(connection["url"]).hostname not in policy.allowed_mcp_hosts:
                raise ValueError("MCP host not approved")
            connection["auth_profile"] = "anonymous"
        elif connection["kind"] == "postgresql":
            access = connection.get("access", {})
            if (
                access.get("kind") != "ssh"
                or access.get("host") not in policy.allowed_ssh_hosts
                or connection.get("host") not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("SQL transport not approved")
            if connection.pop("requires_credentials", None) is not True:
                raise ValueError("Credentials requirement missing")
            connection["auth_profile"] = record["id"]
        else:
            raise ValueError("Unsupported connection kind")
        record["connection"] = connection
        for field in ["allowed_tools", "allowed_tables"]:
            record[field] = sorted(set(record[field]) & set(getattr(policy, field)))
        resources.append(record)
    catalog = Catalog.__new__(Catalog)
    catalog.config = CatalogConfig.model_validate(
        {"schema_version": public["schema_version"], "resources": resources}
    )
    catalog.descriptor_status = snapshot.get("descriptors", {})
    catalog._build()
    catalog.revision = revision
    return catalog
