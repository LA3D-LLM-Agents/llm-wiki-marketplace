#!/usr/bin/env bash
# agents-list.sh — unified live-agent list across Claude Code and Codex.
#
# Emits one row per agent, tagged by platform. Default output is a human
# table; pass --tsv for machine consumption (used by agents-send.sh).
#
#   columns: PLATFORM  ID  NAME  CWD  STATUS
#
#   claude rows come from `claude agents --json` (live sessions).
#   codex  rows come from scanning ~/.codex/sessions (recent, deduped by
#          cwd keeping the newest UUID; codex queue delivers on resume so
#          "live vs saved" does not block sending).
set -euo pipefail

CODEX_SESS="${CODEX_HOME:-$HOME/.codex}/sessions"

claude_rows() {
  command -v claude >/dev/null 2>&1 || return 0
  claude agents --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for a in data if isinstance(data, list) else []:
    print("\t".join([
        "claude",
        a.get("sessionId", "") or "",
        (a.get("name") or "-"),
        (a.get("cwd") or "-"),
        (a.get("status") or "-"),
    ]))
'
}

codex_rows() {
  command -v codex >/dev/null 2>&1 || return 0   # no codex CLI -> no codex rows
  [ -d "$CODEX_SESS" ] || return 0
  # Scan sessions from the last 30 days; temp-dir sessions are filtered below.
  # find -mtime is portable (BSD + GNU); mtime sorting and the file-count cap
  # are done in python so there is no dependence on `stat -f` (macOS) vs
  # `stat -c` (Linux).
  find "$CODEX_SESS" -name '*.jsonl' -type f -mtime -30 2>/dev/null | python3 -c '
import json, os, sys

TEMP_PREFIXES = ("/private/var/folders/", "/var/folders/", "/tmp/", "/private/tmp/")

def is_temp(cwd):
    return cwd.startswith(TEMP_PREFIXES) or "/T/tmp." in cwd

paths = [p for p in sys.stdin.read().splitlines() if p]
paths.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0, reverse=True)
paths = paths[:400]             # newest 400, portable (no stat -f/-c)

seen = {}                       # cwd -> session_id (first seen = newest)
for path in paths:
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                if i > 4:        # session_meta is at the top; do not scan whole file
                    break
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                p = r.get("payload") if isinstance(r.get("payload"), dict) else r
                sid = p.get("session_id") or r.get("session_id")
                cwd = p.get("cwd") or r.get("cwd")
                if sid and cwd:
                    if not is_temp(cwd) and cwd not in seen:
                        seen[cwd] = sid
                    break        # found the metadata record for this session
    except Exception:
        continue
for cwd, sid in seen.items():
    name = os.path.basename(cwd.rstrip("/")) or "-"
    print("\t".join(["codex", sid, name, cwd, "saved"]))
'
}

rows() { claude_rows; codex_rows; }

if [ "${1:-}" = "--tsv" ]; then
  rows
else
  { printf 'PLATFORM\tID\tNAME\tCWD\tSTATUS\n'; rows; } | column -t -s "$(printf '\t')"
fi
