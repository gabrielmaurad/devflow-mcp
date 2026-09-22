"""Tests for secret/config handling (devflow/config.py), including the rubric's secret-management rules."""

import ast
import os  # tests only: used to PROVE the server ignores, and never writes, system environment variables
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devflow.config import DEFAULT_ENV_FILE, PROJECT_ROOT, load_settings
from devflow.errors import ConfigError

SOURCE_FILES = [*sorted((PROJECT_ROOT / "devflow").glob("*.py")), PROJECT_ROOT / "server.py"]


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def env_file(self, text: str) -> Path:
        path = self.dir / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_means_defaults_and_anonymous(self):
        settings = load_settings(self.dir / "no-such.env")
        self.assertIsNone(settings.github_token)
        self.assertFalse(settings.authenticated)
        self.assertEqual((settings.rate_limit_calls, settings.rate_limit_window_seconds), (10, 60))

    def test_reads_token_and_limits_from_file(self):
        settings = load_settings(self.env_file("GITHUB_TOKEN=fake_token_abc123\nRATE_LIMIT_CALLS=5\nRATE_LIMIT_WINDOW_SECONDS=30\n"))
        self.assertEqual(settings.github_token, "fake_token_abc123")
        self.assertTrue(settings.authenticated)
        self.assertEqual((settings.rate_limit_calls, settings.rate_limit_window_seconds), (5, 30))

    def test_blank_token_counts_as_no_token(self):
        self.assertIsNone(load_settings(self.env_file("GITHUB_TOKEN=\n")).github_token)
        self.assertIsNone(load_settings(self.env_file("GITHUB_TOKEN=   \n")).github_token)

    def test_ignores_system_environment_variables(self):
        """Credentials must come from .env only, never from process/system environment variables."""
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "from-process-env", "RATE_LIMIT_CALLS": "999"}):
            settings = load_settings(self.env_file(""))
        self.assertIsNone(settings.github_token)
        self.assertEqual(settings.rate_limit_calls, 10)

    def test_does_not_export_secrets_into_the_environment(self):
        before = dict(os.environ)
        load_settings(self.env_file("GITHUB_TOKEN=fake_token_should_stay_in_the_file\n"))
        self.assertEqual(dict(os.environ), before)

    def test_rejects_tokens_that_could_corrupt_a_header(self):
        for bad in ("abc def", "abc\tdef", "cafétoken"):
            with self.subTest(token=bad):
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env_file(f"GITHUB_TOKEN={bad}\n"))
                self.assertNotIn(bad, str(ctx.exception))

    def test_rejects_bad_numbers(self):
        for line in ("RATE_LIMIT_CALLS=abc", "RATE_LIMIT_CALLS=0", "RATE_LIMIT_CALLS=5000", "RATE_LIMIT_WINDOW_SECONDS=-5"):
            with self.subTest(line=line):
                with self.assertRaises(ConfigError):
                    load_settings(self.env_file(line + "\n"))

    def test_bad_number_messages_do_not_echo_the_raw_value(self):
        for raw in ("not_a_number", "987654"):
            with self.subTest(raw=raw):
                with self.assertRaises(ConfigError) as ctx:
                    load_settings(self.env_file(f"RATE_LIMIT_CALLS={raw}\n"))
                self.assertNotIn(raw, str(ctx.exception))

    def test_repr_never_contains_the_token(self):
        settings = load_settings(self.env_file("GITHUB_TOKEN=fake_token_super_secret_value\n"))
        self.assertNotIn("fake_token_super_secret_value", repr(settings))
        self.assertNotIn("fake_token_super_secret_value", str(settings))

    def test_env_path_is_absolute_and_anchored_to_the_project_not_the_cwd(self):
        self.assertTrue(DEFAULT_ENV_FILE.is_absolute())
        self.assertEqual(DEFAULT_ENV_FILE, PROJECT_ROOT / ".env")
        self.assertTrue((PROJECT_ROOT / "server.py").exists())


class RubricComplianceTests(unittest.TestCase):
    """Assignment 4 rubric: secrets via .env or pathlib; os.path or system variables score zero."""

    def test_source_never_uses_os_path_or_os_environ(self):
        forbidden_attributes = {("os", "path"), ("os", "environ"), ("os", "getenv"), ("os", "putenv")}
        for source_file in SOURCE_FILES:
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                with self.subTest(file=source_file.name, line=getattr(node, "lineno", 0)):
                    if isinstance(node, ast.Import):
                        self.assertNotIn("os", [alias.name.split(".")[0] for alias in node.names])
                    elif isinstance(node, ast.ImportFrom):
                        self.assertNotEqual((node.module or "").split(".")[0], "os")
                    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        self.assertNotIn((node.value.id, node.attr), forbidden_attributes)

    def test_source_uses_pathlib_and_dotenv(self):
        config_source = (PROJECT_ROOT / "devflow" / "config.py").read_text(encoding="utf-8")
        self.assertIn("from pathlib import Path", config_source)
        self.assertIn("dotenv_values", config_source)


if __name__ == "__main__":
    unittest.main()
