"""Print a ready-to-paste Claude Desktop config with the correct ABSOLUTE paths for this machine.

Run (from anywhere):  python scripts/make_claude_config.py

Why a script? Claude Desktop needs absolute paths, and hand-editing them (especially Windows
backslashes in JSON) is the most common way to get "server not showing up". ``json.dumps`` does the
escaping correctly and ``pathlib`` builds the paths for whichever OS you are on.

CITATION: the config shape (an ``mcpServers`` object whose entries have ``command`` and ``args``, absolute
paths, and a restart after editing) follows "Build an MCP server" > "Testing your server with Claude for
Desktop": https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server
The command points at this project's virtual-environment Python instead of ``uv``, because Claude Desktop
launches servers with a minimal PATH and often cannot find ``uv`` (the same page warns about this).
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PROJECT_ROOT / "server.py"
SERVER_NAME = "devflow"


def venv_python() -> Path:
    """Path to the project's virtual-environment interpreter on this operating system."""
    relative = Path("Scripts") / "python.exe" if sys.platform == "win32" else Path("bin") / "python"
    return PROJECT_ROOT / ".venv" / relative


def desktop_config_location() -> Path:
    """Where Claude Desktop normally keeps its config on this OS (Settings > Developer > Edit Config opens it)."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def main() -> int:
    python_path = venv_python()
    config = {"mcpServers": {SERVER_NAME: {"command": str(python_path), "args": [str(SERVER_SCRIPT)]}}}

    print("Paste this into claude_desktop_config.json. If the file already has an \"mcpServers\" block,")
    print(f"add just the \"{SERVER_NAME}\" entry inside it:\n")
    print(json.dumps(config, indent=2))
    print(f"\nConfig file (usually): {desktop_config_location()}")
    print("Easiest way to open it: Claude Desktop > Settings > Developer > Edit Config.")
    print("Afterwards, fully quit and reopen Claude Desktop.\n")

    problems = 0
    if not python_path.exists():
        problems += 1
        print(f"WARNING: {python_path} does not exist yet. Create the virtual environment first (see README).")
    if not (PROJECT_ROOT / ".env").exists():
        print("Note: no .env file found. The server will still run, anonymously (public repos, 60 requests/hour).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
