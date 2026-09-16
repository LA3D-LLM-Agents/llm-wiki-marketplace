# llm-wiki-marketplace

A marketplace of small, cross-harness plugins for the llm-wiki family, served to
both **Claude Code** and **Codex** from one repository. Each plugin ships a
harness-native copy under `claude/plugins/<name>/` and `codex/plugins/<name>/`,
listed by the two root marketplace manifests.

## Plugins

| Plugin | What it does |
|---|---|
| **agent-msg** | List and message coding-agent sessions across Claude Code and Codex on the same machine. `agents-list` shows every running agent tagged by platform; `agents-send <target> "<msg>"` routes by the target's platform (Codex -> `codex queue`; Claude -> a `claude -p` relay that calls `SendMessage`). |

## Install

### Claude Code

```
/plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
/plugin install agent-msg@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace add LA3D-LLM-Agents/llm-wiki-marketplace
codex plugin add agent-msg@llm-wiki-marketplace
```

Codex requires a restart after a plugin change. Codex updates are keyed on the
plugin's manifest version, not the commit, so a release only reaches Codex users
when that version is bumped.

## Update

### Claude Code

```
claude plugin marketplace update llm-wiki-marketplace
claude plugin update agent-msg@llm-wiki-marketplace
```

### Codex

```
codex plugin marketplace upgrade
codex plugin add agent-msg@llm-wiki-marketplace
```

## Uninstall

### Claude Code

```
/plugin uninstall agent-msg@llm-wiki-marketplace
/plugin marketplace remove llm-wiki-marketplace
```

### Codex

```
codex plugin remove agent-msg@llm-wiki-marketplace
codex plugin marketplace remove llm-wiki-marketplace
```

## Requirements

`bash`, `python3`, and the `claude` and/or `codex` CLIs on PATH. A harness with no
CLI installed is simply omitted from the agent list rather than erroring, so a
Claude-only or Codex-only machine still works for the harness it has.

## Adding a plugin

The marketplace is structured to grow. To add a plugin named `<name>`:

1. Create the two harness trees:
   - `claude/plugins/<name>/.claude-plugin/plugin.json` + `skills/<name>/SKILL.md` (+ `scripts/`, `commands/`, `hooks/` as needed).
   - `codex/plugins/<name>/.codex-plugin/plugin.json` + `skills/<name>/SKILL.md` (+ scripts).
   The two skill copies are identical except for the skill-directory variable
   (`${CLAUDE_SKILL_DIR}` on Claude, `$SKILL_DIRECTORY` on Codex) and Claude's
   optional `disable-model-invocation:` frontmatter.
2. Add one entry to each root manifest:
   - `.claude-plugin/marketplace.json` -> `plugins[]` with `"source": "./claude/plugins/<name>"`.
   - `.agents/plugins/marketplace.json` -> `plugins[]` with `"source": {"source": "local", "path": "./codex/plugins/<name>"}`.
3. Bump the plugin's `version` in both `plugin.json` files on each release (Codex
   installs track the manifest version).

## Layout

```
.claude-plugin/marketplace.json      # Claude Code marketplace (lists plugins)
.agents/plugins/marketplace.json     # Codex marketplace (lists plugins)
claude/plugins/<name>/               # Claude Code copy of each plugin
codex/plugins/<name>/                # Codex copy of each plugin
```
