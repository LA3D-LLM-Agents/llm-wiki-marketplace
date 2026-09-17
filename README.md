# llm-wiki-marketplace

A marketplace of small plugins for the llm-wiki family, served to both **Claude
Code** and **Codex** from one repository.

Two root marketplace manifests (`.claude-plugin/marketplace.json` for Claude,
`.agents/plugins/marketplace.json` for Codex) both point at a single shared
plugin tree under `plugins/<name>/`. Because these plugins carry no
harness-specific logic (no hooks; the only per-harness detail is the skill's
directory variable, handled inline), there is **no per-harness duplication and
no build step**, unlike a plugin set whose hooks differ per harness.

## Plugins

| Plugin | What it does |
|---|---|
| **wiki-agent-msg** | List and message coding-agent sessions across Claude Code and Codex on the same machine. `agents-list` shows every running agent tagged by platform; `agents-send <target> "<msg>"` routes by the target's platform (Codex -> `codex queue`; Claude -> a `claude -p` relay that calls `SendMessage`). |
| **wiki-kg** | Build and query a typed-edge knowledge graph from an llm-wiki. `build-graph.sh` materializes frontmatter + body links as RDF (rdflib + pyshacl, in-process, no server; venv self-bootstraps on first run); `query-graph.sh <name>` runs a curated SPARQL query (hubs, orphans, ancestors, extension chains, supports/criticizes). |
| **wiki-graph-viz** | Render a wiki's knowledge graph as a self-contained interactive HTML map for navigation. Reads `wiki-kg`'s `graph.jsonld` and emits a force-directed node-link map (color by type, size by degree, type filter, edge-type toggles, search, drag, zoom); click a node to open its GitHub wiki page. Pure stdlib, no venv. |

## Install

Add the marketplace once, then install any plugin from the table above.
Replace `<plugin>` with a plugin name (`wiki-agent-msg`, `wiki-kg`, `wiki-graph-viz`).

### Claude Code

```
/plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
/plugin install <plugin>@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
codex plugin add <plugin>@llm-wiki-marketplace
```

Codex requires a restart after a plugin change. Codex updates are keyed on the
plugin's manifest version, not the commit, so a release only reaches Codex users
when that version is bumped.

## Activate and use

Plugins register at **session start**, so after installing, activate them with
`/reload-plugins` (Claude Code) or a restart (Codex), otherwise the new skill
will not appear in the running session. Then invoke a plugin two ways:

- **Ask in natural language** — skills are model-invocable, so a plain request
  runs them (e.g. "list the agent sessions", "message <session>: <text>").
- **Slash command** — `/<plugin-name>:<skill-name>` on Claude Code (e.g.
  `/wiki-agent-msg:wiki-agent-msg`, `/wiki-kg:wiki-kg`, `/wiki-graph-viz:wiki-graph-viz`).

See each plugin's own README for its usage:
[`wiki-agent-msg`](plugins/wiki-agent-msg/README.md),
[`wiki-kg`](plugins/wiki-kg/README.md),
[`wiki-graph-viz`](plugins/wiki-graph-viz/README.md).

## Update

Update the marketplace, then the specific plugin (`<plugin>` = any installed one).

### Claude Code

```
claude plugin marketplace update llm-wiki-marketplace
claude plugin update <plugin>@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace upgrade
codex plugin add <plugin>@llm-wiki-marketplace
```

## Uninstall

Remove a single plugin, or the whole marketplace (the `marketplace remove` line).

### Claude Code

```
/plugin uninstall <plugin>@llm-wiki-marketplace
/plugin marketplace remove llm-wiki-marketplace
```

### Codex

```
codex plugin remove <plugin>@llm-wiki-marketplace
codex plugin marketplace remove llm-wiki-marketplace
```

## Requirements

`bash`, `python3`, and the `claude` and/or `codex` CLIs on PATH. A harness with no
CLI installed is simply omitted from the agent list rather than erroring, so a
Claude-only or Codex-only machine still works for the harness it has.

## Adding a plugin

The marketplace is structured to grow. To add a plugin named `<name>`:

1. Create one shared tree `plugins/<name>/` containing both
   `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`, plus
   `skills/<name>/SKILL.md` (+ `scripts/` as needed). Reference the skill dir as
   `${CLAUDE_SKILL_DIR:-$SKILL_DIRECTORY}` so one `SKILL.md` works on both
   harnesses.
2. Add one entry to each root manifest, both pointing at the same tree:
   - `.claude-plugin/marketplace.json` -> `plugins[]` with `"source": "./plugins/<name>"`.
   - `.agents/plugins/marketplace.json` -> `plugins[]` with `"source": {"source": "local", "path": "./plugins/<name>"}`.
3. Bump the plugin's `version` in both `plugin.json` files on each release (Codex
   installs track the manifest version).

If a future plugin needs genuinely harness-specific behavior (e.g. different
hooks per harness), split just that plugin into `claude/plugins/<name>/` and
`codex/plugins/<name>/` and point each root manifest at its own copy. The shared
layout is the default; the split is the exception.

## Layout

```
.claude-plugin/marketplace.json      # Claude Code marketplace (lists plugins)
.agents/plugins/marketplace.json     # Codex marketplace (lists plugins)
plugins/<name>/                      # one shared tree per plugin
  .claude-plugin/plugin.json         #   Claude manifest
  .codex-plugin/plugin.json          #   Codex manifest
  skills/<name>/SKILL.md             #   one skill, both harnesses
  skills/<name>/scripts/             #   shared scripts
```
