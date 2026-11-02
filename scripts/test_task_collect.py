"""Machine-format fixtures, task boundaries, coverage and retry regressions."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import task_collect as collect
from task_usage import report

TASK = "issue-124-usage"
SID = "00000000-0000-0000-0000-000000000001"
PRIVATE = "private-conversation-must-never-leave-parser"


def when(second):
    return f"2026-11-02T12:00:{second:02d}Z"


def tokens(inp=100, cached=40, out=10):
    return dict(
        input_tokens=inp, cached_input_tokens=cached, output_tokens=out, total_tokens=inp + out
    )


def fixture(client):
    if client == "codex":

        def usage(second, inp, cached, out):
            return dict(
                timestamp=when(second),
                type="event_msg",
                payload=dict(
                    type="token_count",
                    info=dict(
                        total_token_usage=tokens(inp, cached, out),
                        last_token_usage=tokens(999, 0, 1),
                    ),
                ),
            )

        return [
            dict(
                type="session_meta",
                payload=dict(id=SID, model_provider="openai", base_instructions=PRIVATE),
            ),
            dict(type="turn_context", payload=dict(model="model-1", prompt=PRIVATE)),
            usage(1, 100, 40, 10),
            usage(2, 150, 60, 15),
            usage(2, 150, 60, 15),
            dict(type="turn_context", payload=dict(model="model-2")),
            usage(3, 230, 80, 20),
            dict(timestamp=when(4), type="event_msg", payload=dict(type="task_complete")),
        ]
    if client == "claude":

        def usage(second, mid, model, inp, cached, out, stop):
            return dict(
                type="assistant",
                timestamp=when(second),
                sessionId=SID,
                message=dict(
                    id=mid,
                    model=model,
                    content=PRIVATE,
                    stop_reason=stop,
                    usage=dict(
                        input_tokens=inp,
                        cache_read_input_tokens=cached,
                        cache_creation_input_tokens=20,
                        output_tokens=out,
                    ),
                ),
            )

        first = usage(2, "msg-1", "model-1", 30, 20, 5, "tool_use")
        return [
            usage(1, "msg-0", "model-1", 1, 40, 1, None),
            first,
            {**first, "timestamp": when(3)},
            usage(3, "msg-2", "model-2", 60, 20, 5, "end_turn"),
        ]
    epoch = 1793620800000
    return [
        dict(type="metadata", protocol_version="1.5"),
        dict(type="llm.request", provider="openai", model="model-1", time=epoch + 1000),
        dict(
            type="usage.record",
            agentId="main",
            model="kimi/model-1",
            usageScope="turn",
            time=epoch + 2000,
            usage=dict(inputOther=30, inputCacheRead=20, inputCacheCreation=0, output=5),
        ),
        dict(type="token_counting.measured", tokens=999999, time=epoch + 2000),
        dict(type="llm.request", provider="openai", model="model-2", time=epoch + 2001),
        dict(
            type="usage.record",
            agentId="main",
            model="kimi/model-2",
            usageScope="turn",
            time=epoch + 3000,
            usage=dict(inputOther=60, inputCacheRead=20, inputCacheCreation=0, output=5),
        ),
        dict(type="turn.ended", time=epoch + 4000, content=PRIVATE),
    ]


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()

    def source(self, client, rows=None, session=SID):
        path = (
            self.home
            / {
                "codex": f".codex/sessions/2026/11/02/rollout-{session}.jsonl",
                "claude": f".claude/projects/project/{session}.jsonl",
                "kimi": f".kimi-code/sessions/workspace/session_{session}/agents/main/wire.jsonl",
            }[client]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in (rows or fixture(client))))
        return path

    def row(self, client, **extra):
        return dict(
            task_id=TASK,
            client=client,
            external_session_id=SID,
            bound_at=when(1),
            closed_at=when(3),
            source=collect.CLIENTS[client],
            **extra,
        )

    def test_machine_adapters_models_deduplication_and_privacy(self):
        for client in collect.CLIENTS:
            with self.subTest(client=client):
                source = self.source(client)
                items, ended, reason = collect.project(client, source, SID)
                self.assertIsNone(reason)
                self.assertTrue(ended)
                self.assertNotIn(PRIVATE, json.dumps(items))
                self.assertEqual(len(items), 3 if client == "codex" else 2)
                if client == "codex":
                    self.assertEqual([x["input_tokens"] for x in items], [100, 50, 80])
                    self.assertEqual([x["model"] for x in items], ["model-1", "model-1", "model-2"])
                if client == "claude":
                    self.assertEqual(items[0]["input_tokens"], 70)
                    self.assertEqual(items[0]["provider"], "unknown")
                if client == "kimi":
                    self.assertEqual(sum(x["input_tokens"] for x in items), 130)

    def test_collection_idempotent_sql_and_final_coverage_for_every_client(self):
        for client in collect.CLIENTS:
            self.source(client)
            with patch.object(collect, "sql") as sql:
                first = collect.collect_one(self.row(client), home=self.home)
                statement = sql.call_args.args[0]
                second = collect.collect_one(self.row(client), home=self.home)
                self.assertEqual(statement, sql.call_args.args[0])
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "complete", first)
            self.assertNotIn(PRIVATE, statement)
            self.assertIn("ON CONFLICT DO NOTHING", statement)
            self.assertTrue(statement.startswith("BEGIN;"))
            self.assertTrue(statement.endswith("COMMIT;"))

    def test_unrelated_requests_and_new_issue_boundary_are_excluded(self):
        self.source("codex")
        row = self.row("codex", next_bound_at=when(3))
        row["bound_at"] = when(2)
        with patch.object(collect, "sql") as sql:
            result = collect.collect_one(row, home=self.home)
        statement = sql.call_args.args[0]
        self.assertEqual(result["observed_samples"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("'counter-2'", statement)
        self.assertNotIn("'counter-6'", statement)

    def test_live_final_pending_and_unavailable_remain_visible(self):
        rows = fixture("codex")[:-1]
        self.source("codex", rows)
        with patch.object(collect, "sql"):
            result = collect.collect_one(self.row("codex"), home=self.home)
        self.assertEqual((result["status"], result["reason"]), ("partial", "final_counter_pending"))
        for client in ("claude", "kimi"):
            with patch.object(collect, "sql") as sql:
                result = collect.collect_one(self.row(client), home=self.home)
            self.assertEqual(result["reason"], "source_missing")
            self.assertIn("'unavailable'", sql.call_args.args[0])

    def test_counter_reset_and_missing_counts_never_create_zero_usage(self):
        rows = fixture("codex")
        rows[6]["payload"]["info"]["total_token_usage"] = tokens(1, 0, 0)
        self.source("codex", rows)
        with patch.object(collect, "sql"):
            result = collect.collect_one(self.row("codex"), home=self.home)
        self.assertEqual((result["status"], result["reason"]), ("partial", "counter_reset"))
        rows = fixture("kimi")
        del rows[2]["usage"]["inputCacheRead"]
        source = self.source("kimi", rows)
        items, _, _ = collect.project("kimi", source, SID)
        self.assertIsNone(items[0]["input_tokens"])
        self.assertIsNone(items[0]["cached_input_tokens"])

    def test_torn_append_recovery_and_malformed_complete_line(self):
        source = self.source("codex")
        before = collect.project("codex", source, SID)
        with source.open("a") as output:
            output.write('{"private":"unfinished')
        self.assertEqual(before, collect.project("codex", source, SID))
        with source.open("a") as output:
            output.write("\n")
        with self.assertRaisesRegex(collect.CollectionError, "source_invalid"):
            collect.project("codex", source, SID)

    def test_rebinding_and_handoff_preserve_original_boundary(self):
        with patch.object(collect, "sql") as sql:
            collect.bind(TASK, "codex", SID)
        statement = sql.call_args.args[0]
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn("task_id<>", statement)
        self.assertIn("ON CONFLICT DO NOTHING", statement)
        self.assertNotIn("DELETE", statement)
        self.assertIn("INSERT INTO tasks", statement)
        for client, key in (
            ("codex", "CODEX_THREAD_ID"),
            ("claude", "CLAUDE_SESSION_ID"),
            ("kimi", "KIMI_SESSION_ID"),
        ):
            self.assertEqual(collect.current({"BSS_TASK_OWNER": client, key: SID}), (client, SID))
        self.assertEqual(collect.current({"BSS_TASK_OWNER": "kimi"}), ("kimi", "unavailable"))

    def test_finish_collects_registered_clients_without_binding_operator(self):
        with patch.object(collect, "collect", return_value=[]) as run:
            with contextlib.redirect_stderr(io.StringIO()):
                collect.automatic(TASK, "task-completed", "codex")
        run.assert_called_once_with(TASK, None, None, close=True)

    def test_final_tail_retries_until_machine_turn_end_and_survives_cleanup(self):
        partial = [dict(status="partial", reason="final_counter_pending")]
        complete = [dict(status="complete", reason="collected")]
        with patch.object(collect, "collect", side_effect=[partial, complete]) as run:
            with patch.object(collect.time, "sleep") as sleep:
                self.assertEqual(collect.watch(TASK), complete)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(2)
        with patch.object(collect.subprocess, "Popen") as popen:
            collect.launch_final_capture(TASK)
        args = popen.call_args
        self.assertIn("--watch", args.args[0])
        self.assertNotIn("BSS_SERVICE_SECRET", args.kwargs["env"])
        self.assertTrue(args.kwargs["start_new_session"])

    def test_finished_handoff_or_delegate_is_complete_before_later_task_closure(self):
        for client in collect.CLIENTS:
            self.source(client)
            row = self.row(client)
            row["closed_at"] = when(5)
            with patch.object(collect, "sql"):
                result = collect.collect_one(row, home=self.home)
            self.assertEqual(result["status"], "complete", (client, result))

    def test_offline_collection_is_visible_and_does_not_leak_error(self):
        with patch.object(collect, "collect", side_effect=RuntimeError(PRIVATE)):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                collect.automatic(TASK, "commit")
        self.assertIn("collection failed", error.getvalue())
        self.assertNotIn(PRIVATE, error.getvalue())

    def test_delegate_sources_are_discovered_by_machine_relationship_for_all_clients(self):
        for client in collect.CLIENTS:
            with self.subTest(client=client):
                source = self.source(client)
                if client == "codex":
                    child = "00000000-0000-0000-0000-000000000002"
                    rows = fixture(client)
                    rows[0]["payload"].update(
                        id=child, source={"subagent": {"thread_spawn": {"parent_thread_id": SID}}}
                    )
                    self.source(client, rows, session=child)
                elif client == "claude":
                    child = SID + ":agent:worker"
                    path = source.parent / SID / "subagents/agent-worker.jsonl"
                    path.parent.mkdir(parents=True)
                    path.write_text("".join(json.dumps(r) + "\n" for r in fixture(client)))
                else:
                    child = SID + ":agent:worker"
                    path = source.parent.parent / "worker/wire.jsonl"
                    path.parent.mkdir(parents=True)
                    rows = fixture(client)
                    for row in rows:
                        if "agentId" in row:
                            row["agentId"] = "worker"
                    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
                with patch.object(Path, "home", return_value=self.home):
                    self.assertEqual(collect.delegates(self.row(client)), [child])
                    located = collect.locate(client, child)
                    samples, _, reason = collect.project(client, located, child)
                self.assertTrue(samples)
                self.assertIsNone(reason)

    def test_mirrored_kimi_agent_usage_is_not_counted_in_parent_source(self):
        rows = fixture("kimi")
        rows.insert(3, {**rows[2], "agentId": "worker"})
        source = self.source("kimi", rows)
        samples, _, _ = collect.project("kimi", source, SID)
        self.assertEqual(len(samples), 2)

    def test_malformed_usage_payload_is_bounded_and_missing_identity_is_visible(self):
        rows = fixture("codex")
        rows[2]["payload"]["info"] = PRIVATE
        self.source("codex", rows)
        with patch.object(collect, "sql") as sql:
            result = collect.collect_one(self.row("codex"), home=self.home)
        self.assertEqual(result["reason"], "source_invalid")
        self.assertNotIn(PRIVATE, sql.call_args.args[0])
        row = self.row("codex")
        row["external_session_id"] = "unavailable"
        with patch.object(collect, "sql"):
            result = collect.collect_one(row, home=self.home)
        self.assertEqual(result["reason"], "session_identity_missing")

    def test_external_task_recovery_does_not_register_the_operators_unrelated_session(self):
        with (
            patch("sys.argv", ["task_collect.py", "--task", TASK]),
            patch.object(collect, "current", return_value=("codex", "unrelated")),
            patch.object(collect, "collect", return_value=[]) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            collect.main()
        self.assertEqual(run.call_args.args[:3], (TASK, None, None))

    def test_expired_rate_card_leaves_tokens_available_and_cost_unknown(self):
        from test_task_usage import pricing

        self.source("codex")
        card = pricing(provider="openai", model="model-1")
        with patch.object(collect, "sql") as sql:
            result = collect.collect_one(self.row("codex"), pricing=card, home=self.home)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("fixture-v1", sql.call_args.args[0])

    def test_kimi_cache_write_tokens_are_counted_but_not_priced_by_three_rate_card(self):
        from test_task_usage import pricing

        rows = fixture("kimi")
        rows[2]["usage"]["inputCacheCreation"] = 20
        self.source("kimi", rows)
        card = pricing(
            provider="openai",
            model="kimi/model-1",
            effective_from=when(0),
            effective_until=when(59),
        )
        with patch.object(collect, "sql") as sql:
            result = collect.collect_one(self.row("kimi"), pricing=card, home=self.home)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("fixture-v1", sql.call_args.args[0])
        samples, _, _ = collect.project("kimi", collect.locate("kimi", SID, self.home), SID)
        self.assertEqual(samples[0]["input_tokens"], 70)

    def test_final_source_with_missing_cache_counts_reports_counter_gap(self):
        rows = fixture("kimi")
        del rows[2]["usage"]["inputCacheRead"]
        self.source("kimi", rows)
        with patch.object(collect, "sql"):
            result = collect.collect_one(self.row("kimi"), home=self.home)
        self.assertEqual((result["status"], result["reason"]), ("partial", "counters_missing"))

    def test_coverage_is_independent_of_pricing_and_lists_missing_clients(self):
        data = dict(
            tasks=[dict(task_id=TASK, started_at=when(1), ended_at=None, outcome=None)],
            events=[
                dict(
                    task_id=TASK,
                    client="kimi",
                    slot="worker1",
                    event_type="start-task",
                    occurred_at=when(1),
                    result=0,
                )
            ],
            usage=[],
            collections=[],
        )
        task = report(data)["tasks"][0]
        self.assertFalse(task["collection_complete"])
        self.assertEqual(task["missing_clients"], ["kimi"])
        data["collections"] = [dict(task_id=TASK, client="kimi", status="complete")]
        task = report(data)["tasks"][0]
        self.assertTrue(task["collection_complete"])
        self.assertFalse(task["cost_complete"])
        data["events"][0]["external_session_id"] = SID
        data["collections"][0].update(external_session_id=SID, bound_at=when(2))
        task = report(data)["tasks"][0]
        self.assertFalse(task["collection_complete"])
        self.assertEqual(task["missing_sessions"][0]["reason"], "binding_started_after_startup")
        data["events"] = []
        task = report(data)["tasks"][0]
        self.assertFalse(task["collection_complete"])
        self.assertTrue(task["missing_startup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
