"""Short-lived, key-authenticated SSH forwarding for descriptor-defined SQL access."""

import socket
import subprocess
import time
from contextlib import contextmanager

from .catalog import FabricError


@contextmanager
def sql_route(connection):
    if connection.access is None:
        yield connection.host, connection.port
        return
    # Only remote loopback destinations are supported for this transport.
    if connection.host not in {"127.0.0.1", "localhost", "::1"}:
        raise FabricError("unsupported_access", "SSH database destination must be loopback")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        local_port = sock.getsockname()[1]
    destination = "[::1]" if connection.host == "::1" else connection.host
    command = [
        "ssh",
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        "-p",
        str(connection.access.port),
        "-L",
        f"127.0.0.1:{local_port}:{destination}:{connection.port}",
        connection.access.host,
    ]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        raise FabricError("ssh_unavailable", "SSH client is unavailable") from None
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise FabricError(
                    "ssh_access_failed",
                    "SSH access failed; configure your login and trusted host key",
                )
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise FabricError("ssh_timeout", "SSH forwarding did not become ready")
        yield "127.0.0.1", local_port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
