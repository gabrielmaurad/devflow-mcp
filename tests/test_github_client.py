"""Tests for the GitHub client (devflow/github_client.py) against the fake GitHub."""

import unittest

from devflow.errors import InputValidationError, UpstreamError
from devflow.github_client import API_BASE_URL, GitHubClient
from tests.fakes import VALID_TOKEN, FakeGitHub


class BuildUrlTests(unittest.TestCase):
    def test_joins_and_percent_encodes_segments(self):
        self.assertEqual(
            GitHubClient.build_url(["repos", "octocat", "Hello-World", "commits"]),
            f"{API_BASE_URL}/repos/octocat/Hello-World/commits",
        )
        # A '/', '?' or '#' inside one segment is encoded, so it cannot open a new path or query.
        url = GitHubClient.build_url(["repos", "a/b", "c?d#e"])
        self.assertEqual(url, f"{API_BASE_URL}/repos/a%2Fb/c%3Fd%23e")

    def test_dot_segments_are_refused_because_they_are_not_encoded_by_quote(self):
        for segment in ("", ".", ".."):
            with self.subTest(segment=segment):
                with self.assertRaises(InputValidationError):
                    GitHubClient.build_url(["repos", "octocat", segment])

    def test_host_is_fixed(self):
        self.assertTrue(GitHubClient.build_url(["x"]).startswith("https://api.github.com/"))


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def make(self, token=None):
        self.fake = FakeGitHub()
        return GitHubClient(token, transport=self.fake.transport)

    async def test_anonymous_requests_have_no_authorization_header(self):
        client = self.make()
        await client.get_json(["repos", "octocat", "Hello-World"])
        headers = self.fake.requests[0].headers
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["x-github-api-version"], "2022-11-28")
        self.assertIn("devflow-mcp", headers["user-agent"])
        self.assertFalse(client.authenticated)

    async def test_token_is_sent_as_bearer_header(self):
        client = self.make(VALID_TOKEN)
        response = await client.get_json(["repos", "octocat", "Hello-World"])
        self.assertEqual(self.fake.requests[0].headers["authorization"], f"Bearer {VALID_TOKEN}")
        self.assertTrue(client.authenticated)
        self.assertEqual((response.rate_limit_remaining, response.rate_limit_limit), (4990, 5000))

    async def test_token_never_appears_in_the_url(self):
        client = self.make(VALID_TOKEN)
        await client.get_json(["repos", "octocat", "Hello-World", "commits"], {"per_page": 5})
        self.assertNotIn(VALID_TOKEN, str(self.fake.requests[0].url))

    async def test_raw_media_type_returns_text(self):
        client = self.make()
        response = await client.get_text(["repos", "octocat", "Hello-World", "readme"])
        self.assertIn("# Hello World", response.body)
        self.assertEqual(self.fake.requests[0].headers["accept"], "application/vnd.github.raw+json")

    async def test_empty_204_reply_gives_none(self):
        client = self.make()
        response = await client.get_json(["repos", "octocat", "empty", "contributors"])
        self.assertIsNone(response.body)

    async def test_failures_map_to_short_safe_messages(self):
        cases = [
            ("octocat/nothere", "not found"),
            ("octocat/boom", "HTTP 500"),
            ("octocat/limited", "rate limit"),
            ("octocat/moved", "moved or been renamed"),
            ("octocat/timeout", "did not respond"),
        ]
        client = self.make(VALID_TOKEN)
        for full_name, expected in cases:
            owner, name = full_name.split("/")
            with self.subTest(repo=full_name):
                with self.assertRaises(UpstreamError) as ctx:
                    await client.get_json(["repos", owner, name])
                message = str(ctx.exception)
                self.assertIn(expected, message)
                self.assertNotIn("secret internal stack trace", message)  # upstream body never forwarded
                self.assertNotIn(VALID_TOKEN, message)

    async def test_rate_limit_message_suggests_a_token_only_when_anonymous(self):
        with self.assertRaises(UpstreamError) as anonymous:
            await self.make().get_json(["repos", "octocat", "limited"])
        self.assertIn("GITHUB_TOKEN", str(anonymous.exception))
        with self.assertRaises(UpstreamError) as authenticated:
            await self.make(VALID_TOKEN).get_json(["repos", "octocat", "limited"])
        self.assertNotIn("GITHUB_TOKEN", str(authenticated.exception))

    async def test_rejected_token_message_does_not_echo_the_token(self):
        bad_token = "fake_token_WRONG_TOKEN_must_never_be_printed"
        client = self.make(bad_token)
        with self.assertRaises(UpstreamError) as ctx:
            await client.get_json(["repos", "octocat", "Hello-World"])
        self.assertIn("rejected the configured token", str(ctx.exception))
        self.assertNotIn(bad_token, str(ctx.exception))

    async def test_redirects_are_not_followed(self):
        client = self.make(VALID_TOKEN)
        with self.assertRaises(UpstreamError):
            await client.get_json(["repos", "octocat", "moved"])
        self.assertEqual(self.fake.request_count, 1)  # no second hop that could carry the token elsewhere


if __name__ == "__main__":
    unittest.main()
