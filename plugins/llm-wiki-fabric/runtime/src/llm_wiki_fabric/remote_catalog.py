"""Import a public discovery snapshot while retaining operator routing and permissions."""

import hashlib
import json
from urllib.parse import urlsplit

import httpx

from .catalog import CatalogConfig, FabricError


def remote_catalog(local, url):
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
        return merge_snapshot(local, json.loads(body))
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
