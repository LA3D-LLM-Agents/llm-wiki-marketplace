#!/usr/bin/env python3
"""build-graph.py — Build the wiki knowledge graph from frontmatter + body links.

Single Python pipeline using rdflib + pyshacl. Replaces the Apache Jena
CLI pipeline (riot / arq / shacl) with in-process Python so tool wrappers
for agents can stay in the same language.

Pipeline:
  1. Fetch / refresh spec from https://la3d.github.io/llm-wiki-colab/ into
     scripts/kg/.cache/ (ontology.ttl, shapes.ttl, context.jsonld).
  2. Run scripts/kg/wiki-to-jsonld.py to extract frontmatter + body links
     into JSON-LD.
  3. Parse JSON-LD with rdflib -> graph.ttl.
  4. Materialise inverse edges, hubs, area inheritance via SPARQL
     CONSTRUCT (rdflib in-process).
  5. SHACL validate against the fetched shapes via pyshacl.

Outputs land in scripts/kg/build/:
  graph.jsonld           raw extraction
  graph.ttl              base Turtle (with RDF-star weights appended)
  graph-weights.ttl      RDF-star weighted mentions (extractor emits)
  graph-full.ttl         base + materialised triples
  validation-report.ttl  SHACL conformance report

Derived from prior work in LA3D/llm-wiki-colab (MIT). The earlier
implementation used Jena CLI tools; this version is the same pipeline
in Python so the in-process API is available to downstream agent tools.
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
CACHE_DIR = SCRIPT_DIR / ".cache"
BUILD_DIR = SCRIPT_DIR / "build"

SPEC_BASE_URL = os.environ.get(
    "SPEC_BASE_URL", "https://la3d.github.io/llm-wiki-colab"
)
SPEC_CACHE_DAYS = int(os.environ.get("SPEC_CACHE_DAYS", "7"))

ONT_PREFIX = "PREFIX llm-wiki-colab: <https://la3d.github.io/llm-wiki-colab/ontology#>\n"

# CONSTRUCT queries (same shape as the LA3D-derived Jena pipeline).

QUERY_AREA_INHERIT = ONT_PREFIX + """
CONSTRUCT { ?note llm-wiki-colab:area ?area }
WHERE {
    ?note llm-wiki-colab:up+ ?ancestor .
    ?ancestor llm-wiki-colab:area ?area .
    FILTER NOT EXISTS { ?note llm-wiki-colab:area ?area }
}
"""

INVERSE_PAIRS = [
    ("supports", "supportedBy"),
    ("criticizes", "criticizedBy"),
    ("concept", "conceptOf"),
    ("partOf", "hasPart"),
    ("dependsOn", "prerequisiteOf"),
    ("defines", "definedBy"),
    ("resolvedBy", "resolves"),
    ("incorporatedInto", "incorporates"),
    ("outOfScopeFor", "excludes"),
    ("precedes", "precededBy"),
    ("feedsInto", "informedBy"),
]


def query_inverse(fwd, inv):
    return ONT_PREFIX + (
        f"\nCONSTRUCT {{ ?t llm-wiki-colab:{inv} ?s }}\n"
        f"WHERE {{ ?s llm-wiki-colab:{fwd} ?t }}\n"
    )


QUERY_HUBS = ONT_PREFIX + """
CONSTRUCT { ?note llm-wiki-colab:isHub true }
WHERE {
    { SELECT ?note (COUNT(?src) AS ?inbound) WHERE {
        ?src ?pred ?note .
        FILTER(?pred IN (llm-wiki-colab:up, llm-wiki-colab:area,
                         llm-wiki-colab:concept, llm-wiki-colab:source,
                         llm-wiki-colab:extends, llm-wiki-colab:supports,
                         llm-wiki-colab:criticizes, llm-wiki-colab:related))
    } GROUP BY ?note }
    FILTER(?inbound >= 10)
}
"""


def import_deps():
    """Import rdflib + pyshacl with a clear error if missing."""
    try:
        from rdflib import Graph
        import pyshacl
        return Graph, pyshacl
    except ImportError as e:
        print(f"ERROR: missing Python dependency: {e}", file=sys.stderr)
        print("Install with: pip install rdflib pyshacl", file=sys.stderr)
        sys.exit(1)


def fetch_spec(name, refresh=False):
    """Fetch a spec file from the published URL into the cache."""
    url = f"{SPEC_BASE_URL}/{name}"
    cached = CACHE_DIR / name

    should_fetch = refresh or not cached.exists()
    if not should_fetch and cached.exists():
        age_days = (time.time() - cached.stat().st_mtime) / 86400
        if age_days > SPEC_CACHE_DAYS:
            should_fetch = True

    if should_fetch:
        print(f"  Fetching {url}", file=sys.stderr)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            tmp = cached.with_name(cached.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(cached)
        except Exception as e:
            if cached.exists():
                print(
                    f"  WARN: fetch failed ({e}); using cached copy",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  ERROR: fetch failed for {name} and no cache: {e}",
                    file=sys.stderr,
                )
                sys.exit(1)
    else:
        print(f"  Using cached {cached}", file=sys.stderr)

    return cached


def default_wiki():
    """Find a sensible default wiki dir under repo root."""
    wiki_dir = REPO_ROOT / "wiki"
    if wiki_dir.exists():
        for d in sorted(wiki_dir.glob("*.wiki")):
            if d.is_dir():
                return d
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Build the wiki knowledge graph (rdflib + pyshacl)"
    )
    parser.add_argument(
        "--wiki", type=Path, default=None,
        help="Wiki directory (default: <REPO_ROOT>/wiki/*.wiki/)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print extractor stats to stderr",
    )
    parser.add_argument(
        "--refresh-spec", action="store_true",
        help="Re-fetch cached spec files from the published URL",
    )
    parser.add_argument(
        "--skip-materialize", action="store_true",
        help="Skip CONSTRUCT materialisation step",
    )
    parser.add_argument(
        "--skip-validate", action="store_true",
        help="Skip SHACL validation step",
    )
    args = parser.parse_args()

    wiki_dir = args.wiki or default_wiki()
    if wiki_dir is None:
        print(
            "ERROR: no wiki directory found. Pass --wiki=PATH.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not wiki_dir.is_dir():
        print(f"ERROR: not a directory: {wiki_dir}", file=sys.stderr)
        sys.exit(1)

    Graph, pyshacl = import_deps()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    print("=== scripts/kg/build-graph.py ===", file=sys.stderr)
    print(f"wiki:        {wiki_dir}", file=sys.stderr)
    print(f"spec source: {SPEC_BASE_URL}", file=sys.stderr)
    print(f"cache:       {CACHE_DIR}", file=sys.stderr)
    print(f"build:       {BUILD_DIR}", file=sys.stderr)
    print("", file=sys.stderr)

    # --- Step 1: fetch / refresh spec ---
    print(f"Step 1: Fetching / refreshing spec from {SPEC_BASE_URL} ...", file=sys.stderr)
    ontology_path = fetch_spec("ontology.ttl", refresh=args.refresh_spec)
    shapes_path = fetch_spec("shapes.ttl", refresh=args.refresh_spec)
    context_path = fetch_spec("context.jsonld", refresh=args.refresh_spec)
    print("", file=sys.stderr)

    # --- Step 2: extract JSON-LD ---
    print("Step 2: Extracting frontmatter + body links -> JSON-LD ...", file=sys.stderr)
    jsonld_out = BUILD_DIR / "graph.jsonld"
    extractor = SCRIPT_DIR / "wiki-to-jsonld.py"
    cmd = [
        sys.executable, str(extractor),
        "--wiki", str(wiki_dir),
        "--context", str(context_path),
        "--ontology", str(ontology_path),
        "--output", str(jsonld_out),
    ]
    if args.stats:
        cmd.append("--stats")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(
            f"ERROR: extractor exited {result.returncode}", file=sys.stderr
        )
        sys.exit(result.returncode)
    print("", file=sys.stderr)

    # --- Step 3: JSON-LD -> Turtle (via rdflib) ---
    turtle_out = BUILD_DIR / "graph.ttl"
    weights_out = BUILD_DIR / "graph-weights.ttl"

    print("Step 3: Converting JSON-LD -> Turtle via rdflib ...", file=sys.stderr)
    g_base = Graph()
    g_base.parse(str(jsonld_out), format="json-ld")
    base_triples = len(g_base)

    # Write base Turtle as plain Turtle (no RDF-star). Weights live in
    # graph-weights.ttl as a separate file. rdflib's default Turtle parser
    # does not accept the RDF-star "<< s p o >>" syntax, so merging would
    # make graph.ttl unparseable for downstream rdflib consumers. Tools
    # that want weight semantics load graph-weights.ttl alongside.
    g_base.serialize(destination=str(turtle_out), format="turtle")
    if weights_out.exists():
        print(
            f"  RDF-star weighted mentions in {weights_out.name} "
            "(kept separate; rdflib turtle parser does not accept << s p o >>)",
            file=sys.stderr,
        )

    print(f"  Base triples in graph.ttl: {base_triples}", file=sys.stderr)
    print("", file=sys.stderr)

    # --- Step 4: materialise inverses, hubs, area inheritance ---
    full_out = BUILD_DIR / "graph-full.ttl"

    if args.skip_materialize:
        full_out.write_bytes(turtle_out.read_bytes())
        print("Step 4: skipped (--skip-materialize)", file=sys.stderr)
        print("", file=sys.stderr)
    else:
        print("Step 4: Materialising inverses, hubs, area inheritance ...", file=sys.stderr)
        # Use a fresh in-memory graph for materialisation; we run CONSTRUCT
        # queries against the base graph and accumulate new triples.
        g_full = Graph()
        for triple in g_base:
            g_full.add(triple)

        added = 0
        for triple in g_full.query(QUERY_AREA_INHERIT):
            g_full.add(triple)
            added += 1
        for fwd, inv in INVERSE_PAIRS:
            for triple in g_full.query(query_inverse(fwd, inv)):
                g_full.add(triple)
                added += 1
        for triple in g_full.query(QUERY_HUBS):
            g_full.add(triple)
            added += 1

        print(f"  Materialised {added} triples", file=sys.stderr)
        # Write graph-full.ttl as plain Turtle. RDF-star weights stay in
        # graph-weights.ttl per the rationale in Step 3 above.
        g_full.serialize(destination=str(full_out), format="turtle")

        print(f"  Full graph triples in graph-full.ttl: {len(g_full)}", file=sys.stderr)
        print("", file=sys.stderr)

    # --- Step 5: SHACL validate ---
    report_out = BUILD_DIR / "validation-report.ttl"

    if args.skip_validate:
        print("Step 5: skipped (--skip-validate)", file=sys.stderr)
    else:
        print(
            f"Step 5: SHACL validating graph-full.ttl against {shapes_path} ...",
            file=sys.stderr,
        )
        # Use the in-memory g_full (without RDF-star weights block) so the
        # parser does not have to handle RDF-star concerns.
        if args.skip_materialize:
            data_graph = Graph()
            data_graph.parse(str(turtle_out), format="turtle")
        else:
            data_graph = g_full

        shapes_graph = Graph()
        shapes_graph.parse(str(shapes_path), format="turtle")

        try:
            conforms, _report_graph, report_text = pyshacl.validate(
                data_graph,
                shacl_graph=shapes_graph,
                inference="none",
                serialize_report_graph="turtle",
            )
            # report_text is a Turtle string; write it as the report
            if isinstance(report_text, bytes):
                report_out.write_bytes(report_text)
            else:
                report_out.write_text(report_text)

            if conforms:
                print("  Result: CONFORMS", file=sys.stderr)
            else:
                rep = report_text.decode() if isinstance(report_text, bytes) else report_text
                violations = rep.count("sh:Violation")
                warnings = rep.count("sh:Warning")
                print(
                    f"  Result: {violations} violations, {warnings} warnings",
                    file=sys.stderr,
                )
                print(f"  Report: {report_out}", file=sys.stderr)
        except Exception as e:
            # Do not abort the build on validation failure; capture and surface.
            print(f"  WARN: SHACL validation raised: {e}", file=sys.stderr)
            report_out.write_text(f"# pyshacl raised an exception\n# {e}\n")
        print("", file=sys.stderr)

    # --- Summary ---
    print("=== Build complete ===", file=sys.stderr)
    for f in [jsonld_out, turtle_out, weights_out, full_out, report_out]:
        if f.exists():
            print(f"  {f.name:30s} {f.stat().st_size:>10d} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
