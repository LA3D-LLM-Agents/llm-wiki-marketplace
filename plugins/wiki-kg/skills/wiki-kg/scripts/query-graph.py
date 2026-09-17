#!/usr/bin/env python3
"""Run a SPARQL query against the built wiki graph with rdflib (in-process).

Usage:
  query-graph.py <name|path|-> [--graph PATH]

  <name>  a query name from sparql/ (e.g. hub-notes -> sparql/hub-notes.rq)
  <path>  a path to a .rq file
  -       read SPARQL from stdin

Defaults to querying build/graph-full.ttl (produced by build-graph.sh).
Output is TSV: a header row of the SELECT variables, then one row per result.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_query(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    p = Path(arg)
    if p.exists():
        return p.read_text()
    named = HERE / "sparql" / (arg if arg.endswith(".rq") else arg + ".rq")
    if named.exists():
        return named.read_text()
    sys.exit(f"query not found: {arg!r} (looked for {named})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Query the wiki graph (rdflib).")
    ap.add_argument("query", help="query name from sparql/, a .rq path, or - for stdin")
    ap.add_argument(
        "--graph",
        default=str(HERE / "build" / "graph-full.ttl"),
        help="graph file to query (default: build/graph-full.ttl)",
    )
    args = ap.parse_args()

    try:
        from rdflib import Graph
    except ImportError:
        sys.exit("rdflib not installed; run build-graph.sh first to create the venv.")

    gpath = Path(args.graph)
    if not gpath.exists():
        sys.exit(f"graph not found: {gpath}. Run build-graph.sh first.")

    g = Graph()
    g.parse(str(gpath), format="turtle")

    res = g.query(load_query(args.query))
    if getattr(res, "vars", None):
        print("\t".join(str(v) for v in res.vars))
    for row in res:
        print("\t".join("" if v is None else str(v) for v in row))


if __name__ == "__main__":
    main()
