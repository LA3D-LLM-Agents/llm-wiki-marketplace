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
from rdflib import RDF, RDFS, Namespace, URIRef

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


class Capability(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=100)


class PublishedService(StrictModel):
    id: Identifier
    kind: Literal["AgentCard", "OpenAPI"]
    url: str

    @model_validator(mode="after")
    def safe_url(self):
        MCPConnection(kind="mcp", url=self.url, transport="streamable-http")
        return self


class Resource(StrictModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    capabilities: list[Identifier] = Field(min_length=1)
    capability_details: list[Capability] = Field(default_factory=list)
    services: list[PublishedService] = Field(default_factory=list)
    semantic_entrypoint: Identifier
    connection: Annotated[MCPConnection | SQLConnection, Field(discriminator="kind")]
    allowed_tools: list[Identifier] = Field(default_factory=list)
    allowed_tables: list[Identifier] = Field(default_factory=list)

    publisher_id: str | None = None
    ontology_url: str | None = None
    data_dictionary: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def permissions(self):
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capability IDs")
        if not self.capability_details:
            self.capability_details = [Capability(id=cap, name=cap) for cap in self.capabilities]
        detail_ids = [cap.id for cap in self.capability_details]
        if len(detail_ids) != len(set(detail_ids)) or set(detail_ids) != set(self.capabilities):
            raise ValueError("Capability details must match the advertised IDs exactly")
        if any(
            len(tag) > 200 or not tag.strip() for cap in self.capability_details for tag in cap.tags
        ):
            raise ValueError("Invalid capability tags")
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("Duplicate service IDs")
        if self.ontology_url:
            MCPConnection(kind="mcp", url=self.ontology_url, transport="streamable-http")
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
    schema_version: Literal[1, 2]
    ontology_version: Literal["0.2.0"] = "0.2.0"
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
        revision = hashlib.sha256(canonical).hexdigest()
        resources = {r.id: r for r in self.config.resources}
        from .discovery_graph import build_graph, validate_graph

        graph, discovery = build_graph(self.config.resources, self.public_config())
        profile = validate_graph(graph)
        self.revision, self.resources = revision, resources
        self.graph, self.discovery, self.ontology_profile = graph, discovery, profile

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
            resource_id = str(self.graph.value(node, FAB.resourceId))
            details = [cap.model_dump() for cap in self.resources[resource_id].capability_details]
            haystack = " ".join(
                [
                    label,
                    description,
                    *caps,
                    *[
                        " ".join([c["id"], c["name"], c["description"], *c["tags"]])
                        for c in details
                    ],
                ]
            ).casefold()
            matches = sum(word in haystack for word in words)
            if (kind is None or kind == resource_kind) and (not words or matches):
                found.append(
                    {
                        "resource_id": str(self.graph.value(node, FAB.resourceId)),
                        "label": label,
                        "description": description,
                        "kind": resource_kind,
                        "capabilities": self.resources[resource_id].capabilities,
                        "capability_details": details,
                        "match_count": matches,
                    }
                )
        found.sort(key=lambda r: (-r["match_count"], r["resource_id"]))
        return {
            "revision": self.revision,
            "resources": found,
            "status": "configured; availability and identity not verified",
            "descriptors": self.freshness(),
            "ontology_profile": self.ontology_profile,
        }

    def identify(self, resource_id: str) -> dict:
        self.get(resource_id)
        node = URIRef(BASE + resource_id)
        return {
            "resource_id": resource_id,
            "revision": self.revision,
            "iri": str(node),
            **self.discovery[resource_id],
            "capability_details": [
                cap.model_dump() for cap in self.get(resource_id).capability_details
            ],
            "ontology_profile": self.ontology_profile,
            "connection": self.get(resource_id).connection.model_dump(),
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
        config["schema_version"] = 2
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
            for relation, node_type in [
                (ECO.hasService, "service"),
                (ECO.hasSemanticEntrypoint, "semantic_entrypoint"),
                (ECO.requiresAccess, "access_requirement"),
            ]:
                for target in sorted(self.graph.objects(node, relation), key=str):
                    label = str(
                        self.graph.value(target, RDFS.label) or str(target).rsplit(":", 1)[-1]
                    )
                    nodes.append(
                        {
                            "data": {
                                "id": str(target),
                                "resource_id": resource_id,
                                "label": label,
                                "type": node_type,
                            }
                        }
                    )
                    edges.append(
                        {
                            "data": {
                                "id": str(node)
                                + ":"
                                + str(relation).split("#")[-1]
                                + ":"
                                + str(target),
                                "source": str(node),
                                "target": str(target),
                                "predicate": str(relation),
                                "label": str(relation).split("#")[-1],
                            }
                        }
                    )
            for entry in self.graph.objects(node, ECO.hasSemanticEntrypoint):
                for endpoint in self.graph.objects(entry, ECO.invokedThrough):
                    edges.append(
                        {
                            "data": {
                                "id": str(entry) + ":invoked-through",
                                "source": str(entry),
                                "target": str(endpoint),
                                "predicate": str(ECO.invokedThrough),
                                "label": "invoked through",
                            }
                        }
                    )
        return {
            "revision": self.revision,
            "nodes": nodes,
            "edges": edges,
            "ontology_profile": self.ontology_profile,
            "projection": "Resource, capability, service, semantic-entrypoint and prerequisite metadata; no source domain ontology is imported.",
        }

    def snapshot(self):
        return {
            "revision": self.revision,
            "catalog": self.public_config(),
            "discovery": self.discovery,
            "descriptors": self.freshness(),
            "ontology_profile": self.ontology_profile,
        }

    def public_identify(self, resource_id):
        result = self.identify(resource_id)
        result["connection"] = next(
            r["connection"] for r in self.public_config()["resources"] if r["id"] == resource_id
        )
        return result
