#!/usr/bin/env python3
"""wiki-to-jsonld.py — Extract wiki frontmatter + body links to JSON-LD.

Walks a wiki directory, parses YAML frontmatter from .md files (including
HTML-comment-wrapped frontmatter for GitHub-wiki compatibility), extracts
body cross-references and Variant 1 inline typed-edge annotations, mints
URIs, applies the supplied JSON-LD context, and emits a single JSON-LD
document with an @graph array.

Two link forms recognised in bodies:
  - Wikilinks:        [[Note Title]] or [[Note Title|display]]
  - GitHub-wiki:      [Display Text](Page-Name)

Variant 1 inline typed-edge annotations (visible, GitHub-wiki-friendly):
  [Content](Target) ([*predicate*](Edge-Types#predicate))

HTML-comment fallback annotations:
  [Content](Target)<!-- rel="predicate" -->

Usage:
    python3 wiki-to-jsonld.py --wiki PATH \\
        --context context.jsonld --ontology ontology.ttl \\
        [--output PATH] [--stats]

The --context and --ontology arguments are required for portable use; the
template's scripts/kg/build-graph.sh fetches both from the published LA3D
URL (https://la3d.github.io/llm-wiki-colab/) and passes the cache paths.

Pipe to `riot` for Turtle:
    python3 wiki-to-jsonld.py --wiki path/to/wiki ... \\
      | riot --syntax=jsonld --output=turtle > graph.ttl

----------------------------------------------------------------------
Derived from https://github.com/LA3D/llm-wiki-colab/blob/main/wiki-to-
jsonld.py (MIT). Adapted for this template by adding --context and
--ontology CLI flags and deferring TYPE_MAP construction until after
arg parse, so the spec files can live in a runtime cache directory
rather than as siblings of this script. Original logic and helpers
are preserved verbatim.
----------------------------------------------------------------------
"""

import json
import os
import re
import sys
import urllib.parse
import yaml
from pathlib import Path

# --- Configuration ---

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = None  # stdout by default

# Directories to skip entirely
DEFAULT_SKIP_DIRS = {
    '.obsidian', '.git', '.trash', '.claude',
    'node_modules', 'scripts', 'Templates',
}

# Edge fields whose string values become URI references
EDGE_FIELDS = {
    'up', 'area', 'concept', 'source', 'extends',
    'supports', 'criticizes', 'implementation', 'related',
    'author', 'affiliation', 'collaborator',
    'partOf', 'dependsOn', 'defines', 'resolvedBy',
    'incorporatedInto', 'outOfScopeFor',
    'precedes', 'feedsInto',
}

# Vault ontology namespace
LLM_WIKI_NS = "https://la3d.github.io/llm-wiki-colab/ontology#"

# Populated in main() from the --ontology arg
TYPE_MAP = {}

# --- Link extraction regexes ---

WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')

GITHUB_LINK_RE = re.compile(r'\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)')

EDGE_TYPES_PAGE = 'Edge-Types'

# Variant 1 visible annotation: content link + predicate link to
# Edge-Types#<predicate>. Captures content target (group 2) and predicate
# name (group 4).
PAIR_LINK_RE = re.compile(
    r'\[([^\]]+)\]'                                         # content link text
    r'\((?!https?://|mailto:|#)([^)#]+)\)'                  # content target
    r'[\s(]*'                                               # whitespace / open paren
    r'\[([^\]]+)\]'                                         # predicate link text
    r'\(' + re.escape(EDGE_TYPES_PAGE) + r'#(\w+)\)'        # predicate target
)

# HTML-comment-style attribute block following a link.
ATTR_LINK_RE = re.compile(
    r'\[([^\]]+)\]\((?!https?://|mailto:|#)([^)]+)\)\s*<!--(.*?)-->',
    re.DOTALL
)

ATTR_KV_RE = re.compile(
    r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))'
)


