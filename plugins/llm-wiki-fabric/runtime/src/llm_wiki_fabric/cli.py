import argparse
import json
import sys

from .catalog import Catalog, FabricError


def main():
    parser = argparse.ArgumentParser(description="Knowledge fabric discovery and local connectors")
    parser.add_argument("command", choices=["serve", "connectors", "inspect", "graph"])
    parser.add_argument("--catalog", default="resources.yaml")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        catalog = Catalog(args.catalog, refresh=args.refresh, offline=args.offline)
        if args.command == "inspect":
            print(json.dumps(catalog.find(), indent=2))
        elif args.command == "graph":
            print(catalog.graph.serialize(format="turtle"))
        else:
            from .servers import connector_server, discovery_server

            factory = discovery_server if args.command == "serve" else connector_server
            factory(catalog).run(transport="stdio")
    except FabricError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
