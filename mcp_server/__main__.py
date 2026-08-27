"""Entry point: `python -m mcp_server`, or the `terminology-mcp` console script.

stdio is the default transport, because that is how MCP clients launch a local
server: the client starts the process and speaks over its standard streams.
Nothing listens on a port unless `--transport streamable-http` is passed, and
that binds to localhost and carries no authentication.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import get_settings
from app.logging_setup import configure_logging
from mcp_server.server import build_server

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terminology-mcp", description=__doc__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio (default) is what MCP clients launch. streamable-http binds "
        "to localhost and has NO authentication -- local development only.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    settings = get_settings()
    # Logs go to stderr: stdout is the MCP wire on the stdio transport, and a
    # stray log line there corrupts the protocol.
    configure_logging(settings.log_level, stream=sys.stderr)

    server = build_server()
    logger.info(
        "mcp_server_starting",
        extra={
            "transport": args.transport,
            "llm_provider": settings.llm_provider,
            "embedding_provider": settings.embedding_provider,
        },
    )

    if args.transport == "stdio":
        server.run("stdio")
    else:
        logger.warning(
            "mcp_http_transport_has_no_auth",
            extra={"host": args.host, "port": args.port},
        )
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
