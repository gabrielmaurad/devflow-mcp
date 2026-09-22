# devflow-mcp

A small **Model Context Protocol (MCP) server** that gives an AI assistant such as Claude Desktop
**read-only** access to GitHub repositories: what changed recently, how healthy a repository is, and a
ready-made prompt for writing a weekly status report.

Built for Cornell ENMGT 5405, Assignment 4 ("Calling services: Model Context Protocol"), with the help of an
AI assistant (Claude), as the assignment requires.

## What it exposes

| Kind | Name | What it does |
|---|---|---|
| Tool | `get_recent_commits(repo, days=7, limit=20, branch=None)` | Newest-first commits: short SHA, author, date, subject line, link. |
| Tool | `analyze_repository(repo)` | Health snapshot: visibility, license, stars/forks, open issues + PRs, language mix, top contributors, activity level. |
| Resource | `repo://{owner}/{repo}/readme` | The repository's README as Markdown (template: any repo). |
| Resource | `devflow://status` | The limits this server enforces right now (never the token). |
| Prompt | `weekly_status_report(repo, days=7, audience="engineering manager")` | Tells the model which tools to call and how to write a short, non-technical status report. |

Everything is read-only. `repo` is always written as `owner/name`, for example `modelcontextprotocol/python-sdk`.

## Requirements

