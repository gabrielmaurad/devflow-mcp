"""Entry point for the devflow MCP server.

Run it directly (``python server.py``) or let Claude Desktop launch it. It speaks MCP over
stdio, so the ONLY thing allowed on stdout is protocol traffic: anything else printed there
would corrupt the JSON-RPC stream. All logging therefore goes to stderr.

CITATION: the "never write to stdout in a stdio server; use the logging module (stderr)" rule is from
the MCP guide "Build an MCP server" > "Logging in MCP Servers":
https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server
and from "Debugging" > "Server-side logging": https://modelcontextprotocol.io/docs/tools/debugging
"""

import logging
import sys

from devflow.app import create_server
from devflow.config import load_settings
from devflow.errors import ConfigError

logger = logging.getLogger("devflow")


def main() -> int:
    # stream=sys.stderr is the point: stdout belongs to the MCP protocol.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO level; keep operational logs focused on our own messages.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings()
    except ConfigError as exc:  # fail fast on a bad .env, with a message that never contains the secret
        logger.error("Configuration error: %s", exc)
        return 1

    if settings.authenticated:
        logger.info("GitHub auth: token loaded from .env (authenticated requests)")
    else:
        logger.warning("GitHub auth: no token in .env, running anonymously (public repos, 60 requests/hour)")
    logger.info(
        "Rate limit: %s tool calls per %s seconds", settings.rate_limit_calls, settings.rate_limit_window_seconds
    )

    create_server(settings).run()  # default transport is stdio
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
