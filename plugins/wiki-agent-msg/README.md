# wiki-agent-msg

Cross-harness live messaging between coding-agent sessions on the **same
machine**: list every running Claude Code and Codex session, and send a message
to one. No durable substrate and no watcher, it targets sessions that are
running (or, for Codex, resumable), and the message just appears in the
recipient (a pop-up for a live Claude session, a queued turn for Codex).

The agent-facing usage is in `skills/wiki-agent-msg/SKILL.md`; this file is the
human reference.

## Commands

```bash
scripts/agents-list.sh            # human table of all agents
scripts/agents-list.sh --tsv      # machine form: PLATFORM ID NAME CWD STATUS
scripts/agents-send.sh <target> "<message>"
```

`<target>` matches an agent by exact id, exact name, or a substring of its name
or cwd. Ambiguous matches print the candidates so you can narrow it.

## The cross-harness send matrix (all four verified)

| From → To | Mechanism |
|---|---|
| Claude → Claude | `SendMessage` tool (name + `[ref]`) |
| Codex → Codex | `codex queue --thread <uuid>` |
| Claude → Codex | `codex queue` from the Claude session |
| Codex → Claude | spawn `claude -p` relay that calls `SendMessage` |

`agents-send.sh` dispatches by the **target** platform only:

- **codex target** → `codex queue --thread <uuid> --message "<msg>"`.
- **claude target** → a throwaway `claude -p` relay that calls `ListAgents`
  then `SendMessage`. A bash script cannot invoke the in-session `SendMessage`
  tool directly (it is an agent tool, not a CLI), even inside Claude, so Claude
  targets always go through the relay. If you *are* a Claude agent, you can skip
  the relay and call `SendMessage` yourself.

## Requirements

`bash`, `python3`, and the `claude` and/or `codex` CLIs on PATH. A harness with
no CLI is omitted from the list rather than erroring, so a Claude-only or
Codex-only machine still works for the harness it has. Cross-platform (macOS +
Linux): the Codex session scan uses `find -mtime` plus a Python mtime sort, no
`stat -f`/`stat -c` dependency.

## Verified boundaries and gotchas

- **The `[ref]` is required and unstable.** `SendMessage` needs the target's
  `[ref]` even when the name is unique, and it changes per session instance, so
  the relay lists and sends in one shot; a ref is never cached.
- **The relay leg is one-way.** A message arrives from the relay's ephemeral
  socket, which is dead (`ENOENT`) by the time the recipient reads it. Fine for
  notifications; a request/reply must carry an explicit reply-to (a persistent
  session name) in the message body.
- **Delivery semantics differ.** A Claude target must be running to receive; a
  Codex `queue` message is written to the persisted thread and delivered on
  resume.
- **`codex queue` writes `~/.codex/state_5.sqlite`** (grant write access in a
  sandbox), and **`claude -p --allowedTools <tools...>` is variadic**, so the
  relay pipes the prompt via stdin rather than passing it positionally.

## Scope

Same machine only. Cross-machine federation (owner/repo addressing, offline
delivery) is a separate layer, out of scope here.

## Source

The scripts are maintained at `scripts/agent-msg/` in
[`chrissweet/llm-wiki-vision`](https://github.com/chrissweet/llm-wiki-vision)
and copied here for publication.
