#!/usr/bin/env bash
# query-graph.sh — run a SPARQL query against the built graph (rdflib).
#
#   ./query-graph.sh <name>          # a named query from sparql/ (e.g. hub-notes)
#   ./query-graph.sh path/to.rq      # a query file
#   ./query-graph.sh - < query.rq    # SPARQL on stdin
#   ./query-graph.sh <q> --graph P   # query a specific graph file
#
# Reuses the venv created by build-graph.sh (run that first). Override with
# LLM_WIKI_KG_VENV.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${LLM_WIKI_KG_VENV:-$HOME/.llm-wiki-kg/venv}"
PY="$VENV/bin/python"

[ -x "$PY" ] || { echo "wiki-kg: venv not found at $VENV; run build-graph.sh first." >&2; exit 1; }

exec "$PY" "$SCRIPT_DIR/query-graph.py" "$@"
