# llm-wiki-fabric

Discover research resources through a small RDF graph, inspect source semantics,
and query PAD MCP or PostgreSQL directly. One shared skill and MCP configuration
serve Claude Code and Codex, with separate client manifests. Version: 0.2.0.

## Install and setup

Install `llm-wiki-fabric@llm-wiki-marketplace` using the root marketplace README.
Requires macOS/Linux (bash), Python 3.11+ and `uv` on PATH. Python dependencies
and the PostgreSQL driver are included in the locked runtime installation.

Run once, using the installed plugin directory or this marketplace checkout:

```sh
python3 plugins/llm-wiki-fabric/scripts/setup.py
```

The script installs the bundled Python runtime non-editably into
`~/.local/share/llm-wiki-fabric/venv`, and seeds the catalog and descriptors under
`~/.config/llm-wiki-fabric/`. Existing configuration is preserved. It downloads
locked dependencies as necessary; it does not create a database, configure a role,
copy credentials, or execute research queries. Restart the client after setup.
The initial missing-runtime message before setup is expected.

For another location set `FABRIC_RUNTIME` (venv directory) and `FABRIC_CATALOG`
(absolute YAML path) consistently for both setup and the client process. The shared
MCP launch command resolves these environment variables independently of client
cache paths and working directory. Dependency installation never occurs in the
MCP startup handshake. Plugin updates require rerunning setup; compare bundled
catalog/dictionary changes with your preserved operator configuration.

PAD's default seed uses https://pad.crc.nd.edu/.well-known/did.json. Startup can
fetch metadata; validated caches are reused with freshness reporting. Query access
is restricted by the catalog's local tool/table allowlists. Publisher identities
are explicitly unverified. No background refresh or signature verification is provided.

## Database access

The default SQL profile expects an already running `raredisease` PostgreSQL database
on localhost:5432. Change the local catalog for your deployment. Credentials stay in
`~/.config/llm-wiki-fabric/credentials.json`, mode 0600 (or `FABRIC_CREDENTIALS`):

```json
{"rare-disease-readonly": {"user": "fabric_reader", "password": "YOUR_LOCAL_SECRET"}}
```

The database administrator must grant SELECT only on the approved research tables.
For the existing Docker container named `rare-disease-db`, the optional
`scripts/setup_database.py` helper provisions that role using Docker administrator
access. Run it explicitly with the installed runtime Python and `--catalog` pointing
to your configured YAML only when authorized to provision database access.
This helper is not run by installation. The plugin does not ship the database or its data.
To use only PAD, remove the database resource from your local catalog.

## Use

Claude: `/llm-wiki-fabric:fabric-query` followed by the question.
Codex: ask to use `fabric-query`.

> Discover both resources and report descriptor freshness. Read PAD's ontology,
> then find the layout and lane B reagent for physical card 19705. Read the
> database dictionary/schema and count MSL3 publications with and without exclusions.

Discovery tools: `fabric_find`, `fabric_identify`.
Direct tools: `resource_tools`, `resource_call`, `db_schema`, `db_query`.
The workflow retains catalog revisions, reads ontology/schema first, uses bounded
queries, and checks completeness. Disable the earlier personal/development fabric
plugin when switching to this marketplace version to avoid duplicate servers.

## Maintenance

Runtime/catalog snapshot: upstream `chrissweet/llm-wiki-fabric` commit `9f4022d`.
Source is bundled in `runtime/` so installation does not depend on a developer's
checkout or a moving Git branch. Refresh the runtime, lockfile, descriptors and
shared skill from reviewed upstream changes together; bump both client manifests.
Run `claude plugin validate`, the Codex plugin validator, and the smoke check:

```sh
FABRIC_RUNTIME=/path/to/test/venv FABRIC_CATALOG=/path/to/test/resources.yaml \
  /path/to/test/venv/bin/python plugins/llm-wiki-fabric/scripts/smoke_test.py
```

The smoke check initializes both configured MCP subprocesses and checks tools and
discovery; it does not query resource data. First discovery may fetch PAD metadata.
