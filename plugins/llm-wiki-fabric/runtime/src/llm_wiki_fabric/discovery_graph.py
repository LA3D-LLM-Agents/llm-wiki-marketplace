"""Metadata-only mapping to pinned eco 0.2.0. No domain ontology imports or data queries."""

import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pyshacl import validate
from rdflib import DCAT, DCTERMS, RDF, RDFS, XSD, Graph, URIRef
from rdflib import Literal as Term

from .catalog import BASE, ECO, FAB, FabricError

VERSION = "0.2.0"
PINS = {
    "eco.ttl": "b3e2263425c99758515980ad0f71737991882f72771ed064c0b5303b3304a70a",
    "eco-discovery-shapes.ttl": "2a45a1ce3ef23f04fc3570438a120d61d2156b1d94ca2de9e167b6ad10b6a8ea",
}


def build_graph(resources, public):
    graph, discovery = Graph(), {}
    graph.bind("eco", ECO)
    graph.bind("fabric", FAB)
    graph.bind("dcat", DCAT)
    graph.bind("dct", DCTERMS)
    public_resources = {r["id"]: r for r in public["resources"]}
    for resource in resources:
        node = URIRef(BASE + resource.id)
        public_connection = public_resources[resource.id]["connection"]
        details = {
            "consumption_mode": "direct-access",
            "services": [],
            "semantic_entrypoints": [],
            "access_requirements": [],
            "capability_status": "advertised; not a grant or a guarantee of connector support",
        }
        discovery[resource.id] = details
        for predicate, value in [
            (RDF.type, ECO.Connector),
            (RDF.type, ECO.Participant),
            (RDF.type, DCAT.DataService),
            (RDFS.label, Term(resource.label)),
            (RDFS.comment, Term(resource.description)),
            (FAB.resourceId, Term(resource.id)),
            (FAB.kind, Term(resource.connection.kind)),
            (FAB.semanticEntrypoint, Term(resource.semantic_entrypoint)),
            (ECO.consumptionMode, ECO.DirectAccess),
        ]:
            graph.add((node, predicate, value))
        if resource.publisher_id:
            graph.add((node, FAB.publisherId, Term(resource.publisher_id)))
        for cap in resource.capability_details:
            cap_node = URIRef(f"{node}:capability:{quote(cap.id, safe='')}")
            for triple in [
                (node, ECO.hasCapability, cap_node),
                (cap_node, RDF.type, ECO.Capability),
                (cap_node, DCTERMS.identifier, Term(cap.id)),
                (cap_node, RDFS.label, Term(cap.name)),
                (cap_node, RDFS.comment, Term(cap.description)),
                (cap_node, ECO.providedBy, node),
            ]:
                graph.add(triple)
            for tag in cap.tags:
                graph.add((cap_node, ECO.keyword, Term(tag)))

        primary = URIRef(f"{node}:service:primary")
        transport = "streamable-http" if resource.connection.kind == "mcp" else "stdio"
        primary_record = {"id": str(primary), "kind": "MCPEndpoint", "transport": transport}
        if resource.connection.kind == "mcp":
            primary_record["url"] = public_connection["url"]
            graph.add(
                (primary, ECO.serviceURL, Term(public_connection["url"], datatype=XSD.anyURI))
            )
            graph.add((node, DCAT.endpointURL, URIRef(public_connection["url"])))
        else:
            primary_record["binding"] = "local direct connector"
        graph.add(
            (
                primary,
                RDFS.label,
                Term(
                    "MCP endpoint" if transport == "streamable-http" else "Local direct connector"
                ),
            )
        )
        details["services"].append(primary_record)
        graph.add((node, ECO.hasService, primary))
        graph.add((primary, RDF.type, ECO.MCPEndpoint))
        graph.add((primary, RDF.type, ECO.ServiceEndpoint))
        graph.add((primary, ECO.transport, Term(transport)))
        for service in resource.services:
            endpoint = URIRef(f"{node}:service:advertised:{quote(service.id, safe='')}")
            graph.add((node, ECO.hasService, endpoint))
            graph.add((endpoint, RDFS.label, Term(service.kind)))
            graph.add((endpoint, RDF.type, ECO[service.kind]))
            graph.add((endpoint, RDF.type, ECO.ServiceEndpoint))
            graph.add((endpoint, ECO.serviceURL, Term(service.url, datatype=XSD.anyURI)))
            details["services"].append(
                {"id": str(endpoint), "kind": service.kind, "url": service.url}
            )
            if service.kind == "OpenAPI":
                graph.add((node, DCAT.endpointDescription, URIRef(service.url)))

        def semantic(suffix, kind, url=None):
            entry = URIRef(f"{node}:semantics:{suffix}")
            location = "ResourceSide" if resource.connection.kind == "mcp" else "ClientSide"
            graph.add((node, ECO.hasSemanticEntrypoint, entry))
            graph.add(
                (
                    entry,
                    RDFS.label,
                    Term(
                        {
                            "PublishedOntology": "Source ontology",
                            "DataDictionary": "Data dictionary",
                            "LiveSchema": "Live schema",
                        }.get(kind, "Semantic entrypoint")
                    ),
                )
            )
            graph.add((entry, RDF.type, ECO[kind]))
            graph.add((entry, RDF.type, ECO.SemanticEntrypoint))
            graph.add((entry, ECO.toolName, Term(resource.semantic_entrypoint)))
            graph.add((entry, ECO.invokedThrough, primary))
            graph.add((entry, ECO.executionLocation, ECO[location]))
            record = {
                "id": str(entry),
                "kind": kind,
                "tool_name": resource.semantic_entrypoint,
                "invoked_through": str(primary),
                "execution_location": location,
            }
            if url:
                record["document_url"] = url
                graph.add((entry, ECO.documentURL, Term(url, datatype=XSD.anyURI)))
            details["semantic_entrypoints"].append(record)
            return entry

        if resource.connection.kind == "mcp":
            if resource.ontology_url:
                entry = semantic("ontology", "PublishedOntology", resource.ontology_url)
                graph.add((entry, RDF.type, ECO.Ontology))
                graph.add((node, ECO.hasOntology, entry))
                # Compatibility pointer: the source ontology stays external.
                graph.add((node, FAB.ontology, URIRef(resource.ontology_url)))
            else:
                semantic("entrypoint", "SemanticEntrypoint")
        else:
            semantic("schema", "LiveSchema")
            if resource.data_dictionary:
                semantic("dictionary", "DataDictionary")
            requirements = [
                (
                    "database",
                    "DatabaseAuthentication",
                    public_connection.get("database", "client-profile"),
                )
            ]
            if public_connection.get("access"):
                requirements.append(
                    ("ssh", "SSHAuthentication", public_connection["access"]["host"])
                )
            for suffix, mechanism, target in requirements:
                requirement = URIRef(f"{node}:access:{suffix}")
                graph.add((node, ECO.requiresAccess, requirement))
                graph.add(
                    (
                        requirement,
                        RDFS.label,
                        Term(
                            "SSH access" if mechanism == "SSHAuthentication" else "Database access"
                        ),
                    )
                )
                graph.add((requirement, RDF.type, ECO.AccessRequirement))
                graph.add((requirement, ECO.accessMechanism, ECO[mechanism]))
                graph.add((requirement, ECO.accessTarget, Term(target)))
                details["access_requirements"].append({"mechanism": mechanism, "target": target})
    return graph, discovery


