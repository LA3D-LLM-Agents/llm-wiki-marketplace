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
    policy = (
        Path(os.environ.get("FABRIC_CLIENT_POLICY", "~/.config/llm-wiki-fabric/client-policy.yaml"))
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
    policy.parent.mkdir(parents=True, exist_ok=True)
    try:
        with policy.open("x") as out:
            out.write((source / "client-policy.yaml").read_text())
    except FileExistsError:
        pass
    print(f"Runtime: {environment}\nPolicy: {policy}")
    print("Existing config preserved. Database access needs SSH login and a local credential keyed by resource ID.")
    print("Restart Claude Code or Codex to connect the plugin MCP servers.")


if __name__ == "__main__":
    main()
