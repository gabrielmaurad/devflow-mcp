"""Exception types whose messages are SAFE to show to the model and the user.

Design rule
-----------
Anything that derives from ``DevFlowError`` was raised on purpose, and its message
was written to be read by the model: no secrets, no upstream response bodies, no
stack traces. Any OTHER exception is a bug; the server logs it to stderr and
replaces it with a generic message (see ``_guarded`` in ``devflow/app.py``).
"""

from __future__ import annotations

import math


class DevFlowError(Exception):
    """Base class for expected failures whose message is safe to display."""


class ConfigError(DevFlowError):
    """Invalid or unsafe configuration in the .env file."""


class InputValidationError(DevFlowError):
    """A tool argument failed validation; rejected before any network call."""


class RateLimitExceeded(DevFlowError):
    """Too many calls inside the current rate-limit window."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        wait = max(1, math.ceil(self.retry_after_seconds))
        super().__init__(f"Rate limit exceeded. Please wait about {wait} second(s) before trying again.")


class UpstreamError(DevFlowError):
    """GitHub returned an error or could not be reached (details deliberately omitted)."""
