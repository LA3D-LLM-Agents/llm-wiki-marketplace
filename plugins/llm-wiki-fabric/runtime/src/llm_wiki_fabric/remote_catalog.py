"""Import a public discovery snapshot while retaining operator routing and permissions."""

import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import yaml
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
    try:
        with httpx.stream("GET", url, timeout=15, follow_redirects=False) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise ValueError("Snapshot too large")
        return json.loads(body)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise FabricError(
            "discovery_unavailable", "Cannot load a valid approved discovery snapshot"
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
