# llm-wiki-fabric

Version 0.5.0, shared by Codex and Claude Code.

Discovery uses https://fabric.crc.nd.edu/mcp. The local connector calls fabric_catalog through the same MCP endpoint at startup; resource metadata, SQL destination and SSH routing
come from descriptors. No client resource catalog is installed or read.

## Setup

Requires macOS/Linux, bash, Python 3.11+, uv and OpenSSH.

```sh
python3 plugins/llm-wiki-fabric/scripts/setup.py
```

Rerun setup after upgrading, then restart Codex/Claude. Older clients using /catalog.json must upgrade. Replace the old URL in
client-policy.yaml trusted_catalogs with https://fabric.crc.nd.edu/mcp.
Preserve other policy restrictions. Setup installs the bundled runtime into
`~/.local/share/llm-wiki-fabric/venv` and creates `client-policy.yaml` under
`~/.config/llm-wiki-fabric/`, preserving existing policy. Override paths with
`FABRIC_RUNTIME` and `FABRIC_CLIENT_POLICY`. Old resources.yaml files are ignored.

Policy approves the HTTPS catalog, MCP/SSH hosts and tool/table permissions.
Descriptors cannot expand these permissions. A newly introduced host needs a
policy change. New transports need connector support. Changes within supported
transports and policy need only descriptor updates and connector restart.

## Database access

Configure SSH login and a trusted host key for fabric.crc.nd.edu. Each SQL
operation opens a temporary local tunnel and closes it afterward; no fixed port
or launch agent is needed. Keep credentials in
`~/.config/llm-wiki-fabric/credentials.json` with mode 0600 (or FABRIC_CREDENTIALS):

```json
{"rare-disease-db": {"user": "fabric_reader", "password": "YOUR_SECRET"}}
```

Credentials and SSH keys remain local. The administrator grants read-only access
to the approved research tables. Setup does not provision database accounts.

## Use

Ask to use fabric-query. Discover and identify resources, read PAD ontology or
SQL dictionary/live schema, then query directly. Retain revisions and query times;
report errors, stale metadata and unverified identities. Restart connectors after
catalog changes; there is no background refresh or identity verification.

## Maintenance

Runtime provenance is recorded in runtime/UPSTREAM.json (upstream fa5fcb9).
Both manifests use the same release. Validate the plugin and run:

```sh
~/.local/share/llm-wiki-fabric/venv/bin/python plugins/llm-wiki-fabric/scripts/smoke_test.py
```

The smoke test initializes discovery and connectors; it does not query data.

All descriptors, dictionaries and routing are transported through MCP. There is
no HTTP catalog download or fallback; /catalog.json is removed. /healthz is
reserved for operational monitoring and carries no resource descriptors.
