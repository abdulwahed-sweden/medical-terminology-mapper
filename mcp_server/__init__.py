"""terminology-mcp — an MCP server over the Swedish terminology adapters.

Read and propose. There is deliberately no tool that records a decision; see
`mcp_server.server` and docs/MCP.md for why.
"""

from __future__ import annotations

from mcp_server.server import build_server

__all__ = ["build_server"]
