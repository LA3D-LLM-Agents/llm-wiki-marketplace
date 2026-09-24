"""Publisher metadata adapters with locally controlled access and validated caching."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .catalog import FabricError, Resource

MAX_BYTES = 1_000_000
TTL = 3600


def https_url(url, origin=None):
    if not isinstance(url, str):
        raise ValueError("Descriptor URL must be a string")
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or (origin and p.netloc != urlsplit(origin).netloc)
    ):
        raise ValueError("Invalid descriptor endpoint")
    return url


def fetch(url):
    https_url(url)
    with httpx.stream("GET", url, timeout=10, follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("Descriptor too large")
    return json.loads(body)


def local_json(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Descriptor too large")
    return json.loads(path.read_text())


def normalize(seed, bundle, profiles):
    if seed["descriptor_format"] == "did-web":
        did, card = bundle["did"], bundle["card"]
        origin = seed["descriptor"]
        if did["id"] != "did:web:" + urlsplit(origin).netloc.replace(":", "%3A"):
            raise ValueError("DID does not match publisher host")

        def service(kind):
            matches = [s["serviceEndpoint"] for s in did["service"] if s["type"] == kind]
            if len(matches) != 1:
                raise ValueError("Missing or ambiguous service")
            return https_url(matches[0], origin)

        service("AgentCard")
        metadata = dict(
            label=card["name"],
            description=card["description"],
            capabilities=sorted({tag for s in card["skills"] for tag in s["tags"]}),
            semantic_entrypoint=seed["semantic_entrypoint"],
            publisher_id=did["id"],
            ontology_url=service("PublishedOntology"),
            connection=dict(
                kind="mcp",
                url=service("MCPEndpoint"),
                transport="streamable-http",
                auth_profile="anonymous",
            ),
        )
    elif seed["descriptor_format"] == "fabric":
        metadata = dict(bundle["descriptor"])
        if metadata.pop("schema_version") != 1 or metadata.pop("kind") != "postgresql":
            raise ValueError("Unsupported descriptor")
        metadata.pop("data_dictionary", None)
        metadata["data_dictionary"] = bundle.get("dictionary", {})
        metadata["connection"] = profiles[seed["connection_profile"]]
        if metadata["connection"]["kind"] != "postgresql":
            raise ValueError("Profile kind mismatch")
        # Reject publisher-supplied authorization or identity overrides.
        if set(metadata) - {
            "label",
            "description",
            "capabilities",
            "semantic_entrypoint",
            "data_dictionary",
            "connection",
        }:
            raise ValueError("Unexpected descriptor fields")
    else:
        raise ValueError("Unknown descriptor format")
    return Resource.model_validate(
        dict(
            metadata,
            id=seed["id"],
            allowed_tools=seed.get("allowed_tools", []),
            allowed_tables=seed.get("allowed_tables", []),
        )
    ).model_dump()


def load_seed(seed, base, profiles, cache_dir, refresh, offline):
    if set(seed) - {
        "id",
        "descriptor",
        "descriptor_format",
        "connection_profile",
        "semantic_entrypoint",
        "allowed_tools",
        "allowed_tables",
    }:
        raise ValueError("Unknown seed fields")
    source = seed["descriptor"]
    if not source.startswith("https://"):
        if seed["descriptor_format"] != "fabric":
            raise ValueError("DID descriptors require HTTPS")
        path = (base / source).resolve()
        path.relative_to(base.resolve())
        descriptor = local_json(path)
        bundle = {"descriptor": descriptor}
        if "data_dictionary" in descriptor:
            dictionary_path = (path.parent / descriptor["data_dictionary"]).resolve()
            dictionary_path.relative_to(base.resolve())
            bundle["dictionary"] = local_json(dictionary_path)
        return normalize(seed, bundle, profiles), {"state": "local", "source": source}
    https_url(source)
    if seed["descriptor_format"] != "did-web":
        raise ValueError("Remote format not supported")
    key = hashlib.sha256(source.encode()).hexdigest()
    cache = cache_dir / (key + ".json")
    previous = None
    try:
        previous = local_json(cache)
        normalize(seed, previous["bundle"], profiles)
        if not isinstance(previous["fetched_at"], (int, float)):
            raise ValueError("Invalid timestamp")
    except (OSError, ValueError, KeyError, TypeError):
        previous = None

    def cached(state):
        return normalize(seed, previous["bundle"], profiles), {
            "state": state,
            "source": source,
            "fetched_at": previous["fetched_at"],
            "age_seconds": max(0, int(time.time() - previous["fetched_at"])),
        }

    if previous and (offline or (not refresh and time.time() - previous["fetched_at"] < TTL)):
        return cached("offline-cache" if offline else "cached")
    if offline:
        raise FabricError(
            "descriptor_unavailable", "Offline mode requires a validated cached descriptor"
        )
    try:
        did = fetch(source)
        cards = [s["serviceEndpoint"] for s in did["service"] if s["type"] == "AgentCard"]
        if len(cards) != 1:
            raise ValueError("Missing or ambiguous agent card")
        bundle = {"did": did, "card": fetch(https_url(cards[0], source))}
        normalized = normalize(seed, bundle, profiles)
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        if previous:
            result, status = cached("stale")
            status["refresh_error"] = "Publisher refresh failed validation or retrieval"
            return result, status
        raise FabricError(
            "descriptor_unavailable", "No valid publisher descriptor or cache available"
        ) from None
    now = time.time()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cache_dir, prefix=".descriptor-")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump({"fetched_at": now, "bundle": bundle}, out)
        os.replace(name, cache)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return normalized, {"state": "fresh", "source": source, "fetched_at": now, "age_seconds": 0}


def expand_catalog(raw, path, *, cache_dir=None, refresh=False, offline=False):
    statuses = {}
    cache_dir = Path(
        cache_dir
        or os.environ.get("FABRIC_DESCRIPTOR_CACHE", "~/.cache/llm-wiki-fabric/descriptors")
    ).expanduser()
    try:
        raw = dict(raw)
        profiles = raw.pop("connection_profiles", {})
        resources = []
        for item in raw["resources"]:
            if "descriptor" in item:
                resource, status = load_seed(
                    item, path.parent, profiles, cache_dir, refresh, offline
                )
                resources.append(resource)
                statuses[item["id"]] = status
            else:
                resources.append(item)
        raw["resources"] = resources
        return raw, statuses
    except (ValueError, TypeError, KeyError, OSError):
        raise FabricError(
            "invalid_catalog", "Invalid descriptor, profile, or seed configuration"
        ) from None