def parse_attr_string(attr_str):
    """Parse a `rel="supports" weight="3"` style attribute string."""
    attrs = {}
    for m in ATTR_KV_RE.finditer(attr_str):
        key = m.group(1)
        value = m.group(2) if m.group(2) is not None else m.group(3)
        attrs[key] = value
    return attrs


def load_type_map(ontology_path):
    """Derive a TYPE_MAP from ontology.ttl skos:notation values.

    Returns mapping from notation string (frontmatter `type:` value) to a
    compact IRI like `llm-wiki-colab:ClassName`. Empty dict if the file is
    missing or unparseable.
    """
    type_map = {}
    if not Path(ontology_path).exists():
        return type_map

    content = Path(ontology_path).read_text(encoding='utf-8')

    current_class = None
    for line in content.split('\n'):
        line = line.strip()
        m = re.match(r'^(llm-wiki-colab:\w+)\s+a\s+rdfs:Class', line)
        if m:
            current_class = m.group(1)
        m = re.match(r'skos:notation\s+"([^"]+)"', line)
        if m and current_class:
            type_map[m.group(1)] = current_class
        if line == '' or (line and not line.startswith(('skos:', 'rdfs:', 'owl:'))
                                                       and 'a rdfs:Class' not in line
                          and not line.endswith(('.',  ';'))):
            if line == '':
                current_class = None

    return type_map


# --- Helpers ---


def title_to_slug(title):
    """Convert a note/page title to a URI-safe slug."""
    parts = title.strip().split('/')
    slugged = []
    for p in parts:
        p = p.replace(' ', '-')
        p = urllib.parse.quote(p, safe='-._~')
        slugged.append(p)
    return '/'.join(slugged)


def strip_wikilink(s):
    """Strip [[ ]] and handle [[Note|display]], [[Note#fragment]], .md."""
    s = s.strip()
    m = WIKILINK_RE.search(s)
    if m:
        inner = m.group(1)
        if '|' in inner:
            inner = inner.split('|')[0]
        if '#' in inner:
            inner = inner.split('#')[0]
        if inner.endswith('.md'):
            inner = inner[:-3]
        return inner.strip()
    return s


def strip_github_link(s):
    """Extract page name from [Display](Page-Name); strips .md, #fragment."""
    s = s.strip()
    m = GITHUB_LINK_RE.search(s)
    if m:
        page = m.group(2)
        if '#' in page:
            page = page.split('#')[0]
        if page.endswith('.md'):
            page = page[:-3]
        return page.strip()
    return s


def extract_link_target(s):
    """Extract a link target from wikilink or GitHub-wiki link format."""
    if '[[' in s:
        return strip_wikilink(s)
    m = GITHUB_LINK_RE.search(s)
    if m:
        return strip_github_link(s)
    return s


def value_to_ref(v, title_uri_map=None):
    """Convert a link string to a JSON-LD @id reference."""
    title = extract_link_target(v)

    if title_uri_map:
        bare_title = title.split('/')[-1] if '/' in title else title
        if bare_title in title_uri_map:
            return {'@id': title_uri_map[bare_title]}
        if title in title_uri_map:
            return {'@id': title_uri_map[title]}

    return {'@id': title_to_slug(title)}


def process_edge_value(v, title_uri_map=None):
    """Process a frontmatter edge field value (string or list) to @id refs."""
    if v is None or v == '':
        return None
    if isinstance(v, str):
        if '[[' in v or GITHUB_LINK_RE.search(v):
            return value_to_ref(v, title_uri_map)
        return None
    if isinstance(v, list):
        result = []
        for item in v:
            if isinstance(item, str) and ('[[' in item or GITHUB_LINK_RE.search(item)):
                result.append(value_to_ref(item, title_uri_map))
        return result if result else None
    return v


