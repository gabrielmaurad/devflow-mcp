"""Configuration and secret handling.

Where secrets live
------------------
Secrets (the GitHub token) live ONLY in a ``.env`` file that sits next to
``server.py``. That file is git-ignored; ``.env.example`` documents the keys.

How they are read
-----------------
We use python-dotenv's ``dotenv_values()``, which parses the file into a plain
dict and does NOT copy anything into the process environment. So credentials are
never stored as system/process environment variables, this server never reads the
environment, and the source never imports the ``os`` module at all (the Assignment 4
rubric gives zero points for system variables or for the ``os`` path helpers; a test
enforces this). The file location is built with ``pathlib``.

CITATION: python-dotenv README, section "Load configuration without altering the
environment" (``dotenv_values``): https://github.com/theskumar/python-dotenv#readme
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .errors import ConfigError

# Resolved from THIS file's location, never from the current working directory.
# Claude Desktop launches MCP servers with an unpredictable working directory, so a
# relative ".env" would silently not be found.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_RATE_LIMIT_CALLS = 10
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings. Immutable so nothing can change them mid-run."""

    # repr=False keeps the token out of logs, tracebacks and debugger output.
    github_token: str | None = field(default=None, repr=False)
    rate_limit_calls: int = DEFAULT_RATE_LIMIT_CALLS
    rate_limit_window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS

    @property
    def authenticated(self) -> bool:
        """True when a GitHub token is configured."""
        return self.github_token is not None


def load_settings(env_file: Path = DEFAULT_ENV_FILE) -> Settings:
    """Read and validate settings from ``env_file`` (a missing file means defaults)."""
    values = dotenv_values(env_file)  # returns {} if the file does not exist
    return Settings(
        github_token=_parse_token(values.get("GITHUB_TOKEN")),
        rate_limit_calls=_parse_int(values, "RATE_LIMIT_CALLS", DEFAULT_RATE_LIMIT_CALLS, 1, 1000),
        rate_limit_window_seconds=_parse_int(
            values, "RATE_LIMIT_WINDOW_SECONDS", DEFAULT_RATE_LIMIT_WINDOW_SECONDS, 1, 3600
        ),
    )


def _parse_token(raw: str | None) -> str | None:
    """Return a clean token, or None if unset. Refuse values that could corrupt an HTTP header."""
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    # Whitespace or non-ASCII characters in a header value are a header-injection risk.
    if not token.isascii() or any(ch.isspace() for ch in token):
        raise ConfigError("GITHUB_TOKEN in .env must be a single token with no spaces or line breaks.")
    return token


def _parse_int(values: Mapping[str, str | None], key: str, default: int, minimum: int, maximum: int) -> int:
    """Parse a bounded integer setting. Error messages never echo the raw value."""
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        number = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{key} in .env must be a whole number between {minimum} and {maximum}.") from None
    if not minimum <= number <= maximum:
        raise ConfigError(f"{key} in .env must be a whole number between {minimum} and {maximum}.")
    return number
