#!/usr/bin/env python3
"""Privacy, failure isolation and SQL regressions for harness telemetry."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_telemetry as telemetry


class TelemetryTests(unittest.TestCase):
    def test_dry_run_events_do_not_claim_a_commit(self):
        for operation in ("commit-dry-run-attempt", "commit-dry-run"):
            statement = telemetry.event_sql("issue-95-example", operation, 0, 1, {}, "worker1")
            self.assertIn("'" + operation + "'", statement)
            self.assertNotIn("commit_hash", statement)
            with self.assertRaises(ValueError):
                telemetry.event_sql("issue-95-example", operation, 0, 1, {}, commit_hash="a" * 40)

    def test_commit_and_push_metadata_is_bounded(self):
        for operation in ("commit", "push"):
            statement = telemetry.event_sql(
                "issue-89-example", operation, 0, 42, {}, "worker1", "a" * 40
            )
            self.assertIn('"commit_hash": "' + "a" * 40, statement)
            self.assertIn('"slot": "worker1"', statement)
        with self.assertRaises(ValueError):
            telemetry.event_sql("issue-89-example", "commit", 0, 1, {}, commit_hash="message text")

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
                patch.object(telemetry, "storage_allowed", return_value=True),
                contextlib.redirect_stderr(io.StringIO()) as output,
            ):
                telemetry.record("issue-82-example", "down", 9, 1)
            self.assertIn("event not recorded", output.getvalue())
            self.assertNotIn("secret", output.getvalue())

    def test_sql_transport_is_bounded_and_not_a_shell(self):
        with (
            patch.object(telemetry.subprocess, "run") as run,
            patch.object(telemetry, "storage_allowed", return_value=True),
        ):
            run.return_value.returncode = 0
            telemetry.sql("SELECT 1;")
        args, kwargs = run.call_args
        self.assertIn("psql", args[0])
        self.assertEqual(kwargs["input"], "SELECT 1;")
        self.assertEqual(kwargs["timeout"], 3)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("POSTGRES_PASSWORD", kwargs["env"])

    def test_slot_is_operational_metadata_separate_from_session(self):
        statement = telemetry.event_sql("issue-85-example", "start-task", 0, None, {}, "worker1")
        self.assertIn('{"slot":"worker1"}', statement)
        self.assertNotIn("INSERT INTO agent_sessions", statement)
        with self.assertRaises(ValueError):
            telemetry.event_sql("issue-85-example", "start-task", 0, None, {}, "../worker1")

    def test_start_task_records_client_separately_from_slot(self):
        for client in ("codex", "claude", "kimi", "future-client"):
            with self.subTest(client=client):
                statement = telemetry.event_sql(
                    "issue-100-example",
                    "start-task",
                    0,
                    None,
                    {},
                    slot="worker2",
                    client=client,
                )
                self.assertIn(f'"client":"{client}"', statement)
                self.assertIn('"slot":"worker2"', statement)
        for client in ("Kimi", "../kimi", "kimi_client"):
            with self.subTest(client=client), self.assertRaises(ValueError):
                telemetry.event_sql("issue-100-example", "start-task", 0, None, {}, client=client)
        with self.assertRaises(ValueError):
            telemetry.event_sql("issue-100-example", "up", 0, None, {}, client="kimi")

    def test_test_repositories_never_reach_shared_storage(self):
        # A copied script sees no compose.telemetry.yaml; Docker would still resolve
        # the fixed project name, so the guard must refuse before any subprocess.
        with tempfile.TemporaryDirectory() as base:
            for root, environ in (
                (Path(base), {}),
                (telemetry.ROOT, {"BSS_TELEMETRY_DISABLED": "1"}),
            ):
                with (
                    patch.object(telemetry, "ROOT", root),
                    patch.dict(os.environ, environ),
                    patch.object(telemetry.subprocess, "run") as run,
                ):
                    if "BSS_TELEMETRY_DISABLED" not in environ:
                        os.environ.pop("BSS_TELEMETRY_DISABLED", None)
                    with self.assertRaises(RuntimeError):
                        telemetry.sql("SELECT 1;")
                    with self.assertRaises(RuntimeError):
                        telemetry.compose("ps")
                    telemetry.record("issue-82-example", "down", 0, 1)
                run.assert_not_called()

    def test_real_checkout_reaches_storage(self):
        with patch.dict(os.environ, {}), patch.object(telemetry.subprocess, "run") as run:
            os.environ.pop("BSS_TELEMETRY_DISABLED", None)
            run.return_value.returncode = 0
            telemetry.sql("SELECT 1;")
        run.assert_called_once()

    def test_prices_are_validated_and_upserted(self):
        price = {
            "model": "claude-opus-5-5",
            "effective_from": "2026-10-06",
            "currency": "USD",
            "input_per_mtok": "4.00",
            "cache_read_per_mtok": "0.20",
            "cache_write_per_mtok": "5.00",
            "output_per_mtok": "20.00",
            "source": "test",
        }
        statement = telemetry.prices_sql({"prices": [price]})
        self.assertIn("ON CONFLICT (model, effective_from) DO UPDATE", statement)
        self.assertIn("'2026-10-06'::date", statement)
        for bad in (
            {"input_per_mtok": "4.0; DROP TABLE tasks"},
            {"input_per_mtok": 4.0},
            {"model": "x'y"},
            {"currency": "usd"},
            {"extra": "1"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                telemetry.prices_sql({"prices": [{**price, **bad}]})
        shipped = json.loads((telemetry.ROOT / "scripts/telemetry/prices.json").read_text())
        self.assertTrue(telemetry.prices_sql(shipped))


if __name__ == "__main__":
    unittest.main(verbosity=2)