@lru_cache(maxsize=1)
def pinned_sources():
    directory = Path(__file__).parent / "ontology"
    sources = {}
    for name, expected in PINS.items():
        raw = (directory / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise FabricError("ontology_pin_mismatch", "Bundled discovery ontology hash mismatch")
        sources[name] = raw.decode()
    return sources


def validate_graph(graph):
    sources = pinned_sources()
    conforms, report, _ = validate(
        graph,
        ont_graph=Graph().parse(data=sources["eco.ttl"], format="turtle"),
        shacl_graph=Graph().parse(data=sources["eco-discovery-shapes.ttl"], format="turtle"),
        inference="rdfs",
        do_owl_imports=False,
    )
    if not conforms:
        # Do not echo publisher-supplied values or arbitrary validator diagnostics.
        from rdflib import Namespace

        sh = Namespace("http://www.w3.org/ns/shacl#")
        paths = sorted({str(value) for value in report.objects(None, sh.resultPath)})
        raise FabricError(
            "invalid_discovery_graph",
            "Discovery graph failed SHACL validation" + (": " + ", ".join(paths) if paths else ""),
        )
    return {
        "version": VERSION,
        "url": "https://la3d-llm-agents.github.io/ns/versions/0.2.0/eco.ttl",
        "sha256": PINS["eco.ttl"],
        "shapes_sha256": PINS["eco-discovery-shapes.ttl"],
        "validation": "conforms",
        "scope": "resource discovery metadata; no source ontology imported",
    }
