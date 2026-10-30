#!/usr/bin/env python3
"""Privacy, failure isolation and SQL regressions for harness telemetry."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

import task_telemetry as telemetry


class TelemetryTests(unittest.TestCase):
    def test_only_operational_identity_is_collected(self):
        statement = telemetry.event_sql(
            "issue-82-example",
            "up",
            0,
            12,
            {
                "POSTGRES_PASSWORD": "never-store-this",
                "PROMPT": "private prompt",
                "MODEL": "unverified-model",
            },
        )
        self.assertNotIn("never-store-this", statement)
        self.assertNotIn("private prompt", statement)
        self.assertNotIn("unverified-model", statement)
        self.assertNotIn("INSERT INTO usage_samples", statement)
        self.assertIn("'up', 0, 12", statement)
        self.assertNotIn("INSERT INTO agent_sessions", statement)

    def test_multiple_sessions_for_one_task(self):
        for identity in (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ):
            statement = telemetry.event_sql(
                "issue-82-example", "run", 7, 500, {"CODEX_THREAD_ID": identity}
            )
            self.assertIn(identity, statement)
            self.assertIn("ON CONFLICT DO NOTHING", statement)
            self.assertIn("'codex'", statement)

    def test_untrusted_payloads_are_rejected(self):
        for task, operation in (
            ("issue-82-x'; DROP TABLE tasks", "up"),
            ("issue-82-example", "arbitrary command contents"),
        ):
            with self.assertRaises(ValueError):
                telemetry.event_sql(task, operation, 0, 1, {})
        with self.assertRaises(ValueError):
            telemetry.event_sql("issue-82-example", "up", 0, 1, {"CODEX_THREAD_ID": "prompt text"})

    def test_failure_is_visible_and_does_not_leak_output(self):
        for error in (
            RuntimeError("secret"),
            OSError("secret"),
            subprocess.TimeoutExpired("secret", 3),
        ):
            with (
                patch.object(telemetry, "sql", side_effect=error),
                contextlib.redirect_stderr(io.StringIO()) as output,
            ):
                telemetry.record("issue-82-example", "down", 9, 1)
            self.assertIn("event not recorded", output.getvalue())
            self.assertNotIn("secret", output.getvalue())

    def test_sql_transport_is_bounded_and_not_a_shell(self):
        with patch.object(telemetry.subprocess, "run") as run:
            run.return_value.returncode = 0
            telemetry.sql("SELECT 1;")
        args, kwargs = run.call_args
        self.assertIn("psql", args[0])
        self.assertEqual(kwargs["input"], "SELECT 1;")
        self.assertEqual(kwargs["timeout"], 3)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("POSTGRES_PASSWORD", kwargs["env"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
