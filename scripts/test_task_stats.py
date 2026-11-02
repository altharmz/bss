"""The two issue comments preserve the starting checkpoint and show observed deltas."""

import json
import unittest
from unittest.mock import patch

import task_stats as stats
from task_usage import TOKENS

TASK = "issue-124-usage"


def snapshot():
    return dict(
        task_id=TASK,
        usage_identities=[dict(provider="openai", client="codex", model="m")],
        tokens=dict.fromkeys(TOKENS, 50),
        sample_count=1,
        cost_complete=False,
        known_cost_by_currency={},
        missing_clients=[],
        collection_complete=False,
        collection_status=[
            dict(
                client="codex",
                external_session_id="session-1",
                status="partial",
                reason="session_open",
                baseline_tokens=dict.fromkeys(TOKENS, 10),
                latest_tokens=dict.fromkeys(TOKENS, 60),
            )
        ],
    )


class StatisticsTests(unittest.TestCase):
    def test_paginated_comment_transport_supports_older_github_cli(self):
        with patch.object(stats.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '[{"id":1}]\n[{"id":2}]\n'
            self.assertEqual(stats.github("test-route", "--paginate"), [[{"id": 1}], [{"id": 2}]])
            self.assertNotIn("--slurp", run.call_args.args[0])

    def test_github_transport_supplies_json_input_flag_only_for_writes(self):
        for payload in (None, {"body": "usage\nstatistics"}):
            with patch.object(stats.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "{}"
                stats.github("test-route", payload=payload)
                args, kwargs = run.call_args
                self.assertEqual("--input" in args[0], payload is not None)
                if payload is not None:
                    self.assertEqual(args[0][-2:], ["--input", "-"])
                    self.assertEqual(json.loads(kwargs["input"]), payload)

    def test_start_end_and_difference_are_distinct_from_pricing(self):
        for phase in ("start", "pr"):
            body = stats.render(snapshot(), phase, 125 if phase == "pr" else None)
            self.assertIn("| input_tokens | 10 | 60 | 50 |", body)
            self.assertIn("Pricing complete: false", body)
            self.assertIn("Known estimated cost: unknown", body)
            self.assertIn(f"<!-- bss-agent-usage:{TASK}:{phase} -->", body)
        self.assertIn("PR: #125", body)

    def test_missing_counters_are_not_fabricated_zeroes(self):
        data = snapshot()
        data["collection_status"][0]["baseline_tokens"] = None
        body = stats.render(data, "start")
        self.assertIn("| input_tokens | unknown | 60 | unknown |", body)

    def test_start_snapshot_is_immutable_across_resume(self):
        data = snapshot()
        with patch.object(stats, "sql", return_value=json.dumps([{"stats": data}])) as sql:
            self.assertEqual(stats.snapshot(TASK, "start"), data)
        self.assertEqual(sql.call_count, 1)

    def test_comment_creation_and_duplicate_retry_are_idempotent(self):
        data = snapshot()
        url = "https://github.com/altharmz/bss/issues/124#issuecomment-123"
        with patch.object(stats, "snapshot", return_value=data):
            with patch.object(
                stats, "github", side_effect=[{"login": "agent"}, [[]], {"html_url": url}]
            ) as github:
                self.assertEqual(stats.post(TASK, "start"), url)
                body = github.call_args.kwargs["payload"]["body"]
            existing = dict(id=123, html_url=url, user={"login": "agent"}, body=body)
            with patch.object(
                stats, "github", side_effect=[{"login": "agent"}, [[existing]]]
            ) as github:
                self.assertEqual(stats.post(TASK, "start"), url)
                self.assertEqual(github.call_count, 2)

    def test_pr_comment_updates_same_checkpoint_and_leaves_other_authors_alone(self):
        data = snapshot()
        url = "https://github.com/altharmz/bss/issues/124#issuecomment-123"
        old = dict(
            id=123,
            html_url=url,
            user={"login": "agent"},
            body=f"<!-- bss-agent-usage:{TASK}:pr -->\nold stats",
        )
        other = {**old, "id": 456, "user": {"login": "human"}}
        with patch.object(stats, "snapshot", return_value=data):
            with patch.object(
                stats, "github", side_effect=[{"login": "agent"}, [[other, old]], {"html_url": url}]
            ) as github:
                self.assertEqual(stats.post(TASK, "pr", 125), url)
                self.assertEqual(github.call_args.args[0], "repos/altharmz/bss/issues/comments/123")
                self.assertIn("PATCH", github.call_args.args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
