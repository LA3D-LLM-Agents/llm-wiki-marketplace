"""Validated, secret-free resource catalog and derived RDF discovery graph."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rdflib import DCAT, RDF, RDFS, Graph, Namespace, URIRef
from rdflib import Literal as Term

ECO = Namespace("https://la3d-llm-agents.github.io/ns/eco#")
FAB = Namespace("https://github.com/chrissweet/llm-wiki-fabric/ns#")
BASE = "urn:llm-wiki-fabric:resource:"
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", max_length=100)]


class FabricError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MCPConnection(StrictModel):
    kind: Literal["mcp"]
    url: str
    transport: Literal["streamable-http"]
    auth_profile: Identifier = "anonymous"

    @model_validator(mode="after")
    def safe_endpoint(self):
        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("MCP endpoint must be an HTTPS URL without credentials")
        if url.query or url.fragment:
            raise ValueError("MCP endpoint cannot contain query parameters or fragments")
        return self


class SSHAccess(StrictModel):
    kind: Literal["ssh"]
    host: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]*$")]
    port: int = Field(default=22, ge=1, le=65535)


class SQLConnection(StrictModel):
    kind: Literal["postgresql"]
    host: str = "localhost"
    host_env: Identifier | None = None
    port: int = Field(default=5432, ge=1, le=65535)
    database: Identifier
    auth_profile: Identifier
    access: SSHAccess | None = None


class Resource(StrictModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    capabilities: list[Identifier] = Field(min_length=1)
    semantic_entrypoint: Identifier
    connection: Annotated[MCPConnection | SQLConnection, Field(discriminator="kind")]
    allowed_tools: list[Identifier] = Field(default_factory=list)
    allowed_tables: list[Identifier] = Field(default_factory=list)

    publisher_id: str | None = None
    ontology_url: str | None = None
    data_dictionary: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def permissions(self):
        if self.connection.kind == "mcp":
            if not self.allowed_tools or self.allowed_tables:
                raise ValueError("MCP resources need allowed_tools, not allowed_tables")
            if self.semantic_entrypoint not in self.allowed_tools:
                raise ValueError("semantic entrypoint must be an allowed tool")
        elif (
            not self.allowed_tables or self.allowed_tools or self.semantic_entrypoint != "db_schema"
        ):
            raise ValueError("SQL resources need allowed_tables and db_schema entrypoint")
        return self


class CatalogConfig(StrictModel):
    schema_version: Literal[1]
    resources: list[Resource] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [r.id for r in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate resource IDs")
        return self


class Catalog:
    def __init__(self, path: str | Path, *, refresh=False, offline=False, cache_dir=None):
        try:
            raw = yaml.safe_load(Path(path).read_text())
            from .descriptors import expand_catalog

            raw, self.descriptor_status = expand_catalog(
                raw, Path(path), refresh=refresh, offline=offline, cache_dir=cache_dir
            )
            self.config = CatalogConfig.model_validate(raw)
        except ValidationError as exc:
            locations = [".".join(map(str, e["loc"])) or "resources" for e in exc.errors()]
            raise FabricError(
                "invalid_catalog", "Invalid catalog fields: " + ", ".join(locations)
            ) from None
        except (OSError, yaml.YAMLError):
            raise FabricError(
                "invalid_catalog", "Cannot read a valid YAML catalog at the supplied path"
            ) from None
        self._build()

    def _build(self):
        # Resolve only nonsecret host overrides. Credentials are never loaded here.
        for r in self.config.resources:
            c = r.connection
            if isinstance(c, SQLConnection):
                if c.host_env:
                    c.host = os.environ.get(c.host_env, c.host)
                if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", c.host):
                    raise FabricError("invalid_catalog", "Invalid PostgreSQL host")
        canonical = json.dumps(self.public_config(), sort_keys=True).encode()
        self.revision = hashlib.sha256(canonical).hexdigest()
        self.resources = {r.id: r for r in self.config.resources}
        self.graph = Graph()
        self.graph.bind("eco", ECO)
        self.graph.bind("fabric", FAB)
        self.graph.bind("dcat", DCAT)
        for r in self.resources.values():
            node = URIRef(BASE + r.id)
            for predicate, value in [
                (RDF.type, ECO.Connector),
                (RDF.type, DCAT.DataService),
                (RDFS.label, Term(r.label)),
                (RDFS.comment, Term(r.description)),
                (FAB.resourceId, Term(r.id)),
                (FAB.kind, Term(r.connection.kind)),
                (FAB.semanticEntrypoint, Term(r.semantic_entrypoint)),
                (FAB.connection, Term(json.dumps(r.connection.model_dump()))),
            ]:
                self.graph.add((node, predicate, value))
            if r.publisher_id:
                self.graph.add((node, FAB.publisherId, Term(r.publisher_id)))
            if r.ontology_url:
                self.graph.add((node, FAB.ontology, URIRef(r.ontology_url)))
            for cap in r.capabilities:
                cap_node = URIRef(f"{BASE}{r.id}:capability:{cap}")
                self.graph.add((node, ECO.hasCapability, cap_node))
                self.graph.add((cap_node, RDF.type, ECO.Capability))
                self.graph.add((cap_node, RDFS.label, Term(cap)))
                self.graph.add((cap_node, ECO.providedBy, node))

    def get(self, resource_id: str, revision: str | None = None) -> Resource:
        if revision is not None and revision != self.revision:
            raise FabricError(
                "stale_catalog", "Catalog changed; discover and identify the resource again"
            )
        if resource_id not in self.resources:
            raise FabricError("not_found", "Resource ID is not in the configured catalog")
        return self.resources[resource_id]

    def find(self, query: str = "", kind: str | None = None) -> dict:
        words = re.findall(r"[\w-]+", query.casefold())
        found = []
        for node in self.graph.subjects(RDF.type, ECO.Connector):
            label = str(self.graph.value(node, RDFS.label))
            description = str(self.graph.value(node, RDFS.comment))
            resource_kind = str(self.graph.value(node, FAB.kind))
            caps = sorted(
                str(self.graph.value(c, RDFS.label))
                for c in self.graph.objects(node, ECO.hasCapability)
            )
            haystack = " ".join([label, description, *caps]).casefold()
            matches = sum(word in haystack for word in words)
            if (kind is None or kind == resource_kind) and (not words or matches):
                found.append(
                    {
                        "resource_id": str(self.graph.value(node, FAB.resourceId)),
                        "label": label,
                        "description": description,
                        "kind": resource_kind,
                        "capabilities": caps,
                        "match_count": matches,
                    }
                )
        found.sort(key=lambda r: (-r["match_count"], r["resource_id"]))
        return {
            "revision": self.revision,
            "resources": found,
            "status": "configured; availability and identity not verified",
            "descriptors": self.freshness(),
        }

    def identify(self, resource_id: str) -> dict:
        self.get(resource_id)
        node = URIRef(BASE + resource_id)
        return {
            "resource_id": resource_id,
            "revision": self.revision,
            "iri": str(node),
            "connection": json.loads(str(self.graph.value(node, FAB.connection))),
            "semantic_entrypoint": str(self.graph.value(node, FAB.semanticEntrypoint)),
            "identity_verified": False,
            "publisher_id": self.get(resource_id).publisher_id,
            "ontology_url": self.get(resource_id).ontology_url,
            "descriptor": self.freshness().get(resource_id, {"state": "inline"}),
        }

    def freshness(self):
        statuses = {key: dict(value) for key, value in self.descriptor_status.items()}
        for status in statuses.values():
            if "fetched_at" in status:
                status["age_seconds"] = max(0, int(time.time() - status["fetched_at"]))
                if status["age_seconds"] >= 3600 and status["state"] in {"cached", "fresh"}:
                    status["state"] = "stale"
        return statuses

    def public_config(self):
        config = self.config.model_dump()
        for resource in config["resources"]:
            connection = resource["connection"]
            if connection["kind"] == "postgresql":
                if connection.get("access"):
                    connection.pop("auth_profile", None)
                    connection.pop("host_env", None)
                    connection["requires_credentials"] = True
                else:
                    resource["connection"] = {"kind": "postgresql", "requires_local_profile": True}
            else:
                connection.pop("auth_profile", None)
        return config

    def graph_view(self):
        """Public resource/capability projection of the materialized RDF graph."""
        nodes, edges = [], []
        for node in sorted(self.graph.subjects(RDF.type, ECO.Connector), key=str):
            resource_id = str(self.graph.value(node, FAB.resourceId))
            nodes.append(
                {
                    "data": {
                        "id": str(node),
                        "resource_id": resource_id,
                        "label": str(self.graph.value(node, RDFS.label)),
                        "type": "resource",
                        "kind": str(self.graph.value(node, FAB.kind)),
                    }
                }
            )
            for capability in sorted(self.graph.objects(node, ECO.hasCapability), key=str):
                nodes.append(
                    {
                        "data": {
                            "id": str(capability),
                            "resource_id": resource_id,
                            "label": str(self.graph.value(capability, RDFS.label)),
                            "type": "capability",
                        }
                    }
                )
                edges.append(
                    {
                        "data": {
                            "id": str(capability) + ":provided-by",
                            "source": str(node),
                            "target": str(capability),
                            "predicate": str(ECO.hasCapability),
                            "label": "provides",
                        }
                    }
                )
        return {
            "revision": self.revision,
            "nodes": nodes,
            "edges": edges,
            "projection": "Resource and capability relationships; resource-scoped capabilities are not semantic equivalence claims.",
        }

    def snapshot(self):
        return {
            "revision": self.revision,
            "catalog": self.public_config(),
            "descriptors": self.freshness(),
        }

    def public_identify(self, resource_id):
        result = self.identify(resource_id)
        result["connection"] = next(
            r["connection"] for r in self.public_config()["resources"] if r["id"] == resource_id
        )
        return result
