---
name: agent-msg
description: List and message coding-agent sessions across Claude Code and Codex on this machine. Use to see which agents are running and to send a message to one.
disable-model-invocation: true
---

You operate the cross-harness messaging tool: two scripts under `${CLAUDE_SKILL_DIR}/scripts/`. It lists sessions from both harnesses and sends a message to one, routed by the target's platform. Same machine only; a message just appears in the recipient (a pop-up for a live Claude session, a queued turn for Codex).

The user's input is: `$ARGUMENTS`

## List agents

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/agents-list.sh"
```

Prints one row per agent, tagged by platform (`claude` / `codex`), with its id, name, cwd, and status. Live Claude sessions come from `claude agents --json`; recent Codex sessions from `~/.codex/sessions`. Add `--tsv` for machine-readable output.

## Send a message

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/agents-send.sh" <target> "<message>"
```

`<target>` matches an agent by exact id, exact name, or a substring of its name or cwd (resolved against the list; ambiguous matches print candidates). Dispatch is by the **target's** platform:

- **Codex target** -> `codex queue` (written to the session's thread; delivered on resume if it is not live).
- **Claude target** -> a throwaway `claude -p` relay that calls `SendMessage`. This leg is **one-way**: the recipient cannot reply to the relay, so for a round trip include an explicit reply-to (a persistent session name) in your message body.

Report the delivery confirmation, or the resolution error, back to the user.

## Notes

- Needs `bash`, `python3`, and the `claude` and/or `codex` CLIs on PATH. A harness with no CLI is omitted from the list rather than erroring.
- If you are a Claude agent messaging another **Claude** session, you can skip the relay and call the `SendMessage` tool directly; the relay is the shell / cross-harness path.
- `codex queue` writes `~/.codex/state_5.sqlite`; allow that write in a sandbox.
- The target's `[ref]` for a Claude send is resolved live by the relay and is not stable across runs.
