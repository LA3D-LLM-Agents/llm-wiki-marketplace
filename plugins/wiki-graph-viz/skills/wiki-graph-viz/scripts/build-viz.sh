#!/usr/bin/env bash
# build-viz.sh — render a wiki-kg graph.jsonld as a self-contained interactive
# HTML map. Pure python3 standard library (no venv, no dependencies). All flags
# pass through to build-viz.py (see --help).
#
#   ./build-viz.sh --graph <path>/graph.jsonld --repo owner/repo --out graph.html
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/build-viz.py" "$@"
