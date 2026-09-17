# wiki-graph-viz

Render a wiki's knowledge graph as a **self-contained interactive HTML map** for
navigation. Once a wiki grows past ~30 pages the flat `index` stops being
navigable; this gives you a clickable node-link map where you can see the
structure, spot hubs and orphans, and jump straight to a page.

It reads the JSON-LD produced by the `wiki-kg` plugin, so it renders the same
typed edges kg extracts (frontmatter edges, body `mentions`, Variant-1 typed
body edges, `up` hierarchy). Pure `python3` standard library, no venv.

## Using it

Build the graph with `wiki-kg` first, then render it:

```bash
# 1. build the graph (wiki-kg plugin)
bash <wiki-kg>/scripts/build-graph.sh --wiki=<path-to-wiki>

# 2. render the map (this plugin)
bash <wiki-graph-viz>/scripts/build-viz.sh \
  --graph <wiki-kg>/scripts/build/graph.jsonld \
  --repo <owner>/<repo> \
  --out graph.html
```

Then open `graph.html` in a browser.

## Options

| Flag | Meaning |
|---|---|
| `--graph PATH` | the `graph.jsonld` from a `wiki-kg` build (required) |
| `--repo OWNER/REPO` | build click-through URLs as `https://github.com/OWNER/REPO/wiki/<Page>` |
| `--wiki-base URL` | explicit base URL for click-through (overrides `--repo`) |
| `--out FILE` | output HTML file (default `graph.html`) |
| `--title TITLE` | page title |

## What the map does

- Force-directed node-link graph; nodes colored by `type:`, **sized by degree**
  so hubs stand out and orphans sit alone.
- Typed edges (extends, supports, criticizes, mentions, partOf, `up`, ...) with
  an edge-type legend and per-predicate toggles.
- Type filter, search box, node drag, and zoom/pan.
- **Click a node to open its GitHub wiki page** in a new tab.
- Dangling link targets appear as grey `Unresolved` stub nodes, a cue for
  pages that are referenced but do not exist yet.

## Requirements

`bash` and `python3` only (standard library; no venv, no third-party packages).
The output is a single self-contained HTML file (inline JS, no network), so it
opens offline and can be shared as-is.

## Composition

Companion to `wiki-kg`: kg builds the graph, this renders it. If the map looks
stale, rebuild the graph with `wiki-kg` (`--refresh-spec` to also re-fetch the
ontology/shapes).
