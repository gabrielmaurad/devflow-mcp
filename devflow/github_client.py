"""A thin, safe wrapper around the GitHub REST API (read-only GET requests).

Security properties (see also ``security.py``):

* FIXED BASE URL: the host can never be influenced by tool arguments.
* SAFE PATHS: the URL path is built from separate *segments*. Empty, ``.`` and ``..``
  segments are refused and every segment is percent-encoded. This is a second layer
  behind ``security.validate_repo``.
* TOKEN HANDLING: the token is attached as an ``Authorization`` header at request
  time only. It is never logged, returned to the model, or put in an error message.
* NO REDIRECTS: a redirect could carry the token somewhere unexpected.
* NO ERROR LEAKS: upstream response bodies and headers are never forwarded; status
  codes are mapped to short, fixed messages.

CITATION: endpoints, the ``X-GitHub-Api-Version`` header, the raw README media type and
the ``x-ratelimit-*`` headers come from the GitHub REST API docs:
  https://docs.github.com/en/rest/commits/commits#list-commits
  https://docs.github.com/en/rest/repos/contents#get-a-repository-readme
  https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .errors import InputValidationError, UpstreamError

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.github.com"  # constant on purpose: user input can never change the host
API_VERSION = "2022-11-28"
USER_AGENT = "devflow-mcp/1.0 (ENMGT 5405 assignment)"  # GitHub rejects requests without a User-Agent
DEFAULT_TIMEOUT_SECONDS = 10.0
JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw+json"

_REDIRECT_STATUSES = {301, 302, 307, 308}


@dataclass(frozen=True)
class GitHubResponse:
    """A successful response: parsed body plus GitHub's quota headers."""

    body: Any  # parsed JSON, plain text for the raw media type, or None for an empty 204 reply
    rate_limit_remaining: int | None
    rate_limit_limit: int | None


class GitHubClient:
    """Async GitHub client. One instance is shared by all tools."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._transport = transport  # tests inject an httpx.MockTransport here (no real network)

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    # -- public API ---------------------------------------------------------

    async def get_json(self, segments: Sequence[str], params: Mapping[str, str | int] | None = None) -> GitHubResponse:
        """GET a JSON resource, e.g. ``get_json(["repos", "octocat", "Hello-World"])``."""
        response = await self._get(segments, params, JSON_ACCEPT)
        if response.status_code == 204 or not response.content:
            body: Any = None
        else:
            try:
                body = response.json()
            except ValueError:
                raise UpstreamError("GitHub returned a response that could not be read.") from None
        return self._wrap(response, body)

    async def get_text(self, segments: Sequence[str], params: Mapping[str, str | int] | None = None) -> GitHubResponse:
        """GET a resource using the raw media type (used for README Markdown)."""
        response = await self._get(segments, params, RAW_ACCEPT)
        return self._wrap(response, response.text)

    @staticmethod
    def build_url(segments: Sequence[str]) -> str:
        """Join path segments onto the fixed API base URL (second layer of path-traversal defense)."""
        encoded: list[str] = []
        for segment in segments:
            if not isinstance(segment, str) or segment in {"", ".", ".."}:
                raise InputValidationError("Invalid URL path segment.")
            encoded.append(quote(segment, safe=""))  # '/', '?', '#', '%' and spaces become %XX
        return f"{API_BASE_URL}/{'/'.join(encoded)}"

    # -- internals ----------------------------------------------------------

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION, "User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get(
        self, segments: Sequence[str], params: Mapping[str, str | int] | None, accept: str
    ) -> httpx.Response:
        url = self.build_url(segments)
        try:
            # httpx docs, "Mock transports": passing transport= lets tests replace the network.
            # https://www.python-httpx.org/advanced/transports/#mock-transports
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.get(url, params=params, headers=self._headers(accept))
        except httpx.TimeoutException:
            raise UpstreamError("GitHub did not respond in time. Try again shortly.") from None
        except httpx.RequestError:
            raise UpstreamError("Could not reach GitHub. Check the internet connection.") from None

        if response.status_code == 200 or response.status_code == 204:
            return response
        # Log only the method, path and status. Never headers, query strings or bodies.
        logger.warning("GitHub request failed: GET %s -> HTTP %s", response.request.url.path, response.status_code)
        raise self._error_for(response)

    def _error_for(self, response: httpx.Response) -> UpstreamError:
        """Map a failed response to a short, fixed, safe message."""
        status = response.status_code
        remaining = _int_header(response, "x-ratelimit-remaining")
        if status == 429 or (status == 403 and remaining == 0):
            hint = "" if self.authenticated else " Add a GITHUB_TOKEN to .env for a much higher limit."
            return UpstreamError(f"GitHub API rate limit reached{_reset_hint(response)}.{hint}")
        if status == 401:
            return UpstreamError(
                "GitHub rejected the configured token (it may be invalid, expired or revoked). "
                "Check GITHUB_TOKEN in .env."
            )
        if status == 403:
            if self.authenticated:
                return UpstreamError(
                    "GitHub refused this request. The token may lack read access "
                    "(it needs Contents: read) to this resource."
                )
            return UpstreamError("GitHub refused this request. Private resources need a GITHUB_TOKEN in .env.")
        if status == 404:
            return UpstreamError("Repository or resource not found, or the configured token cannot access it.")
        if status in _REDIRECT_STATUSES:
            return UpstreamError("The repository appears to have moved or been renamed. Use its current owner/name.")
        if status >= 500:
            return UpstreamError(f"GitHub is having trouble right now (HTTP {status}). Try again later.")
        return UpstreamError(f"GitHub returned an unexpected response (HTTP {status}).")

    @staticmethod
    def _wrap(response: httpx.Response, body: Any) -> GitHubResponse:
        return GitHubResponse(
            body=body,
            rate_limit_remaining=_int_header(response, "x-ratelimit-remaining"),
            rate_limit_limit=_int_header(response, "x-ratelimit-limit"),
        )


def _int_header(response: httpx.Response, name: str) -> int | None:
    try:
        return int(response.headers[name])
    except (KeyError, ValueError):
        return None


def _reset_hint(response: httpx.Response) -> str:
    reset = _int_header(response, "x-ratelimit-reset")
    if reset is None:
        return ""
    try:
        return f" (resets at {datetime.fromtimestamp(reset, tz=timezone.utc):%H:%M} UTC)"
    except (OverflowError, OSError, ValueError):
        return ""
