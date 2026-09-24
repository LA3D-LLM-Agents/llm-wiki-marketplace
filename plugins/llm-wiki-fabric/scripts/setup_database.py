"""Provision a dedicated research reader in the existing local Docker database.

No password is sent on a command line or printed. Existing unmanaged roles are
not adopted or changed. Credentials stay outside the repository with mode 0600.
"""

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

from psycopg import sql

from llm_wiki_fabric.catalog import Catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="rare-disease-db")
    parser.add_argument("--catalog", default="resources.yaml")
    parser.add_argument(
        "--credentials", type=Path, default=Path.home() / ".config/llm-wiki-fabric/credentials.json"
    )
    args = parser.parse_args()
    resource = Catalog(args.catalog).get("rare-disease-db")
    role = "fabric_reader"
    path = args.credentials.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = json.loads(path.read_text()) if path.exists() else {}
    profile = existing.get(resource.connection.auth_profile)
    command = [
        "docker",
        "exec",
        "-i",
        args.container,
        "psql",
        "-U",
        "postgres",
        "-d",
        resource.connection.database,
        "-X",
        "-At",
        "-v",
        "ON_ERROR_STOP=1",
    ]

    def run(statement):
        result = subprocess.run(command, input=statement, text=True, capture_output=True)
        if result.returncode:
            # PostgreSQL diagnostics may include password-bearing DDL. Never echo them.
            raise SystemExit(
                "Database provisioning failed; inspect database configuration locally. No credentials printed."
            )
        return result.stdout.strip()

    exists = run("SELECT 1 FROM pg_roles WHERE rolname='fabric_reader';") == "1"
    if exists:
        if not profile or profile.get("user") != role:
            raise SystemExit(
                "fabric_reader already exists without a matching local profile; refusing to modify it."
            )
        print("Existing fabric_reader profile retained. Run the live tests to verify access.")
        return
    password = secrets.token_urlsafe(36)
    tables = sql.SQL(", ").join(sql.Identifier("public", t) for t in resource.allowed_tables)
    statement = sql.SQL("""
        BEGIN;
        CREATE ROLE {role} LOGIN PASSWORD {password}
          NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
        GRANT CONNECT ON DATABASE {database} TO {role};
        GRANT USAGE ON SCHEMA public TO {role};
        GRANT SELECT ON {tables} TO {role};
        ALTER ROLE {role} SET default_transaction_read_only = on;
        ALTER ROLE {role} SET statement_timeout = '10s';
        ALTER ROLE {role} SET search_path = pg_catalog, public;
        COMMIT;
    """).format(
        role=sql.Identifier(role),
        password=sql.Literal(password),
        database=sql.Identifier(resource.connection.database),
        tables=tables,
    )
    # Write the profile first so interruption after role creation does not lose the secret.
    existing[resource.connection.auth_profile] = {"user": role, "password": password}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")
    run(statement.as_string())
    print(f"Provisioned {role} with SELECT on {len(resource.allowed_tables)} research tables.")
    print(f"Credential profile saved with owner-only permissions: {path}")


if __name__ == "__main__":
    main()
