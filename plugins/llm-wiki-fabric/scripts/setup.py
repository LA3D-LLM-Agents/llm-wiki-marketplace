"""Install the bundled runtime; preserve existing operator configuration and secrets."""

import os
import shutil
import subprocess
from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1] / "runtime"
    environment = (
        Path(os.environ.get("FABRIC_RUNTIME", "~/.local/share/llm-wiki-fabric/venv"))
        .expanduser()
        .absolute()
    )
    catalog = (
        Path(os.environ.get("FABRIC_CATALOG", "~/.config/llm-wiki-fabric/resources.yaml"))
        .expanduser()
        .absolute()
    )
    if not shutil.which("uv"):
        raise SystemExit("Install uv and Python 3.11+ before running setup.")
    subprocess.run(
        [
            "uv",
            "sync",
            "--project",
            str(source),
            "--locked",
            "--no-dev",
            "--no-editable",
            "--reinstall-package",
            "llm-wiki-fabric",
        ],
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
        check=True,
    )
    catalog.parent.mkdir(parents=True, exist_ok=True)
    # Never replace operator catalog or dictionary files during plugin upgrades.
    for file in (source / "descriptors").glob("*.json"):
        target = catalog.parent / "descriptors" / file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x") as out:
                out.write(file.read_text())
        except FileExistsError:
            pass
    try:
        with catalog.open("x") as out:
            out.write((source / "resources.yaml").read_text())
    except FileExistsError:
        pass
    print(f"Runtime: {environment}\nCatalog: {catalog}")
    print("Existing config preserved. Database access needs a local read-only credential profile.")
    print("Restart Claude Code or Codex to connect the plugin MCP servers.")


if __name__ == "__main__":
    main()