* Python **3.10 or newer**
* [uv](https://docs.astral.sh/uv/) (recommended) or plain `python -m venv`
* VS Code (or Cursor) and Claude Desktop
* Optional: a GitHub token (needed only for private repositories; see step 4)

## Setup

**1. Get the code and open it in VS Code**

```bash
git clone <your-repository-url> devflow-mcp
cd devflow-mcp
code .
```

**2. Create and activate a virtual environment** (the terminal prompt should then start with `(.venv)`)

```bash
# macOS / Linux
uv venv --python 3.12
source .venv/bin/activate

# Windows (PowerShell)
uv venv --python 3.12
.venv\Scripts\Activate.ps1
```

Without uv: `python -m venv .venv`, then activate as above. In VS Code, run **Python: Select Interpreter** and pick
the `.venv` one.

**3. Install the dependencies**

```bash
uv pip install -r requirements.txt      # or: pip install -r requirements.txt
```

**4. Add your GitHub token (optional but recommended)**

```bash
cp .env.example .env                    # Windows: copy .env.example .env
```

Open `.env` and paste a token after `GITHUB_TOKEN=`. Create a **fine-grained, read-only** token at
GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens:
*Only select repositories* (pick the ones you want) and **Contents: Read-only**. Without a token the server still
works for public repositories (GitHub allows 60 requests/hour anonymously, 5,000/hour with a token).
`.env` is git-ignored. Never commit it.

**5. Verify everything works**

```bash
python -m unittest                      # unit + protocol + real-stdio tests (no network needed)
python exploits/run_all.py              # the two exploit demos (see "Security design")
python scripts/live_check.py            # real GitHub call through the real server (needs internet)
```

Tip: `python scripts/live_check.py your-username/your-private-repo` proves the token works, because anonymous
requests cannot see private repositories.

## Connect it to Claude Desktop

```bash
python scripts/make_claude_config.py
```

It prints a ready-to-paste config with the correct absolute paths for your machine. Paste it into Claude Desktop's
config (Claude Desktop > Settings > Developer > Edit Config), then **fully quit and reopen Claude Desktop**
(closing the window is not enough). `claude_desktop_config.example.json` shows the shape:

```json
{
  "mcpServers": {
    "devflow": {
      "command": "/ABSOLUTE/PATH/TO/devflow-mcp/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/devflow-mcp/server.py"]
    }
  }
}
```

On Windows the command is `...\\.venv\\Scripts\\python.exe` (backslashes must be doubled in JSON; the script does
this for you). The config deliberately has **no `env` block**: the secret lives only in `.env`.

Things to try in a Claude Desktop chat:

* "Use devflow to show what changed in modelcontextprotocol/python-sdk in the last 14 days."
* "Analyze the repository modelcontextprotocol/servers."
* Attach the **Weekly status report** prompt (from the + menu, under devflow) and give it a repository.

Running `python server.py` by hand appears to hang. That is normal: it is waiting for an MCP client on stdin.
Press Ctrl+C to stop it.

## Security design

**Secret management.** The GitHub token lives only in `.env`, read with python-dotenv's `dotenv_values()` from a path
built with `pathlib` relative to the source file (never the working directory, which Claude Desktop does not control).
`dotenv_values()` returns a plain dict without copying anything into the process environment, so credentials are never
stored as system environment variables, and the source never imports the `os` module (a test enforces this; the only
file that touches `os.environ` is `tests/test_config.py`, and only to prove the server ignores it). The token is
excluded from `repr()`, never logged, never returned to the model, and never included in error messages.

**Input sanitization.** Every argument the model sends is treated as untrusted, because a model that read a malicious
README or commit message can be steered into sending crafted values.

* `repo` must match allow-lists for owner and name (`re.fullmatch`, not `^...$`, which would accept a trailing newline);
  that blocks `../` traversal, `?`/`#` injection, percent-encoded tricks, CR/LF, oversize input and non-strings.
* Numbers must be real integers inside fixed ranges (booleans and strings are rejected); branch names must be plain
  names (no leading `-`, no `..`).
* The GitHub client builds URLs from separate path segments on a fixed host, refuses `.` and `..` segments and
  percent-encodes the rest: a second layer behind the validators.
* Validation runs first, so bad input never reaches the network or spends rate-limit budget.
* Text coming back from GitHub (commit messages, descriptions) is flattened to one printable line and length-bounded
  before the model sees it; the prompt also tells the model to treat it as data, not instructions.
* There is no shell, subprocess or SQL anywhere, so command and SQL injection are eliminated by design.

**Rate limiting.** A sliding-window limiter allows at most `RATE_LIMIT_CALLS` calls per `RATE_LIMIT_WINDOW_SECONDS`
(default 10 per 60 s, set in `.env`), shared by both tools and the README resource. Blocked calls never reach GitHub,
are not counted against you, and the model is told how long to wait. This protects GitHub's hourly quota from a
runaway loop.

**Also in place:** unexpected exceptions are logged to stderr and replaced with a generic message; only errors written
to be safe are shown to the model; redirects are not followed (they could carry the token elsewhere); all tools carry
the MCP `readOnlyHint` annotation.

### Exploit demos

```bash
python exploits/run_all.py
```

Each attack runs first against a deliberately naive baseline (`exploits/naive_server.py`, never used by the real
server) to show what it does, and then against the real server through the real MCP protocol. GitHub is simulated
(fake data, fake token), so nothing touches the network. Exit code 0 means every attack was blocked.

* **Input sanitization:** 8 attacks, including path traversal that would leak the token owner's private profile
  through a naive URL builder.
* **Rate limiting:** a burst of 25 calls; the naive baseline sends all 25 to GitHub, the real server serves 10 and
  blocks 15, then recovers after the window.

Live demo inside Claude Desktop: ask Claude to *call `get_recent_commits` with `repo` set exactly to
`octocat/Hello-World/../../../user?`* and it comes back as an error. For the rate limit, set `RATE_LIMIT_CALLS=3` in
`.env`, restart Claude Desktop, and ask for five `get_recent_commits` calls in a row.

## Tests

```bash
python -m unittest -v
```

Uses only the standard library. The suite covers validators and the rate limiter, config and secret handling, the
GitHub client (against a fake GitHub built on `httpx.MockTransport`), the server through the real MCP protocol
(in-memory client), and a real subprocess started over stdio from an unrelated working directory.

## Project layout

```
server.py                  entry point (stdio); logging goes to stderr
devflow/
  app.py                   builds the server: 2 tools, 2 resources, 1 prompt (wiring only)
  security.py              input validators, text cleaning, sliding-window rate limiter
  github_client.py         GitHub REST client: auth header, safe URLs, safe error messages
  analysis.py              pure functions that shape GitHub JSON into compact summaries
  config.py                reads .env with pathlib + python-dotenv into immutable Settings
  errors.py                exception types whose messages are safe to show
tests/                     unit, protocol and stdio tests, plus fakes.py (fake GitHub, fake clock)
exploits/                  exploit demos and the deliberately naive baseline
scripts/                   make_claude_config.py, live_check.py
.env.example               template for secrets and settings (copy to .env)
requirements.txt           pinned dependencies
```

## Troubleshooting

* **The server does not show up in Claude Desktop.** Validate the JSON, make sure both paths are absolute and the
  `.venv` Python exists (`python scripts/make_claude_config.py` warns if not), and fully quit and reopen Claude Desktop.
  Logs: macOS `~/Library/Logs/Claude/mcp*.log`, Windows `%APPDATA%\Claude\logs`.
* **"Could not reach GitHub".** No internet, or a firewall/proxy is in the way. Run `python scripts/live_check.py`.
* **"GitHub rejected the configured token".** The token is wrong, expired or revoked. Check `.env`: no quotes and no
  spaces around the value; create a new token if needed.
* **"Repository ... not found, or the configured token cannot access it".** For a private repository, the token must be
  allowed to read that specific repository.
* **"Rate limit exceeded".** You hit this server's own limit. Wait the number of seconds shown or raise
  `RATE_LIMIT_CALLS` in `.env`.
* **`No module named 'mcp.server.fastmcp'`.** You have mcp 2.x installed. Run `pip install -r requirements.txt`.
* **Never use `print()` in `server.py`.** stdout carries the protocol; extra output corrupts it. Use `logging` (stderr).

## SDK version note

The code targets the MCP Python SDK **1.27.0** (`mcp.server.fastmcp.FastMCP`), pinned in `requirements.txt`. SDK 2.x
renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`) and moved `ToolError`/`ResourceError`
to `mcp.server.mcpserver.exceptions`; the decorators are unchanged. See the SDK's migration guide:
https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver

## Sources and citations

Code adapted from or modelled on these sources is also cited in comments where it is used.

* MCP Python SDK v1.27.0 README, "Quickstart" (FastMCP tool / resource / prompt pattern):
  https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/README.md#quickstart
* MCP Python SDK: in-memory test session helper (`mcp/shared/memory.py`) and stdio client docs (`docs/client.md`)
* MCP Python SDK docs, "Handling errors" (raise `ToolError` so the model sees the failure):
  https://py.sdk.modelcontextprotocol.io/servers/handling-errors/
* MCP reference servers, Git server (parameter design of `git_log`; rejecting values that start with `-`):
  https://github.com/modelcontextprotocol/servers/tree/main/src/git
* MCP docs, "Build an MCP server" and "Debugging" (stdio logging rule, Claude Desktop config, log locations):
  https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server and https://modelcontextprotocol.io/docs/tools/debugging
* GitHub REST API: [list commits](https://docs.github.com/en/rest/commits/commits#list-commits),
  [get a README](https://docs.github.com/en/rest/repos/contents#get-a-repository-readme),
  [rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
* GitHub username rules:
  https://docs.github.com/en/enterprise-cloud@latest/admin/managing-iam/iam-configuration-reference/username-considerations-for-external-authentication
* python-dotenv README, `dotenv_values`: https://github.com/theskumar/python-dotenv#readme
* httpx docs, mock transports: https://www.python-httpx.org/advanced/transports/#mock-transports
* Python `re` docs, `fullmatch`: https://docs.python.org/3/library/re.html#re.Pattern.fullmatch
