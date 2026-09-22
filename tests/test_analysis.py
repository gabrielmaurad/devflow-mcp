"""Tests for the pure data-shaping functions (devflow/analysis.py)."""

import unittest
from datetime import datetime, timezone

from devflow.analysis import (
    activity_level,
    language_breakdown,
    parse_github_time,
    subject_line,
    summarize_commits,
    summarize_repository,
)
from tests.fakes import COMMITS

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class CommitSummaryTests(unittest.TestCase):
    def test_keeps_subject_line_only_and_scrubs_control_characters(self):
        first, second, third = summarize_commits(COMMITS)
        self.assertEqual(first["message"], "fix: handle empty input")  # the injected paragraph is dropped
        self.assertEqual(second["message"], "feat: add [31m red thing")
        self.assertEqual(first["sha"], "aaaaaaa")
        self.assertEqual(first["author"], "Ada Lovelace")

    def test_only_github_links_survive(self):
        first, _, third = summarize_commits(COMMITS)
        self.assertTrue(first["url"].startswith("https://github.com/"))
        self.assertIsNone(third["url"])

    def test_tolerates_missing_fields(self):
        result = summarize_commits([{}, {"commit": None}, {"sha": "abcdef123", "commit": {"author": None}}])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[2]["sha"], "abcdef1")
        self.assertEqual(result[2]["message"], "")

    def test_subject_line_is_bounded(self):
        self.assertLessEqual(len(subject_line("x" * 1000)), 200)
        self.assertEqual(subject_line(None), "")
        self.assertEqual(subject_line("   \n  "), "")


class LanguageTests(unittest.TestCase):
    def test_percentages_and_ordering(self):
        result = language_breakdown({"Shell": 1500, "Python": 8000, "Dockerfile": 500})
        self.assertEqual([item["language"] for item in result], ["Python", "Shell", "Dockerfile"])
        self.assertEqual([item["percent"] for item in result], [80.0, 15.0, 5.0])

    def test_top_n_and_empty_input(self):
        many = {f"Lang{i}": 100 - i for i in range(10)}
        self.assertEqual(len(language_breakdown(many, top_n=3)), 3)
        self.assertEqual(language_breakdown({}), [])
        self.assertEqual(language_breakdown({"X": 0}), [])


class ActivityTests(unittest.TestCase):
    def test_bucket_boundaries(self):
        self.assertEqual(activity_level(0), "active")
        self.assertEqual(activity_level(30), "active")
        self.assertEqual(activity_level(31), "slowing")
        self.assertEqual(activity_level(180), "slowing")
        self.assertEqual(activity_level(181), "stale")


class RepositorySummaryTests(unittest.TestCase):
    REPO = {
        "full_name": "octocat/Hello-World",
        "description": "First line\nSecond line",
        "private": False,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-09-19T12:00:00Z",
        "stargazers_count": 10,
        "forks_count": 2,
        "open_issues_count": 3,
    }

    def test_full_summary(self):
        summary = summarize_repository(
            self.REPO, {"Python": 900, "Shell": 100}, [{"login": "ada", "contributions": 5}], now=NOW
        )
        self.assertEqual(summary["visibility"], "public")
        self.assertEqual(summary["days_since_last_push"], 2)
        self.assertEqual(summary["activity"], "active")
        self.assertEqual(summary["description"], "First line Second line")
        self.assertEqual(summary["license"], "MIT")
        self.assertEqual(summary["languages"][0], {"language": "Python", "percent": 90.0})
        self.assertEqual(summary["top_contributors"], [{"login": "ada", "contributions": 5}])
        self.assertEqual(summary["open_issues_and_prs"], 3)

    def test_private_and_missing_optional_data(self):
        summary = summarize_repository({"full_name": "a/b", "private": True}, None, None, now=NOW)
        self.assertEqual(summary["visibility"], "private")
        self.assertEqual(summary["activity"], "unknown")
        self.assertEqual(summary["languages"], [])
        self.assertEqual(summary["top_contributors"], [])

    def test_parse_github_time_handles_the_z_suffix(self):
        self.assertEqual(parse_github_time("2026-09-21T12:00:00Z"), NOW)


if __name__ == "__main__":
    unittest.main()
