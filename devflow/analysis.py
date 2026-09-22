"""Pure functions that turn raw GitHub JSON into compact, model-friendly summaries.

No network and no I/O happen here, which keeps this module trivially unit-testable.
All free-text fields coming from GitHub are passed through ``clean_text`` because
commit messages and descriptions are written by third parties and are therefore
untrusted input to the model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .security import clean_text

MAX_MESSAGE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 300
TOP_N = 5


def parse_github_time(value: str) -> datetime:
    """Parse GitHub's ISO-8601 timestamps (they end in 'Z'; Python 3.10 cannot parse that suffix directly)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def subject_line(message: object) -> str:
    """First line of a commit message, cleaned and length-bounded."""
    text = message if isinstance(message, str) else ""
    lines = text.strip().splitlines()
    return clean_text(lines[0] if lines else "", MAX_MESSAGE_LENGTH)


def summarize_commits(raw_commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce GitHub's verbose commit objects to sha / author / date / subject / url."""
    summaries: list[dict[str, Any]] = []
    for item in raw_commits:
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        url = item.get("html_url")
        summaries.append(
            {
                "sha": str(item.get("sha", ""))[:7],
                "author": clean_text(author.get("name"), 80),
                "date": author.get("date"),
                "message": subject_line(commit.get("message")),
                # Only pass on links that really point at github.com.
                "url": url if isinstance(url, str) and url.startswith("https://github.com/") else None,
            }
        )
    return summaries


def language_breakdown(languages: Mapping[str, int], top_n: int = TOP_N) -> list[dict[str, Any]]:
    """Turn GitHub's bytes-per-language map into the top-N languages with percentages of total bytes."""
    total = sum(languages.values())
    if total <= 0:
        return []
    ranked = sorted(languages.items(), key=lambda pair: pair[1], reverse=True)[:top_n]
    return [{"language": clean_text(name, 40), "percent": round(100 * size / total, 1)} for name, size in ranked]


def activity_level(days_since_push: int) -> str:
    """Bucket recency of the last push: active (<=30 days), slowing (<=180), stale (older)."""
    if days_since_push <= 30:
        return "active"
    if days_since_push <= 180:
        return "slowing"
    return "stale"


def summarize_repository(
    repo: Mapping[str, Any],
    languages: Mapping[str, int] | None,
    contributors: Sequence[Mapping[str, Any]] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the health snapshot returned by the ``analyze_repository`` tool."""
    now = now or datetime.now(timezone.utc)
    pushed_at = repo.get("pushed_at")
    created_at = repo.get("created_at")
    days_since_push = (now - parse_github_time(pushed_at)).days if pushed_at else None
    age_days = (now - parse_github_time(created_at)).days if created_at else None
    license_info = repo.get("license") or {}

    return {
        "full_name": repo.get("full_name"),
        "description": clean_text(repo.get("description"), MAX_DESCRIPTION_LENGTH),
        "visibility": "private" if repo.get("private") else "public",
        "default_branch": repo.get("default_branch"),
        "archived": bool(repo.get("archived")),
        "license": license_info.get("spdx_id"),
        "created_at": created_at,
        "pushed_at": pushed_at,
        "age_days": age_days,
        "days_since_last_push": days_since_push,
        "activity": activity_level(days_since_push) if days_since_push is not None else "unknown",
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        # GitHub's open_issues_count includes open pull requests.
        "open_issues_and_prs": repo.get("open_issues_count"),
        "languages": language_breakdown(languages or {}),
        "top_contributors": [
            {"login": clean_text(person.get("login"), 40), "contributions": person.get("contributions")}
            for person in (contributors or [])[:TOP_N]
        ],
    }
