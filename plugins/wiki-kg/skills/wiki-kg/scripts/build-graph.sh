#!/usr/bin/env bash
# build-graph.sh — venv-bootstrapping entry point for the wiki-kg pipeline.
#
# On first run it creates a Python venv and installs rdflib + pyshacl, then
# runs the in-process pipeline (no Jena, no Fuseki). Subsequent runs reuse the
# venv. All flags pass through to build-graph.py (see --help).
#
#   ./build-graph.sh                 # build against wiki/<repo>.wiki/
#   ./build-graph.sh --wiki=PATH     # build against a specific wiki
#   ./build-graph.sh --stats         # extractor stats
#
# Override the venv location with LLM_WIKI_KG_VENV.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${LLM_WIKI_KG_VENV:-$HOME/.llm-wiki-kg/venv}"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "wiki-kg: first run — creating venv at $VENV and installing rdflib + pyshacl ..." >&2
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV/bin/pip" install --quiet rdflib pyshacl pyyaml >&2
  echo "wiki-kg: venv ready ($VENV)." >&2
fi

exec "$PY" "$SCRIPT_DIR/build-graph.py" "$@"