def extract_frontmatter(filepath):
    """Extract YAML frontmatter from a markdown file.

    Supports standard `--- ... ---` and HTML-comment-wrapped
    `<!-- \\n--- ... ---\\n -->` forms.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, OSError):
        return None

    comment_re = re.compile(
        r'^<!--\s*\n---\n(.*?\n)---\s*\n-->', re.DOTALL
    )
    m = comment_re.match(content)
    if m:
        try:
            return yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            return None

    if not content.startswith('---\n'):
        return None
    end = content.find('\n---', 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(content[4:end])
    except yaml.YAMLError:
        return None


def strip_body(filepath):
    """Read a markdown file and return the body with frontmatter stripped."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, OSError):
        return ''

    comment_re = re.compile(
        r'^<!--\s*\n---\n.*?\n---\s*\n-->\s*\n?', re.DOTALL
    )
    content = comment_re.sub('', content)
    if content.startswith('---\n'):
        end = content.find('\n---', 3)
        if end != -1:
            content = content[end + 4:]

    return content


def extract_body_links(filepath):
    """Extract body cross-references as page -> occurrence-count.

    Predicate-carrier links (targeting Edge-Types#<predicate>) are excluded
    so the Edge-Types page does not become a spurious hub.
    """
    content = strip_body(filepath)
    if not content:
        return {}

    counts = {}

    for m in WIKILINK_RE.finditer(content):
        inner = m.group(1)
        if '|' in inner:
            inner = inner.split('|')[0]
        if inner.startswith(EDGE_TYPES_PAGE + '#'):
            continue
        if '#' in inner:
            inner = inner.split('#')[0]
        if inner.endswith('.md'):
            inner = inner[:-3]
        inner = inner.strip()
        if inner:
            counts[inner] = counts.get(inner, 0) + 1

    for m in GITHUB_LINK_RE.finditer(content):
        full_target = m.group(2)
        if full_target.startswith(EDGE_TYPES_PAGE + '#'):
            continue
        page = full_target
        if '#' in page:
            page = page.split('#')[0]
        if page.endswith('.md'):
            page = page[:-3]
        page = page.strip()
        if page:
            counts[page] = counts.get(page, 0) + 1

    return counts


def resolve_link(link, title_uri_map):
    """Resolve a link target string to a URI slug using the title map."""
    if title_uri_map:
        bare = link.split('/')[-1] if '/' in link else link
        if bare in title_uri_map:
            return title_uri_map[bare]
        if link in title_uri_map:
            return title_uri_map[link]
    return title_to_slug(link)


def extract_typed_body_links(filepath, title_uri_map=None):
    """Extract Variant 1 + HTML-comment typed-edge annotations from body.

    Returns dict: predicate -> deduplicated list of resolved target URIs.
    Predicates not in EDGE_FIELDS are silently dropped.
    """
    content = strip_body(filepath)
    if not content:
        return {}
    typed = {}

    for m in PAIR_LINK_RE.finditer(content):
        page = m.group(2).strip()
        if '#' in page:
            page = page.split('#')[0]
        if page.endswith('.md'):
            page = page[:-3]
        page = page.strip()
        if not page or page == EDGE_TYPES_PAGE:
            continue
        rel = m.group(4)
        if rel not in EDGE_FIELDS:
            continue
        target_uri = resolve_link(page, title_uri_map)
        bucket = typed.setdefault(rel, [])
        if target_uri not in bucket:
            bucket.append(target_uri)

    for m in ATTR_LINK_RE.finditer(content):
        page = m.group(2).strip()
        if '#' in page:
            page = page.split('#')[0]
        if page.endswith('.md'):
            page = page[:-3]
        page = page.strip()
        if not page:
            continue
        attrs = parse_attr_string(m.group(3))
        rel = attrs.get('rel')
        if not rel or rel not in EDGE_FIELDS:
            continue
        target_uri = resolve_link(page, title_uri_map)
        bucket = typed.setdefault(rel, [])
        if target_uri not in bucket:
            bucket.append(target_uri)

    return typed


