# Pinned discovery vocabulary

The Turtle files are unmodified copies from LA3D-LLM-Agents/ns commit
7650c279b1cc03750cc14722460d78030f86c8e6, version 0.3.0:

- https://la3d-llm-agents.github.io/ns/versions/0.3.0/eco.ttl
- https://la3d-llm-agents.github.io/ns/versions/0.3.0/eco-discovery-shapes.ttl

`agent-card-1.0.schema.json` is an unmodified copy of
`examples/agent-card.schema.json` from the same commit, also vendored by the
federation index reader. Card schema 1.0 is distinct from ontology version 0.3.0.

SHA-256 Turtle pins are enforced in discovery_graph.py. Validation uses bundled
artifacts without fetching imports or domain ontologies. Declared inverses are
materialized on a copy before SHACL validation. Public graph views use only
forward predicates so inverse expansion does not duplicate visual relationships.
