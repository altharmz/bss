"""Usage provenance, exact pricing, missing data, privacy and report regressions."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import ROUND_UP, localcontext
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


def pricing(**changes):
    # Synthetic fixture rates, never a claim about a provider's prices.
    return {
        "version": "fixture-v1",
        "provider": "example",
        "model": "test-model",
        "currency": "USD",
        "source_url": "https://example.com/pricing",
        "effective_from": "2026-10-01T00:00:00Z",
        "effective_until": "2026-11-01T00:00:00Z",
        "input_per_million": "2",
        "cached_input_per_million": "0.5",
        "output_per_million": "8",
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

    def test_cost_rounding_is_independent_of_callers_decimal_context(self):
        # Exact cost 0.00000000005 must round half-even to zero, even under ROUND_UP.
        s = usage.validate(
            sample(input_tokens=1, cached_input_tokens=0, output_tokens=0, total_tokens=1)
        )
        with localcontext() as ctx:
            ctx.rounding = ROUND_UP
            self.assertEqual(
                usage.price(s, pricing(input_per_million="0.00005"))[0], "0.0000000000"
            )

    def test_complete_usage_preserves_counts_and_prices_inclusive_cache_exactly(self):
        s = usage.validate(sample())
        self.assertEqual(s["total_tokens"], 1050)
        cost, currency, metadata = usage.price(s, pricing())
        self.assertEqual(cost, "0.0021000000")
        self.assertEqual(currency, "USD")
        self.assertEqual(json.loads(metadata), pricing())

    def test_partial_unavailable_and_no_pricing_stay_null(self):
        for s in (
            sample(cached_input_tokens=None),
            sample(output_tokens=None),
            sample(kind="unavailable", **dict.fromkeys(usage.TOKENS)),
        ):
            self.assertIsNone(usage.price(usage.validate(s), pricing())[0])
        self.assertEqual(usage.price(usage.validate(sample()), None), (None, None, None))
        minimal = {k: v for k, v in sample().items() if k not in (*usage.TOKENS, "model")}
        self.assertTrue(all(usage.validate(minimal)[k] is None for k in usage.TOKENS))
        self.assertIsNone(usage.validate(minimal)["model"])

    def test_pricing_versions_are_preserved_and_not_applied_outside_validity(self):
        first = usage.usage_sql(sample(), pricing())
        second = usage.usage_sql(
            sample(sample_id="request-2"), pricing(version="fixture-v2", input_per_million="4")
        )
        self.assertIn("fixture-v1", first)
        self.assertIn("0.0021000000", first)
        self.assertIn("fixture-v2", second)
        self.assertIn("0.0037000000", second)
        for changes in (
            {"model": "different"},
            {"provider": "other"},
            {"effective_until": "2026-10-31T12:00:00Z"},
            {"effective_from": "2026-11-01T00:00:00Z"},
            {"input_per_million": 2.0},
            {"input_per_million": "NaN"},
            {"source_url": "https://user:password@example.com/"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                usage.price(usage.validate(sample()), pricing(**changes))

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
        statement = usage.usage_sql(sample(), pricing())
        self.assertEqual(statement, usage.usage_sql(copy.deepcopy(sample()), pricing()))
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn("ON CONFLICT DO NOTHING", statement)
        self.assertIn("conflicting usage sample", statement)
        self.assertNotIn("DO UPDATE", statement)
        self.assertNotIn("INSERT INTO tasks", statement)
        for identity in ("provider", "client", "session_id", "source", "sample_id"):
            self.assertNotEqual(
                statement,
                usage.usage_sql(
                    sample(**{identity: "different"}),
                    pricing() if identity != "provider" else pricing(provider="different"),
                ),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