def note_to_jsonld(filepath, wiki_root, title_uri_map=None, disambiguate_parents=None):
    """Convert a single page's frontmatter + body links to a JSON-LD node."""
    fm = extract_frontmatter(filepath)

    doc = dict(fm) if fm and isinstance(fm, dict) else {}

    if 'noteType' in doc and 'type' not in doc:
        doc['type'] = doc.pop('noteType')
    elif 'noteType' in doc:
        del doc['noteType']

    for legacy in ('relatedConcepts', 'relatedLiterature', 'implementations'):
        doc.pop(legacy, None)

    basename = filepath.stem
    slug = title_to_slug(basename)

    if disambiguate_parents:
        parent = filepath.parent.name
        if parent in disambiguate_parents:
            slug = f'{title_to_slug(parent)}/{slug}'

    doc['@id'] = slug

    if 'type' in doc:
        t = doc['type']
        doc['type'] = TYPE_MAP.get(t, f'llm-wiki-colab:{t}')
    else:
        doc['type'] = 'llm-wiki-colab:UntypedNote'

    for field in EDGE_FIELDS:
        if field in doc:
            resolved = process_edge_value(doc[field], title_uri_map)
            if resolved is None:
                del doc[field]
            else:
                doc[field] = resolved

    typed_body = extract_typed_body_links(filepath, title_uri_map)
    for predicate, targets in typed_body.items():
        existing = doc.get(predicate)
        existing_list = []
        existing_uris = set()
        if existing:
            if isinstance(existing, dict):
                existing_list = [existing]
            elif isinstance(existing, list):
                existing_list = list(existing)
            for item in existing_list:
                if isinstance(item, dict) and '@id' in item:
                    existing_uris.add(item['@id'])
        for target_uri in targets:
            if target_uri not in existing_uris:
                existing_list.append({'@id': target_uri})
                existing_uris.add(target_uri)
        if existing_list:
            doc[predicate] = existing_list if len(existing_list) > 1 else existing_list[0]

    link_counts = extract_body_links(filepath)
    weighted_mentions = []
    if link_counts:
        mentions = []
        for link, count in link_counts.items():
            target_uri = resolve_link(link, title_uri_map)
            mentions.append({'@id': target_uri})
            weighted_mentions.append((slug, target_uri, count))
        doc['mentions'] = mentions

    doc['title'] = basename

    for k, v in doc.items():
        if hasattr(v, 'isoformat'):
            doc[k] = v.isoformat()

    for field in ('cssclasses', 'aliases', 'publish'):
        doc.pop(field, None)

    return doc, weighted_mentions


def should_skip(dirpath, root, skip_dirs):
    rel = os.path.relpath(dirpath, root)
    parts = Path(rel).parts
    return any(p in skip_dirs for p in parts)


def build_title_uri_map(root, skip_dirs, disambiguate_parents=None):
    title_map = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        if should_skip(dirpath, root, skip_dirs):
            continue
        for fname in sorted(filenames):
            if not fname.endswith('.md'):
                continue
            fpath = Path(dirpath) / fname
            basename = fpath.stem
            slug = title_to_slug(basename)
            if disambiguate_parents:
                parent = fpath.parent.name
                if parent in disambiguate_parents:
                    slug = f'{title_to_slug(parent)}/{slug}'
            title_map[basename] = slug
    return title_map


def build_graph(root, stats, skip_dirs, disambiguate_parents=None):
    """Walk wiki, build @graph array. Two-pass: title map, then nodes."""
    title_uri_map = build_title_uri_map(root, skip_dirs, disambiguate_parents)

    graph = []
    all_weighted_mentions = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        if should_skip(dirpath, root, skip_dirs):
            continue

        for fname in sorted(filenames):
            if not fname.endswith('.md'):
                continue

            fpath = Path(dirpath) / fname
            stats['total'] += 1

            result = note_to_jsonld(fpath, root, title_uri_map, disambiguate_parents)
            if result is None:
                stats['skipped'] += 1
                continue

            node, weighted_mentions = result
            graph.append(node)
            all_weighted_mentions.extend(weighted_mentions)
            stats['processed'] += 1

            t = node.get('type', 'unknown')
            stats['types'][t] = stats['types'].get(t, 0) + 1

    return graph, all_weighted_mentions


