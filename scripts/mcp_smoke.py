#!/usr/bin/env python
"""Start the MCP server in-process and call one tool.

Run in CI after the sample data is loaded. It answers the only question a smoke
test should: does the server build, register its tools, and serve a request
against a real database? Depth belongs in tests/test_mcp_server.py.

Lives in a file rather than inline in the workflow because YAML block scalars
and Python indentation do not mix -- an earlier inline version silently broke
the workflow so badly that it produced no jobs at all.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import Client
from mcp.types import TextContent

from mcp_server.server import build_server


def _payload(result: object) -> dict[str, Any]:
    """Read a tool's JSON payload out of its first content block.

    A tool result can carry images, audio or resource links; ours carry one
    block of JSON text. Asserting that rather than reaching for `.text` and
    hoping keeps the type checker honest and turns a protocol change into a
    clear failure instead of an AttributeError.
    """
    content = getattr(result, "content", [])
    if not content or not isinstance(content[0], TextContent):
        raise SystemExit(f"expected a text content block, got: {content!r}")
    parsed = json.loads(content[0].text)
    if not isinstance(parsed, dict):
        raise SystemExit(f"expected a JSON object, got: {parsed!r}")
    return parsed


async def _run() -> int:
    async with Client(build_server()) as client:
        listed = _payload(await client.call_tool("list_terminologies", {}))
        if not listed.get("ok"):
            print(f"list_terminologies failed: {listed}", file=sys.stderr)
            return 1
        loaded = [t for t in listed["terminologies"] if t["status"] == "loaded"]
        if not loaded:
            print("no terminology is loaded; load the sample first", file=sys.stderr)
            return 1

        tools = (await client.list_tools()).tools
        names = {tool.name for tool in tools}
        forbidden = {n for n in names if any(v in n for v in ("accept", "reject", "decide"))}
        if forbidden:
            print(f"a decision-shaped tool appeared: {forbidden}", file=sys.stderr)
            return 1

        print(
            f"mcp smoke ok: {len(names)} tools, "
            f"{len(loaded)} loaded terminolog{'y' if len(loaded) == 1 else 'ies'}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
