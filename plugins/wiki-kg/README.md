# wiki-kg

Build, query, and **visualize** a typed-edge knowledge graph from an llm-wiki. It
extracts the wiki's YAML frontmatter and body links into RDF, materializes
inverses / hubs / area inheritance, SHACL-validates, lets you ask structural
questions with a curated SPARQL library, and renders the graph as an interactive
map for navigation. Runs in-process with `rdflib` + `pyshacl`, no Jena and no
Fuseki server.

This plugin ships two skills: **`wiki-kg`** (build + query) and
**`wiki-graph-viz`** (render the interactive map). The agent-facing usage is in
each skill's `SKILL.md`; this file is the human reference.

## Using it

After installing, activate with `/reload-plugins` (Claude Code) or a restart
(Codex). Then:

- **Build:** ask "build the knowledge graph for my wiki" or run
  `bash scripts/build-graph.sh --wiki=<path-to-wiki>`.
- **Query:** ask "which notes are hubs / orphans?" or run
  `bash scripts/query-graph.sh <name>`.

## Commands

```bash
scripts/build-graph.sh --wiki=<path>     # build; --stats, --help for more
scripts/query-graph.sh <name>            # run a curated query (TSV output)
scripts/query-graph.sh path/to.rq        # a query file
scripts/query-graph.sh - < query.rq      # SPARQL on stdin
```

Curated queries in `scripts/sparql/`: `hub-notes`, `orphan-notes`,
`ancestors-of`, `children-of`, `extension-chains`, `supports-criticizes`,
`notes-by-type`, `notes-by-tag`, `note-neighborhood`, `search-by-title`,
`graph-stats`.

## Outputs

Written to `scripts/build/` (gitignored):

| File | Contents |
|---|---|
| `graph.jsonld` | JSON-LD from frontmatter + body links |
| `graph.ttl` | Turtle translation |
| `graph-weights.ttl` | RDF-star weighted `mentions` (kept separate; rdflib's Turtle parser rejects `<< s p o >>`) |
| `graph-full.ttl` | `graph.ttl` plus materialized inverses, hubs, area inheritance — this is what `query-graph` reads |
| `validation-report.ttl` | SHACL conformance report |

## Visualize (the `wiki-graph-viz` skill)

Render the graph as a self-contained interactive HTML map for navigation, useful
once a wiki grows past ~30 pages and the flat index is hard to scan.

```bash
bash skills/wiki-graph-viz/scripts/build-viz.sh \
  --graph skills/wiki-kg/scripts/build/graph.jsonld \
  --repo <owner>/<repo> \
  --wiki <path-to-wiki> \
  --out graph.html
```

Open `graph.html` in a browser. It uses Cytoscape.js (inlined, offline) with the
`cose` layout (static: computed once, settles at rest, no perpetual motion).
Nodes are colored by type and sized by degree; edges are typed and legended;
there is a type filter, edge-type toggles, a search box, node drag, and zoom.
**Click a node to open its GitHub wiki page.** A **Color by: Type / Recency**
toggle recolors nodes by git commit date (darker = older) with a colorbar
showing the date span (needs `--wiki`). Dangling links appear as grey
`Unresolved` stubs. No venv or third-party Python; pure `python3` stdlib.

## Requirements and setup

- `bash` and `python3` (with `venv`).
- Dependencies (`rdflib`, `pyshacl`, `pyyaml`) are installed into a venv on the
  first build, at `~/.llm-wiki-kg/venv` (override with `LLM_WIKI_KG_VENV`). No
  system-wide pip changes.
- The first build fetches the ontology / SHACL shapes / JSON-LD context from the
  published LA3D spec (`https://la3d.github.io/llm-wiki-colab/`) and caches them
  under `scripts/.cache/`, so the first build needs network access.

## Notes

- Companion to the wiki: point `--wiki` at your `wiki/<repo>.wiki/` directory.
- In-process only (no server). A Fuseki/SPARQL-endpoint mode is intentionally
  out of scope here.

## Source

Ported from `scripts/kg/` in
[`LA3D-LLM-Agents/llm-wiki-KB`](https://github.com/LA3D-LLM-Agents/llm-wiki-KB);
maintained here in the marketplace (marketplace-as-source).
