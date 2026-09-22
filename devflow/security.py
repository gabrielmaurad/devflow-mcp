"""The two security best practices this server implements (Assignment 4, Part 2A).

1. INPUT SANITIZATION
   Every argument that reaches a tool comes from the model, and the model may have
   been steered by prompt-injected text (for example a malicious commit message or
   README). So arguments are untrusted. We validate with ALLOW-LISTS (describe
   exactly what is valid) rather than DENY-LISTS (try to guess what is dangerous),
   and we validate BEFORE any network call or rate-limit accounting.

2. RATE LIMITING
   A looping or manipulated model could hammer the GitHub API and burn the
   token's hourly quota (60/hour anonymous, 5,000/hour with a token). A
   sliding-window limiter caps how many tool calls can reach GitHub.

There is no shell, subprocess, or SQL anywhere in this server, so command and SQL
injection are eliminated by design. The injection risk that does exist is in how
user-controlled text ends up inside a URL, which is what ``validate_repo`` guards.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable

from .errors import InputValidationError, RateLimitExceeded

# --------------------------------------------------------------------------- #
# 1. Input sanitization
# --------------------------------------------------------------------------- #

# CITATION: GitHub username rules (alphanumerics and single dashes, cannot start or end
# with a dash, at most 39 characters), from GitHub Docs, "Username considerations for
# external authentication":
# https://docs.github.com/en/enterprise-cloud@latest/admin/managing-iam/iam-configuration-reference/username-considerations-for-external-authentication
_OWNER_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
MAX_OWNER_LENGTH = 39

# Repository names: letters, digits, '-', '_' and '.'; GitHub caps them at 100 characters.
_REPO_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
MAX_REPO_NAME_LENGTH = 100

_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]+")
MAX_BRANCH_LENGTH = 100

_AUDIENCE_RE = re.compile(r"[A-Za-z0-9 ,.'-]+")
MAX_AUDIENCE_LENGTH = 60

_REPO_HELP = (
    "repo must look like 'owner/name', for example 'modelcontextprotocol/python-sdk' "
    "(letters, digits, '-', '_' and '.' only)."
)

# NOTE on re.fullmatch: every pattern above is used with ``fullmatch`` rather than
# ``^...$``. In Python, ``$`` also matches just BEFORE a trailing newline, so
# re.match(r"^[a-z]+$", "abc\n") succeeds and would let a newline through.
# CITATION: https://docs.python.org/3/library/re.html#re.Pattern.fullmatch


def validate_repo(value: object) -> tuple[str, str]:
    """Return ``(owner, name)`` for a well-formed ``"owner/name"`` string, else raise.

    Blocks path traversal (``../``), query/fragment injection (``?`` ``#``), encoded
    tricks (``%2e%2e``), CR/LF injection, oversize input, and non-string types.
    """
    if isinstance(value, str) and len(value) <= MAX_OWNER_LENGTH + 1 + MAX_REPO_NAME_LENGTH:
        owner, slash, name = value.partition("/")
        if (
            slash
            and len(owner) <= MAX_OWNER_LENGTH
            and len(name) <= MAX_REPO_NAME_LENGTH
            and _OWNER_RE.fullmatch(owner)
            and _REPO_NAME_RE.fullmatch(name)
            and name not in {".", ".."}  # '.' and '..' pass the character check but are path segments
        ):
            return owner, name
    raise InputValidationError(_REPO_HELP)


def validate_int(name: str, value: object, minimum: int, maximum: int) -> int:
    """Return ``value`` if it is a real integer within [minimum, maximum], else raise."""
    # bool is a subclass of int in Python (True == 1), so it must be rejected explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InputValidationError(f"{name} must be a whole number between {minimum} and {maximum}.")
    return value


def validate_branch(value: object) -> str | None:
    """Return a safe branch/tag name, or None when not provided. Empty string counts as not provided."""
    if value is None or value == "":
        return None
    if (
        isinstance(value, str)
        and len(value) <= MAX_BRANCH_LENGTH
        and _BRANCH_RE.fullmatch(value)
        # CITATION (idea): the reference Git MCP server rejects values starting with '-' as
        # "defense in depth ... to prevent flag injection":
        # https://github.com/modelcontextprotocol/servers/blob/main/src/git/src/mcp_server_git/server.py
        and not value.startswith(("-", "/", "."))
        and not value.endswith(("/", ".", ".lock"))
        and ".." not in value
        and "//" not in value
    ):
        return value
    raise InputValidationError("branch must be a plain branch or tag name (letters, digits, '.', '_', '-', '/').")


def validate_audience(value: object) -> str:
    """Return a short plain-text audience description for the prompt template, else raise."""
    if isinstance(value, str):
        text = value.strip()
        if 0 < len(text) <= MAX_AUDIENCE_LENGTH and _AUDIENCE_RE.fullmatch(text):
            return text
    raise InputValidationError("audience must be a short plain-text description such as 'engineering manager'.")


def clean_text(value: object, max_length: int) -> str:
    """Make untrusted upstream text safer to hand to the model.

    Keeps printable characters only, collapses everything onto ONE line (so a commit
    message cannot smuggle in fake "sections"), and bounds the length.
    """
    text = value if isinstance(value, str) else ""
    printable = "".join(ch if ch.isprintable() else " " for ch in text)
    collapsed = " ".join(printable.split())
    if len(collapsed) > max_length:
        return collapsed[: max_length - 1].rstrip() + "…"
    return collapsed


# --------------------------------------------------------------------------- #
# 2. Rate limiting
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Sliding-window-log limiter: at most ``max_calls`` calls in any ``window_seconds`` span.

    We remember the timestamp of each accepted call. A new call is refused if
    ``max_calls`` timestamps are still inside the window; ``retry_after`` tells the
    caller when the oldest one expires. Refused calls are NOT recorded, so hammering
    the server does not extend the lock-out.

    The algorithm is the standard "sliding window log"; this implementation was
    written for this project. ``clock`` is injectable so tests and the exploit
    demos can move time forward instantly instead of sleeping.
    """

    def __init__(self, max_calls: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if max_calls < 1 or window_seconds <= 0:
            raise ValueError("max_calls must be >= 1 and window_seconds must be > 0")
        self._max_calls = max_calls
        self._window = float(window_seconds)
        self._clock = clock
        self._calls: deque[float] = deque()  # never longer than max_calls, so memory is bounded

    def acquire(self) -> None:
        """Record one call, or raise ``RateLimitExceeded`` if the budget is used up."""
        now = self._clock()
        cutoff = now - self._window
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()  # forget calls that have left the window
        if len(self._calls) >= self._max_calls:
            raise RateLimitExceeded(self._calls[0] + self._window - now)
        self._calls.append(now)