def emit_weighted_mentions_ttl(weighted_mentions, base_uri="https://la3d.github.io/llm-wiki-colab/page/"):
    """Emit RDF-star Turtle for weighted mention edges."""
    lines = [
        '@prefix llm-wiki-colab: <https://la3d.github.io/llm-wiki-colab/ontology#> .',
        f'@base <{base_uri}> .',
        '',
    ]

    for source, target, count in weighted_mentions:
        s = f'<{source}>'
        t = f'<{target}>'
        lines.append(f'{s} llm-wiki-colab:mentions {t} .')
        lines.append(f'<< {s} llm-wiki-colab:mentions {t} >> llm-wiki-colab:weight {count} .')

    lines.append('')
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Extract wiki frontmatter + body links to JSON-LD',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--wiki', type=Path,
                       help='Wiki root directory (flat structure)')
    group.add_argument('--vault', type=Path,
                       help='Vault root directory (Obsidian, with subfolders)')
    parser.add_argument('--context', type=Path, required=True,
                        help='Path to context.jsonld (the JSON-LD context to embed)')
    parser.add_argument('--ontology', type=Path, required=True,
                        help='Path to ontology.ttl (used to build the type -> class map)')
    parser.add_argument('--output', type=Path, default=None,
                        help='Output file (default: stdout)')
    parser.add_argument('--stats', action='store_true',
                        help='Print stats to stderr')
    parser.add_argument('--skip-dir', action='append', default=[],
                        help='Additional directories to skip (repeatable)')
    args = parser.parse_args()

    if args.wiki:
        root = args.wiki.resolve()
        disambiguate_parents = None
        skip_dirs = {'.git'} | set(args.skip_dir)
    else:
        root = args.vault.resolve()
        disambiguate_parents = {
            'Literature', 'Theory', 'Implementation', 'External Resources',
            'Methods', 'Memory Architecture', 'Findings',
        }
        skip_dirs = DEFAULT_SKIP_DIRS | {'04 - Archive'} | set(args.skip_dir)

    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        sys.exit(1)

    # Resolve TYPE_MAP from the supplied ontology
    global TYPE_MAP
    TYPE_MAP = load_type_map(args.ontology)
    if not TYPE_MAP and args.stats:
        print(f"WARN: TYPE_MAP empty (parsed 0 classes from {args.ontology})", file=sys.stderr)

    with open(args.context) as f:
        ctx = json.load(f)['@context']

    stats = {'total': 0, 'processed': 0, 'skipped': 0, 'types': {}}
    graph, weighted_mentions = build_graph(root, stats, skip_dirs, disambiguate_parents)

    doc = {
        '@context': ctx,
        '@graph': graph,
    }

    output_json = json.dumps(doc, indent=2, ensure_ascii=False)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(output_json)
            f.write('\n')
        if args.stats:
            print(f"JSON-LD written to {args.output}", file=sys.stderr)

        if weighted_mentions and args.output.name != 'null':
            weights_path = args.output.with_name(
                args.output.stem + '-weights.ttl'
            )
            ttl = emit_weighted_mentions_ttl(weighted_mentions)
            with open(weights_path, 'w') as f:
                f.write(ttl)
            if args.stats:
                print(f"Weighted mentions written to {weights_path}", file=sys.stderr)
                print(f"Weighted edges: {len(weighted_mentions)}", file=sys.stderr)
    else:
        print(output_json)
        if weighted_mentions and args.stats:
            print(f"\nWeighted edges: {len(weighted_mentions)}", file=sys.stderr)

    if args.stats:
        print(f"\n--- Stats ---", file=sys.stderr)
        print(f"Total .md files: {stats['total']}", file=sys.stderr)
        print(f"Processed: {stats['processed']}", file=sys.stderr)
        print(f"Skipped: {stats['skipped']}", file=sys.stderr)
        print(f"\nTypes:", file=sys.stderr)
        for t, count in sorted(stats['types'].items(), key=lambda x: -x[1]):
            print(f"  {t}: {count}", file=sys.stderr)


if __name__ == '__main__':
    main()
