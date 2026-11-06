"""Usage provenance, missing data, privacy and report regressions."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_usage as usage


def sample(**changes):
    return {
        "version": 1,
        "task_id": "issue-92-example",
        "sample_id": "request-1",
        "provider": "example",
        "client": "test-client",
        "model": "test-model",
        "session_id": "session-1",
        "source": "provider-export-v1",
        "kind": "delta",
        "sampled_at": "2026-10-31T12:00:00Z",
        "input_tokens": 1000,
        "cached_input_tokens": 200,
        "output_tokens": 50,
        "total_tokens": 1050,
        **changes,
    }


class UsageTests(unittest.TestCase):
    def test_report_make_target_runs_the_report_action(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python3"
            executable.write_text(
                f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
            )
            executable.chmod(0o700)
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
            }
            env["PATH"] = directory + os.pathsep + env["PATH"]
            root = Path(__file__).resolve().parent.parent
            result = subprocess.run(  # noqa: S603 - fixed Make target in a disposable fixture
                ["make", "--no-print-directory", "telemetry-report"],  # noqa: S607
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(result.stdout.splitlines()[-1]),
                ["-B", "scripts/task_usage.py", "report"],
            )

    def test_complete_usage_preserves_counts(self):
        self.assertEqual(usage.validate(sample())["total_tokens"], 1050)

    def test_minimal_sample_leaves_tokens_and_model_null(self):
        minimal = {k: v for k, v in sample().items() if k not in (*usage.TOKENS, "model")}
        self.assertTrue(all(usage.validate(minimal)[k] is None for k in usage.TOKENS))
        self.assertIsNone(usage.validate(minimal)["model"])

    def test_sql_carries_no_cost_columns(self):
        statement = usage.usage_sql(sample())
        for column in ("estimated_cost", "currency", "pricing_metadata"):
            self.assertNotIn(column, statement.split("INSERT INTO usage_samples")[1])

    def test_invalid_and_content_bearing_exports_are_rejected(self):
        for changes in (
            {"input_tokens": -1},
            {"input_tokens": True},
            {"input_tokens": 2**63},
            {"cached_input_tokens": 1001},
            {"total_tokens": 1049},
            {"kind": "cumulative"},
            {"kind": "unavailable"},
            {"sampled_at": "2026-10-31T12:00:00"},
            {"task_id": "master"},
            {"session_id": "private prompt text"},
            {"prompt": "secret"},
            {"response": "secret"},
            {"estimated_cost": "123"},
            {"environment": {"SECRET": "private"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                usage.validate(sample(**changes))

    def test_duplicates_are_serialized_and_conflicts_fail_instead_of_overwriting(self):
        statement = usage.usage_sql(sample())
        self.assertEqual(statement, usage.usage_sql(copy.deepcopy(sample())))
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn("ON CONFLICT DO NOTHING", statement)
        self.assertIn("conflicting usage sample", statement)
        self.assertNotIn("DO UPDATE", statement)
        self.assertNotIn("INSERT INTO tasks", statement)
        for identity in ("provider", "client", "session_id", "source", "sample_id"):
            self.assertNotEqual(
                statement,
                usage.usage_sql(sample(**{identity: "different"})),
            )

    def test_database_failures_do_not_leak_content(self):
        for error in (
            RuntimeError("sensitive database output"),
            OSError("private path"),
            subprocess.TimeoutExpired("secret", 3),
        ):
            with (
                patch("sys.argv", ["task_usage.py", "report"]),
                patch.object(usage, "sql", side_effect=error),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(usage.main(), 1)
                self.assertEqual(
                    stderr.getvalue(), "telemetry: usage report failed; check storage\n"
                )

    def test_report_cli(self):
        with (
            patch("sys.argv", ["task_usage.py", "report"]),
            patch.object(usage, "sql", return_value='{"tasks":[],"events":[],"usage":[]}'),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(usage.main(), 0)
            self.assertEqual(json.loads(output.getvalue()), {"tasks": [], "completed_tasks": []})

    def test_report_known_fixtures_nullable_totals_and_multiple_currencies(self):
        task = "issue-92-example"

        def event(kind, second, result=0, **extra):
            return {
                "task_id": task,
                "event_type": kind,
                "result": result,
                "slot": "worker3",
                "occurred_at": f"2026-10-31T12:00:{second:02d}Z",
                **extra,
            }

        row = {
            "task_id": task,
            "provider": "example",
            "client": "test-client",
            "model": "test-model",
            **{k: sample()[k] for k in usage.TOKENS},
            "currency": "USD",
            "estimated_cost": "0.0021000000",
        }
        data = {
            "tasks": [
                {
                    "task_id": task,
                    "started_at": "2026-10-31T12:00:00Z",
                    "ended_at": None,
                    "outcome": "completed",
                },
                {
                    "task_id": "issue-93-empty",
                    "started_at": "2026-10-31T12:00:00Z",
                    "ended_at": None,
                    "outcome": None,
                },
            ],
            "events": [
                event("check", 1, 1),
                event("check", 2),
                event("create-pr", 3),
                event("check-pr", 4, 1, ci_state="failed"),
                event("check-pr", 5, ci_state="passed"),
                event("merge-task", 7),
            ],
            "usage": [row, {**row, "estimated_cost": "0.1", "currency": "EUR"}],
        }
        result = usage.report(data)
        t = result["tasks"][0]
        self.assertTrue(t["merged"])
        self.assertEqual(t["tokens"]["total_tokens"], 2100)
        self.assertEqual(t["known_cost_by_currency"], {"USD": "0.0021000000", "EUR": "0.1"})
        self.assertTrue(t["cost_complete"])
        self.assertEqual(
            [
                t[k]
                for k in ("time_to_pr_seconds", "time_to_green_ci_seconds", "time_to_merge_seconds")
            ],
            [3, 5, 7],
        )
        self.assertEqual(t["validation_retries"], 1)
        self.assertEqual(t["validation_failures"], 1)
        self.assertEqual(t["ci_failed_observations"], 1)
        self.assertIsNone(t["ci_retries"])
        self.assertEqual(result["completed_tasks"][0]["count"], 1)
        empty = result["tasks"][1]
        self.assertIsNone(empty["tokens"]["total_tokens"])
        self.assertFalse(empty["cost_complete"])
        self.assertIsNone(empty["time_to_merge_seconds"])
        data["usage"].append({**row, "total_tokens": None, "estimated_cost": None})
        t = usage.report(data)["tasks"][0]
        self.assertIsNone(t["tokens"]["total_tokens"])
        self.assertFalse(t["cost_complete"])
        self.assertEqual(t["known_cost_by_currency"]["USD"], "0.0021000000")

    def test_list_price_cost_is_exact_and_unknowns_stay_unpriced(self):
        task = "issue-232-cost"
        price = {
            "model": "claude-opus-5-5",
            "effective_from": "2026-10-06",
            "currency": "USD",
            "input_per_mtok": "4.0000",
            "cache_read_per_mtok": "0.2000",
            "cache_write_per_mtok": "5.0000",
            "output_per_mtok": "20.0000",
            "source": "test",
        }
        row = {
            "task_id": task,
            "provider": "anthropic",
            "client": "claude",
            "model": "claude-opus-5-5",
            "sampled_at": "2026-11-01T10:00:00Z",
            # 1M inclusive input: 600k reads, 100k writes, 300k uncached; 10k output.
            "input_tokens": 1_000_000,
            "cached_input_tokens": 600_000,
            "cache_creation_tokens": 100_000,
            "output_tokens": 10_000,
            "total_tokens": 1_010_000,
            "estimated_cost": None,
            "currency": None,
        }
        data = {
            "tasks": [
                {
                    "task_id": task,
                    "started_at": "2026-11-01T09:00:00Z",
                    "ended_at": None,
                    "outcome": None,
                }
            ],
            "events": [],
            "usage": [row],
            "prices": [price],
        }
        t = usage.report(data)["tasks"][0]
        # 0.3*4 + 0.6*0.2 + 0.1*5 + 0.01*20 = 1.2 + 0.12 + 0.5 + 0.2
        self.assertEqual(t["known_cost_by_currency"], {"USD": "2.0200000000"})
        self.assertTrue(t["cost_complete"])
        self.assertEqual(t["pricing_provenance"][0]["source"], "test")
        for change in (
            {"cache_creation_tokens": None},
            {"model": "unpriced-model"},
            {"sampled_at": "2026-10-01T10:00:00Z"},
        ):
            with self.subTest(change=change):
                data["usage"] = [{**row, **change}]
                t = usage.report(data)["tasks"][0]
                self.assertEqual(t["known_cost_by_currency"], {})
                self.assertFalse(t["cost_complete"])
                self.assertTrue(t["unpriced_models"])

    def test_friction_prompts_active_time_and_facts(self):
        task = "issue-232-effect"

        def event(kind, result=0, **extra):
            return {
                "task_id": task,
                "event_type": kind,
                "result": result,
                "occurred_at": "2026-11-01T10:00:00Z",
                **extra,
            }

        data = {
            "tasks": [
                {
                    "task_id": task,
                    "started_at": "2026-11-01T09:00:00Z",
                    "ended_at": None,
                    "outcome": None,
                }
            ],
            "events": [
                event("commit", 1),
                event("commit", 1),
                event("commit"),
                event("create-pr", 1),
                event("check-pr", 1, ci_state="pending"),
                event("check-pr", 1, ci_state="failed"),
                event("run", 1),
            ],
            "collections": [
                {
                    "task_id": task,
                    "client": "claude",
                    "status": "complete",
                    "human_prompts": 3,
                    "active_ms": 90000,
                },
                {
                    "task_id": task,
                    "client": "claude",
                    "status": "complete",
                    "human_prompts": 0,
                    "active_ms": 30000,
                },
                {
                    "task_id": task,
                    "client": "claude",
                    "status": "partial",
                    "human_prompts": None,
                    "active_ms": None,
                },
            ],
            "usage": [],
            "facts": [
                {
                    "task_id": task,
                    "pr_number": 9,
                    "additions": 10,
                    "deletions": 2,
                    "captured_at": "x",
                }
            ],
        }
        t = usage.report(data)["tasks"][0]
        self.assertEqual(t["friction"], {"check-pr-ci-failed": 1, "commit": 2, "create-pr": 1})
        self.assertEqual(t["human_prompts"], 3)
        self.assertEqual(t["agent_active_seconds"], 120)
        self.assertEqual(t["facts"], {"pr_number": 9, "additions": 10, "deletions": 2})
        for c in data["collections"]:
            c.update(human_prompts=None, active_ms=None)
        data["facts"] = []
        t = usage.report(data)["tasks"][0]
        self.assertIsNone(t["human_prompts"])  # unknown, never zero
        self.assertIsNone(t["agent_active_seconds"])
        self.assertIsNone(t["facts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
