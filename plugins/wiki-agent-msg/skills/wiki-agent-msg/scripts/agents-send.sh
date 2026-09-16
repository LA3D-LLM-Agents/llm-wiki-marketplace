#!/usr/bin/env bash
# agents-send.sh <target> "<message>" — send a message to a Claude Code or
# Codex session, dispatched by the TARGET's platform.
#
#   codex target  -> codex queue --thread <uuid> --message "<msg>"
#   claude target -> spawn `claude -p` relay that calls SendMessage with the
#                    target's live [ref] (a bash script cannot call the
#                    in-session SendMessage tool directly, even inside Claude)
#
# <target> matches an agent by exact id, exact name, or a substring of its
# name or cwd, resolved against agents-list.sh --tsv. Ambiguous matches error
# with the candidates so you can be more specific.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

[ $# -ge 2 ] || { echo "usage: agents-send.sh <target-name-or-cwd> \"<message>\"" >&2; exit 1; }
TARGET="$1"; shift
MSG="$*"

LIST="$("$HERE/agents-list.sh" --tsv 2>/dev/null || true)"
[ -n "$LIST" ] || { echo "no agents found (is claude/codex installed and any session open?)" >&2; exit 2; }

MATCHES="$(printf '%s\n' "$LIST" | awk -F'\t' -v q="$TARGET" '
  $2==q || $3==q || index($3,q)>0 || index($4,q)>0 { print }
')"

n=$(printf '%s\n' "$MATCHES" | grep -c . 2>/dev/null || true)
if [ "${n:-0}" -eq 0 ]; then
  echo "no agent matches '$TARGET'. Run: $HERE/agents-list.sh" >&2
  exit 2
fi
if [ "${n:-0}" -gt 1 ]; then
  echo "ambiguous target '$TARGET' — matches:" >&2
  printf '%s\n' "$MATCHES" | awk -F'\t' '{printf "  %-7s %-38s %s (%s)\n",$1,$2,$3,$4}' >&2
  exit 3
fi

IFS=$'\t' read -r PLAT ID NAME CWD STATUS <<<"$MATCHES"

case "$PLAT" in
  codex)
    command -v codex >/dev/null 2>&1 || { echo "codex CLI not found on PATH; cannot message Codex target '$NAME'." >&2; exit 6; }
    echo "-> codex queue to '$NAME'  (thread $ID)" >&2
    if ! codex queue --thread "$ID" --message "$MSG"; then
      echo "codex queue failed. If it reported a read-only SQLite DB, grant write access to ~/.codex/state_5.sqlite and retry." >&2
      exit 5
    fi
    ;;
  claude)
    command -v claude >/dev/null 2>&1 || { echo "claude CLI not found on PATH; cannot message Claude target '$NAME'." >&2; exit 6; }
    echo "-> claude relay to '$NAME'  (session $ID)" >&2
    # A throwaway Claude agent resolves the live [ref] and does the send.
    # Prompt goes via stdin: --allowedTools is variadic and would otherwise
    # swallow a positional prompt as a tool name.
    RELAY_PROMPT="You are a one-shot message relay. Do exactly this and nothing else:
1. Call ListAgents.
2. Find the peer session whose name is exactly \"$NAME\" (its cwd is $CWD). Note its current [ref] token.
3. Call SendMessage with: to = that name followed by a space and its [ref] (e.g. \"$NAME [abc123]\"); message = the text between <<< and >>> below, verbatim.
4. Report the SendMessage result (success and msg_id), then stop.
<<<
$MSG
>>>"
    printf '%s' "$RELAY_PROMPT" | claude -p --allowedTools ListAgents SendMessage
    ;;
  *)
    echo "unknown platform '$PLAT' for target '$TARGET'" >&2
    exit 4
    ;;
esac
