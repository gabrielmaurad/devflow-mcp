"""Live smoke test: start the REAL server over stdio and call it against the REAL GitHub API.

Run:  python scripts/live_check.py [owner/name]        (default: modelcontextprotocol/python-sdk)

Use it after setting up .env to confirm that authentication works BEFORE opening Claude Desktop.
It makes 2 tool calls and 1 resource read, well under the default rate limit of 10 calls/minute.
Try it with a PRIVATE repo of yours to prove the token works (anonymous requests cannot see it).

CITATION: the stdio client pattern (StdioServerParameters / stdio_client / ClientSession) follows the
MCP Python SDK v1.27.0 client docs:
https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/docs/client.md
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "modelcontextprotocol/python-sdk"


async def run_checks(session: ClientSession, repo: str) -> int:
    """Exercise both tools and both kinds of resource. Returns the number of failed checks."""
    failures = 0

    result = await session.call_tool("get_recent_commits", {"repo": repo, "days": 30, "limit": 5})
    if result.isError:
        failures += 1
        print("get_recent_commits FAILED:", result.content[0].text)
    else:
        data = result.structuredContent
        print(f"get_recent_commits: {data['count']} commit(s) in the last 30 days (newest first)")
        for commit in data["commits"][:3]:
            print(f"   {commit['sha']}  {commit['author']}: {commit['message']}")
        print(f"   GitHub quota remaining: {data['github_rate_limit_remaining']}\n")

    result = await session.call_tool("analyze_repository", {"repo": repo})
    if result.isError:
        failures += 1
        print("analyze_repository FAILED:", result.content[0].text)
    else:
        data = result.structuredContent
        languages = ", ".join(f"{item['language']} {item['percent']}%" for item in data["languages"][:3])
        print(f"analyze_repository: {data['visibility']} repo, activity={data['activity']}, "
              f"{data['stars']} stars, languages: {languages}")
        print(f"   authenticated request: {data['authenticated']}\n")

    owner, name = repo.split("/", 1)
    try:
        readme = await session.read_resource(f"repo://{owner}/{name}/readme")
        first_line = readme.contents[0].text.strip().splitlines()[0]
        print(f"README resource: OK (first line: {first_line[:70]!r})\n")
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        failures += 1
        print("README resource FAILED:", exc)

    status = await session.read_resource("devflow://status")
    print("Server status resource:", json.dumps(json.loads(status.contents[0].text)["rate_limit"]))
    return failures


async def main(repo: str) -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(PROJECT_ROOT / "server.py")])
    # The server's stderr log goes to a temp file: quiet when all is well, shown if something fails.
    with tempfile.TemporaryFile("w+", encoding="utf-8") as server_log:
        async with stdio_client(params, errlog=server_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print(f"Connected to the devflow MCP server. Checking {repo} ...\n")
                failures = await run_checks(session, repo)

        if failures:
            server_log.seek(0)
            print("\n--- server log (stderr) ---")
            print(server_log.read()[-3000:])
    print("\nAll checks passed." if failures == 0 else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO)))
