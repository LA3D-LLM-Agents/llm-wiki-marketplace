---
name: wiki-kg
description: Build and query a typed-edge knowledge graph from an llm-wiki. Use to materialize the wiki's frontmatter and body links as RDF and answer structural questions (hubs, orphans, ancestors, extension chains, supports/criticizes) via SPARQL.
---

You operate the wiki knowledge-graph pipeline: two scripts under
`${CLAUDE_SKILL_DIR:-$SKILL_DIRECTORY}/scripts/`. It runs in-process with rdflib
and pyshacl (no Jena, no Fuseki, no server). The first build creates a Python
venv and installs the dependencies; later runs reuse it.

The user's input is: `$ARGUMENTS`

## Build the graph

```bash
bash "${CLAUDE_SKILL_DIR:-$SKILL_DIRECTORY}/scripts/build-graph.sh" --wiki=<path-to-wiki>
```

Extracts JSON-LD from the wiki's YAML frontmatter and body links, converts to
Turtle, materializes inverses / hubs / area inheritance, and SHACL-validates.
Outputs land in the skill's `scripts/build/` directory, notably
`graph-full.ttl` (the queryable graph) and `validation-report.ttl`. Pass
`--stats` for extractor statistics, `--help` for all flags. First run prints a
one-time venv/setup message.

## Query the graph

```bash
bash "${CLAUDE_SKILL_DIR:-$SKILL_DIRECTORY}/scripts/query-graph.sh" <name>
```

`<name>` is a curated query from `scripts/sparql/`:
`hub-notes`, `orphan-notes`, `ancestors-of`, `children-of`, `extension-chains`,
`supports-criticizes`, `notes-by-type`, `notes-by-tag`, `note-neighborhood`,
`search-by-title`, `graph-stats`. You can also pass a `.rq` file path, or `-`
to read SPARQL from stdin. Output is TSV (a header row of variables, then
results). Build the graph first; query reads `build/graph-full.ttl`.

## Notes

- Needs `bash` and `python3` (with `venv`); dependencies (`rdflib`, `pyshacl`)
  are installed into a venv on first build, at `~/.llm-wiki-kg/venv` (override
  with `LLM_WIKI_KG_VENV`).
- The build fetches the ontology / SHACL shapes / JSON-LD context from the
  published LA3D spec URL on first use and caches them; the first build needs
  network access.
- This is a companion to the wiki: it reads an existing llm-wiki. Point
  `--wiki` at your `wiki/<repo>.wiki/` directory.
