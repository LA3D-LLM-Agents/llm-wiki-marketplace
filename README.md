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

## Install

### Claude Code

```
/plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
/plugin install wiki-agent-msg@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
codex plugin add wiki-agent-msg@llm-wiki-marketplace
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
- **Slash command** — `/<plugin-name>:<skill-name>` on Claude Code (for this
  marketplace's plugin, `/wiki-agent-msg:wiki-agent-msg`).

See each plugin's own README for its usage (e.g.
[`plugins/wiki-agent-msg/README.md`](plugins/wiki-agent-msg/README.md)).

## Update

### Claude Code

```
claude plugin marketplace update llm-wiki-marketplace
claude plugin update wiki-agent-msg@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace upgrade
codex plugin add wiki-agent-msg@llm-wiki-marketplace
```

## Uninstall

### Claude Code

```
/plugin uninstall wiki-agent-msg@llm-wiki-marketplace
/plugin marketplace remove llm-wiki-marketplace
```

### Codex

```
codex plugin remove wiki-agent-msg@llm-wiki-marketplace
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
