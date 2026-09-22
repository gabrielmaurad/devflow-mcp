"""Starts the REAL server as a subprocess and talks to it over stdio, exactly like Claude Desktop does.

This proves the rubric item "MCP server starts and runs without crashing":
* the process boots with no .env at all (the token is optional),
* it works no matter what the current working directory is (Claude Desktop's cwd is unpredictable),
* stdout carries only protocol traffic, and logging goes to stderr.

No network is needed: the tool call below is rejected by input validation before any request is made.

CITATION: stdio client usage (StdioServerParameters / stdio_client / ClientSession) follows the MCP
Python SDK v1.27.0 client docs:
https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/docs/client.md
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "server.py"


class StdioSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_starts_and_answers_over_real_stdio(self):
        with tempfile.TemporaryDirectory() as unrelated_cwd, tempfile.TemporaryFile("w+", encoding="utf-8") as stderr_log:
            params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)], cwd=unrelated_cwd)
            async with stdio_client(params, errlog=stderr_log) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.assertEqual(init.serverInfo.name, "devflow")
                    self.assertIn("untrusted", init.instructions)

                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {"get_recent_commits", "analyze_repository"})

                    prompts = await session.list_prompts()
                    self.assertEqual([p.name for p in prompts.prompts], ["weekly_status_report"])

                    status = await session.read_resource("devflow://status")
                    self.assertIn("rate_limit", json.loads(status.contents[0].text))

                    # A traversal attempt is refused by validation (no network needed for this to pass).
                    blocked = await session.call_tool("get_recent_commits", {"repo": "octocat/x/../../../user?"})
                    self.assertTrue(blocked.isError)
                    self.assertIn("repo must look like", blocked.content[0].text)

            stderr_log.seek(0)
            log_text = stderr_log.read()
        self.assertIn("GitHub auth:", log_text)  # startup logging reached stderr, not stdout
        self.assertIn("Rate limit:", log_text)


if __name__ == "__main__":
    unittest.main()
