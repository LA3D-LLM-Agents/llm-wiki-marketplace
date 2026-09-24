import argparse
import json
import os
import sys

from .catalog import Catalog, FabricError


def main():
    parser = argparse.ArgumentParser(description="Knowledge fabric discovery and local connectors")
    parser.add_argument("command", choices=["serve", "connectors", "inspect", "graph"])
    parser.add_argument("--catalog", default="resources.yaml")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-host", default="fabric.crc.nd.edu")
    parser.add_argument("--discovery-url", default=os.environ.get("FABRIC_DISCOVERY_URL"))
    parser.add_argument("--policy", default=os.environ.get("FABRIC_CLIENT_POLICY"))
    parser.add_argument("--agent-store", default=os.environ.get("FABRIC_AGENT_STORE", ":memory:"))
    args = parser.parse_args()
    if args.command == "connectors" and args.transport != "stdio":
        parser.error("Direct connectors must remain local (stdio)")
    try:
        if args.policy:
            from .remote_catalog import policy_catalog

            if not args.discovery_url:
                parser.error("--policy requires --discovery-url")
            catalog = policy_catalog(args.policy, args.discovery_url)
        else:
            catalog = Catalog(args.catalog, refresh=args.refresh, offline=args.offline)
        if args.discovery_url and not args.policy:
            from .remote_catalog import remote_catalog

            catalog = remote_catalog(catalog, args.discovery_url)
        if args.command == "inspect":
            print(json.dumps(catalog.find(), indent=2))
        elif args.command == "graph":
            print(catalog.graph.serialize(format="turtle"))
        else:
            from .servers import connector_server, discovery_server

            if args.command == "serve":
                from .agents import AgentRegistry

                server = discovery_server(
                    catalog,
                    http=args.transport == "streamable-http",
                    host=args.host,
                    port=args.port,
                    public_host=args.public_host,
                    agents=AgentRegistry(args.agent_store),
                )
            else:
                server = connector_server(catalog)
            server.run(transport=args.transport)
    except FabricError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
