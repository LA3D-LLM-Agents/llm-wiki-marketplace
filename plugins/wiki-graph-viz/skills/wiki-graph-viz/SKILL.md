---
name: wiki-graph-viz
description: Render a wiki's knowledge graph as a self-contained interactive HTML map for navigation. Use when a wiki has grown past ~30 pages and the flat index is hard to navigate; produces a clickable node-link map where each page opens its GitHub wiki page.
---

You render a wiki's structural graph as one self-contained interactive HTML file
that a person can open in a browser to navigate the wiki visually. It reads the
JSON-LD produced by the `wiki-kg` plugin, so build that graph first. Pure
`python3` standard library, no venv or dependencies.

The user's input is: `$ARGUMENTS`

## Build the map

```bash
bash "${CLAUDE_SKILL_DIR:-$SKILL_DIRECTORY}/scripts/build-viz.sh" \
  --graph <path-to>/graph.jsonld \
  --repo <owner>/<repo> \
  --out graph.html
```

- `--graph` points at `graph.jsonld` from a `wiki-kg` build (run its
  `build-graph.sh --wiki=<wiki>` first; the file lands in that plugin's
  `scripts/build/graph.jsonld`).
- `--repo <owner>/<repo>` builds the click-through URLs as
  `https://github.com/<owner>/<repo>/wiki/<Page>`. Use `--wiki-base <url>` to
  set a different base explicitly.
- `--out` is the HTML file to write (default `graph.html`); open it in a
  browser.

Report the node/edge counts it prints and the output path.

## What the map shows

- A force-directed node-link graph: nodes are pages colored by `type:`, sized by
  degree so hubs stand out and orphans sit alone; edges are the typed
  relationships (extends, supports, criticizes, mentions, partOf, up, ...).
- A type filter, an edge-type legend with per-predicate toggles, a search box,
  node drag, and zoom/pan.
- Click a node to open its GitHub wiki page in a new tab.
- Dangling link targets (references to pages that do not exist yet) appear as
  `Unresolved` stub nodes, a cue for missing pages.

## Notes

- Needs `bash` and `python3` only; no venv, no third-party packages.
- Companion to `wiki-kg`: it consumes that plugin's `graph.jsonld`. If the
  graph is stale, rebuild it with `wiki-kg` first.
- The output is a single self-contained HTML file (inline JS, no network), so
  it opens offline and can be shared as-is.
