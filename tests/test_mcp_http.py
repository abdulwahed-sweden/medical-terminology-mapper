"""The streamable-HTTP transport, exercised over a real socket.

`--transport streamable-http` is a documented flag, and a documented flag that
nothing runs is a claim the repository cannot back. This starts the server the
way the flag says to start it, connects with the SDK's HTTP client, and calls a
tool -- one round trip through a real port.

It is not a substitute for `test_mcp_server.py`: tool behaviour is covered
in-memory there, and repeating it over a socket would only buy flakiness. What
this covers is the transport, and the fact that the entry point can serve on it
at all.

The transport carries no authentication and binds to localhost -- see
`docs/MCP.md`.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from mcp import Client

pytestmark = pytest.mark.requires_db

STARTUP_TIMEOUT = 30.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _accepting(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def http_server() -> Iterator[str]:
    """Run `terminology-mcp --transport streamable-http` and yield its URL."""
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mcp_server",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited early:\n{process.communicate()[1]}")
        if _accepting(port):
            break
        time.sleep(0.1)
    else:
        process.kill()
        raise AssertionError(f"server did not accept connections within {STARTUP_TIMEOUT}s")

    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.anyio
async def test_a_tool_can_be_called_over_streamable_http(http_server: str) -> None:
    async with Client(http_server) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert "list_terminologies" in tools

        result = await client.call_tool("list_terminologies", {})
        payload = json.loads(result.content[0].text)

    assert payload["ok"] is True
    assert isinstance(payload["terminologies"], list)


@pytest.mark.anyio
async def test_the_http_transport_exposes_no_decision_tool(http_server: str) -> None:
    """The boundary is a property of the server, not of one transport."""
    async with Client(http_server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert not {n for n in names if any(v in n for v in ("accept", "reject", "decide"))}
