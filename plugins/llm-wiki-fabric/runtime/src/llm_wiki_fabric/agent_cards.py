"""Validate federation metadata and map card documents separately from invocation."""

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from jsonschema import Draft202012Validator
from rdflib import DCTERMS, RDF, RDFS, XSD, Graph, Literal, URIRef

from .catalog import ECO, FAB

SCHEMA = json.loads((Path(__file__).parent / "ontology/agent-card-1.0.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def normalize_entry(item):
    """Accept legacy rows and structured rows, retaining the legacy client projection."""
    from .agents import validate_identity

    if not isinstance(item, dict):
        raise ValueError("Invalid federation entry")
    item = dict(item)
    if "x-fabric-card" in item:
        card = item["x-fabric-card"]
        error = next(VALIDATOR.iter_errors(card), None)
        if error:
            raise ValueError("Invalid x-fabric-card schema")
        for field in ("skills", "knowledge_bundles", "interfaces"):
            ids = [record["id"] for record in card[field]]
            if len(ids) > 100 or len(ids) != len(set(ids)):
                raise ValueError("Invalid or duplicate card identifiers")
        bundles = {b["id"] for b in card["knowledge_bundles"]}
        for interface in card["interfaces"]:
            if interface["kind"] == "clone-and-invoke" and interface["bundle_id"] not in bundles:
                raise ValueError("Unknown knowledge bundle")
        for key, value in {
            "id": card["id"],
            "card_url": card["card_url"],
            "description": card["description"].strip(),
            "capabilities": [skill["description"] for skill in card["skills"]],
        }.items():
            if key in item and item[key] != value:
                raise ValueError("Conflicting structured and legacy card fields")
            item[key] = value
    validate_identity(item["id"], item["card_url"])
    metadata = {
        key: item.get(key)
        for key in (
            "id",
            "owner_repo",
            "description",
            "card_url",
            "home_url",
            "topics",
            "capabilities",
        )
    }
    metadata["topics"] = item.get("topics", [])
    metadata["capabilities"] = item.get("capabilities", [])
    if not isinstance(metadata["description"], str) or len(metadata["description"]) > 4000:
        raise ValueError("Invalid description")
    for key in ("topics", "capabilities"):
        values = metadata[key]
        if (
            not isinstance(values, list)
            or len(values) > 100
            or any(not isinstance(v, str) or len(v) > 1000 for v in values)
        ):
            raise ValueError("Invalid agent metadata")
    for key in ("wiki_clone_url", "x-fabric-card"):
        if key in item:
            metadata[key] = item[key]
    if "wiki_clone_url" in metadata:
        from .descriptors import https_url

        https_url(metadata["wiki_clone_url"])
    return metadata


def agent_node(agent_id):
    return URIRef("urn:fabric:agent:" + quote(agent_id, safe=""))


def map_entry(graph, entry, *, unverified_claim=False):
    agent = agent_node(entry["id"])
    card = entry.get("x-fabric-card")
    document = URIRef(f"{agent}:claimed-card" if unverified_claim else entry["card_url"])
    for triple in (
        (agent, RDF.type, ECO.Agent),
        (agent, DCTERMS.identifier, Literal(entry["id"])),
        (agent, RDFS.label, Literal(card["name"] if card else entry["id"])),
        (agent, RDFS.comment, Literal(entry["description"])),
        (agent, ECO.hasCard, document),
        (document, RDF.type, ECO.AgentCard),
        (document, RDFS.label, Literal("Agent card")),
        (document, ECO.documentURL, Literal(entry["card_url"], datatype=XSD.anyURI)),
        (document, DCTERMS.format, Literal("text/markdown")),
        (document, DCTERMS.hasVersion, Literal(card["schema_version"] if card else "legacy")),
    ):
        graph.add(triple)
    for topic in entry["topics"]:
        graph.add((agent, DCTERMS.subject, Literal(topic)))
    # Legacy strings have no authored skill IDs. Content hashes are local mapping
    # identifiers only and are never written back as a structured card.
    skills = (
        card["skills"]
        if card
        else [
            {
                "id": "legacy-" + hashlib.sha256(text.encode()).hexdigest(),
                "name": text or "Unspecified capability",
                "description": text,
                "tags": [],
            }
            for text in entry["capabilities"]
        ]
    )
    for skill in skills:
        node = URIRef(f"{agent}:skill:{skill['id']}")
        for triple in (
            (agent, ECO.hasCapability, node),
            (node, RDF.type, ECO.Capability),
            (node, DCTERMS.identifier, Literal(skill["id"])),
            (node, RDFS.label, Literal(skill["name"])),
            (node, RDFS.comment, Literal(skill["description"])),
        ):
            graph.add(triple)
        for tag in skill["tags"]:
            graph.add((node, ECO.keyword, Literal(tag)))
    if not card:
        # A legacy index clone URL is an explicit route, not a live service claim.
        if entry.get("wiki_clone_url"):
            route = URIRef(f"{agent}:interface:legacy-wiki")
            graph.add((agent, ECO.hasService, route))
            graph.add((agent, ECO.consumptionMode, ECO.CloneAndInvoke))
            graph.add((route, RDF.type, ECO.CloneEndpoint))
            graph.add((route, RDFS.label, Literal("Wiki clone invocation")))
            graph.add(
                (route, ECO.serviceURL, Literal(entry["wiki_clone_url"], datatype=XSD.anyURI))
            )
        return
    bundles = {b["id"]: URIRef(b["url"]) for b in card["knowledge_bundles"]}
    for bundle_id, node in bundles.items():
        graph.add((node, RDF.type, ECO.Bundle))
        graph.add((node, RDFS.label, Literal(bundle_id)))
        graph.add((agent, ECO.hasKnowledgeBundle, node))
    kinds = {"clone-and-invoke": ECO.CloneEndpoint, "mcp": ECO.MCPEndpoint, "a2a": ECO.A2AEndpoint}
    for interface in card["interfaces"]:
        node = URIRef(f"{agent}:interface:{interface['id']}")
        kind = interface["kind"]
        graph.add((agent, ECO.hasService, node))
        graph.add((node, RDF.type, kinds[kind]))
        graph.add((node, RDFS.label, Literal(interface["id"])))
        graph.add((node, ECO.serviceURL, Literal(interface["url"], datatype=XSD.anyURI)))
        if kind == "clone-and-invoke":
            graph.add((bundles[interface["bundle_id"]], ECO.hasService, node))
            graph.add((agent, ECO.consumptionMode, ECO.CloneAndInvoke))
        else:
            graph.add((node, ECO.transport, Literal(interface["transport"])))
            if kind == "a2a":
                graph.add((node, DCTERMS.hasVersion, Literal(interface["protocol_version"])))


def federation_graph(entries, activity=()):
    """Local metadata graph; activity uses public session IDs, never bearer tokens."""
    from .discovery_graph import materialize_inverses, validate_graph

    graph = Graph()
    for entry in entries.values():
        map_entry(graph, entry)
    for announcement in activity:
        node = agent_node(announcement["id"])
        if announcement["id"] not in entries:
            # Unlisted announcements remain explicitly unverified claims.
            map_entry(
                graph,
                {
                    "id": announcement["id"],
                    "card_url": announcement["card_url"],
                    "description": "",
                    "topics": [],
                    "capabilities": [],
                },
                unverified_claim=True,
            )
        graph.add((node, FAB.identityVerification, Literal("unverified")))
        for session in announcement["sessions"]:
            session_node = URIRef("urn:fabric:session:" + session["id"])
            graph.add((session_node, RDF.type, ECO.AgentSession))
            graph.add((session_node, RDFS.label, Literal("Announced session")))
            graph.add((session_node, ECO.sessionOfAgent, node))
    graph = materialize_inverses(graph)
    validate_graph(graph)
    return graph
