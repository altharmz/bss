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
from task_usage import report, timestamp

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

    def test_subagent_source_with_parent_session_meta_is_accepted(self):
        rows = fixture("codex")
        rows.insert(
            1,
            dict(type="session_meta", payload=dict(id="parent-thread", model_provider="other")),
        )
        source = self.source("codex", rows)
        items, _, _ = collect.project("codex", source, SID)
        self.assertEqual({i["provider"] for i in items}, {"openai"})
        self.assertEqual(len(items), 3)

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
        with patch.object(collect, "kimi_session", return_value=None):
            self.assertEqual(collect.current({"BSS_TASK_OWNER": "kimi"}), ("kimi", "unavailable"))

    def test_kimi_session_matches_only_one_process_owned_session_lock(self):
        self.source("kimi")
        proc = self.home / "proc"
        (proc / "123").mkdir(parents=True)
        (proc / "123/stat").write_text("123 (kimi) R 1")
        registry = self.home / ".kimi-code/locks/sessions"
        registry.mkdir(parents=True)

        def lock(session, pid=123):
            (registry / f"session_{session}.lock").write_text(
                json.dumps(
                    dict(
                        version=1,
                        token=f"{pid}:{session}",
                        pid=pid,
                        hostname=os.uname().nodename,
                    )
                )
            )

        lock(SID)
        with (
            patch.object(Path, "home", return_value=self.home),
            patch.object(collect.os, "getpid", return_value=123),
        ):
            self.assertEqual(collect.kimi_session(proc), SID)
            with patch.object(collect, "kimi_session", return_value=SID):
                self.assertEqual(collect.current({"BSS_TASK_OWNER": "kimi"}), ("kimi", SID))
            other = "00000000-0000-0000-0000-000000000002"
            self.source("kimi", session=other)
            lock(other)
            self.assertIsNone(collect.kimi_session(proc))
            (registry / f"session_{other}.lock").unlink()
            self.assertEqual(collect.kimi_session(proc), SID)
            lock(SID, pid=999)
            self.assertIsNone(collect.kimi_session(proc))

    def test_finish_collects_registered_clients_without_binding_operator(self):
        with patch.object(collect, "collect", return_value=[]) as run:
            with contextlib.redirect_stderr(io.StringIO()):
                collect.automatic(TASK, "task-completed", "codex")
        run.assert_called_once_with(TASK, None, None, close=True)

    def test_startup_without_samples_explains_that_it_is_normal(self):
        empty = [dict(status="unavailable", reason="counters_missing", observed_samples=0)]
        with patch.object(collect, "collect", return_value=empty):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                collect.automatic(TASK, "start-task", "claude")
        self.assertIn("normal at startup", error.getvalue())
        with patch.object(collect, "collect", return_value=empty):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                collect.automatic(TASK, "commit", "claude")
        self.assertNotIn("normal at startup", error.getvalue())

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

    def catch_up_db(self, rows):
        """Fake storage: the catch-up query returns rows; writes are recorded."""
        queries, writes = [], []

        def fake(statement):
            if statement.startswith("BEGIN;"):
                writes.append(statement)
                return ""
            queries.append(statement)
            return json.dumps(rows)

        return fake, queries, writes

    def test_start_task_collects_a_finished_tasks_late_end_turn(self):
        patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")}).start()
        rows = fixture("claude")
        self.source("claude", rows[:-1])
        row = self.row("claude", checked_at=when(2), status="partial")
        row["closed_at"] = when(2)
        with patch.object(collect, "sql"):
            first = collect.collect_one(row)
        self.assertEqual((first["status"], first["reason"]), ("partial", "final_counter_pending"))
        # The turn ended after finish-task's watcher gave up; the source keeps it.
        path = self.source("claude", rows)
        os.utime(path, (timestamp(when(5)).timestamp(),) * 2)
        fake, queries, writes = self.catch_up_db([row])
        with (
            patch.object(collect, "sql", side_effect=fake),
            patch.object(collect, "collect", return_value=[]) as current,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            collect.automatic("issue-125-next", "start-task", "claude")
        self.assertIn("final counters collected for 1 finished task(s) (#124)", error.getvalue())
        self.assertEqual(len(writes), 1)
        self.assertIn("status='complete'", writes[0])
        current.assert_called_once()
        # Only finished tasks' closed windows waiting for the final counter are read.
        query = queries[0]
        for clause in (
            "t.ended_at IS NOT NULL",
            "a.closed_at IS NOT NULL",
            "a.status='partial'",
            "a.reason='final_counter_pending'",
        ):
            self.assertIn(clause, query)

    def test_catch_up_skips_unchanged_sources_and_respects_the_limit(self):
        patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")}).start()
        path = self.source("claude")
        os.utime(path, (timestamp(when(5)).timestamp(),) * 2)
        unchanged = self.row("claude", checked_at=when(6))
        missing = dict(self.row("claude", checked_at=when(1)), external_session_id="gone")
        changed = self.row("claude", checked_at=when(4))
        fake, _, writes = self.catch_up_db([unchanged, missing, changed, changed])
        with patch.object(collect, "sql", side_effect=fake):
            results = collect.catch_up(limit=1)
        self.assertEqual([r["status"] for r in results], ["complete"])
        self.assertEqual(len(writes), 1)
        fake, _, writes = self.catch_up_db([unchanged])
        with (
            patch.object(collect, "sql", side_effect=fake),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(collect.report_catch_up(), [])
        self.assertEqual((writes, error.getvalue()), ([], ""))

    def test_catch_up_failure_warns_and_start_task_still_collects(self):
        with (
            patch.object(collect, "catch_up", side_effect=RuntimeError(PRIVATE)),
            patch.object(collect, "collect", return_value=[]) as current,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(collect.automatic(TASK, "start-task", "claude"), [])
        current.assert_called_once()
        self.assertIn("catch-up of finished tasks failed", error.getvalue())
        self.assertNotIn(PRIVATE, error.getvalue())
        self.assertNotIn("collection failed", error.getvalue())
        with (
            patch.object(collect, "catch_up") as catch,
            patch.object(collect, "collect", return_value=[]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            collect.automatic(TASK, "commit", "claude")
        catch.assert_not_called()

    def test_finish_reports_a_pending_final_counter_as_background_capture(self):
        pending = [
            dict(client="claude", status="partial", reason="final_counter_pending"),
            dict(client="codex", status="complete", reason="collected"),
        ]
        collect._ANNOUNCED.discard(TASK)
        with (
            patch.object(collect, "collect", return_value=pending),
            patch.object(collect, "launch_final_capture") as launch,
            patch.object(collect, "capture_facts"),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            collect.automatic(TASK, "task-completed")
            collect.automatic(TASK, "finish-task")
        launch.assert_called_once_with(TASK)
        lines = error.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("final usage counter pending for 1 closed session(s)", lines[0])
        self.assertIn("background capture", lines[0])
        self.assertIn(f"make telemetry-watch TASK={TASK}", lines[0])
        self.assertNotIn('"partial"', lines[0])
        # Any other open result is shown as it is.
        other = [*pending, dict(client="kimi", status="unavailable", reason="source_missing")]
        with (
            patch.object(collect, "collect", return_value=other),
            patch.object(collect, "launch_final_capture"),
            patch.object(collect, "capture_facts"),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            collect.automatic("issue-126-other", "task-completed")
        self.assertIn('"source_missing"', error.getvalue())

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

    def test_primary_checkout_without_task_prints_one_line_and_no_traceback(self):
        import task_quality
        from task_runtime import InvalidRuntime

        blocked = InvalidRuntime("use a linked task worktree; primary is administration-only")
        for argv in (["task_collect.py"], ["task_collect.py", "--watch"]):
            err = io.StringIO()
            with (
                self.subTest(argv=argv),
                patch("sys.argv", argv),
                patch.dict(os.environ, {"BSS_COLLECT_TASK": ""}),
                patch.object(collect, "current", return_value=("claude", SID)),
                patch.object(task_quality, "context", side_effect=blocked),
                patch.object(collect, "collect") as run,
                patch.object(collect, "watch") as tail,
                patch.object(collect, "catch_up", side_effect=RuntimeError) as catch,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(collect.main(), 1)
            self.assertTrue(err.getvalue().endswith(collect.NO_WORKTREE + "\n"))
            self.assertIn("TASK=<task-id>", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())
            run.assert_not_called()
            tail.assert_not_called()
            self.assertEqual(catch.called, argv == ["task_collect.py"])

    def test_collect_without_a_task_catches_up_finished_tasks_first(self):
        import task_quality
        from task_runtime import InvalidRuntime

        blocked = InvalidRuntime("use a linked task worktree; primary is administration-only")
        caught = [dict(task_id=TASK, status="complete", reason="collected")]
        with (
            patch("sys.argv", ["task_collect.py"]),
            patch.dict(os.environ, {"BSS_COLLECT_TASK": ""}),
            patch.object(collect, "current", return_value=("claude", SID)),
            patch.object(task_quality, "context", side_effect=blocked),
            patch.object(collect, "catch_up", return_value=caught),
            patch.object(collect, "collect") as run,
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            self.assertEqual(collect.main(), 0)
        self.assertIn("final counters collected for 1 finished task(s) (#124)", err.getvalue())
        run.assert_not_called()

    def test_explicit_task_collects_and_watches_without_a_worktree(self):
        import task_quality

        done = [{"client": "claude", "status": "complete", "reason": None}]
        for argv, target in (
            (["task_collect.py", "--task", TASK], "collect"),
            (["task_collect.py", "--watch", "--task", TASK], "watch"),
        ):
            with (
                self.subTest(argv=argv),
                patch("sys.argv", argv),
                patch.object(collect, "current", return_value=("claude", SID)),
                patch.object(task_quality, "context", side_effect=AssertionError("worktree")),
                patch.object(collect, target, return_value=done) as run,
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(collect.main(), 0)
            self.assertEqual(run.call_args.args[0], TASK)
            self.assertEqual(json.loads(out.getvalue()), {"collections": done})

    def test_kimi_cache_write_tokens_are_counted(self):
        rows = fixture("kimi")
        rows[2]["usage"]["inputCacheCreation"] = 20
        self.source("kimi", rows)
        with patch.object(collect, "sql"):
            result = collect.collect_one(self.row("kimi"), home=self.home)
        self.assertEqual(result["status"], "complete")
        samples, _, _ = collect.project("kimi", collect.locate("kimi", SID, self.home), SID)
        self.assertEqual(samples[0]["input_tokens"], 70)

    def test_final_source_with_missing_cache_counts_reports_counter_gap(self):
        rows = fixture("kimi")
        del rows[2]["usage"]["inputCacheRead"]
        self.source("kimi", rows)
        with patch.object(collect, "sql"):
            result = collect.collect_one(self.row("kimi"), home=self.home)
        self.assertEqual((result["status"], result["reason"]), ("partial", "counters_missing"))

    def test_coverage_lists_missing_clients(self):
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

    def test_subagent_make_binds_its_own_agent_session(self):
        with patch.object(collect, "claude_session", return_value=SID):
            environ = {"BSS_TASK_OWNER": "claude", "BSS_TELEMETRY_AGENT": "a4fbc098e052c5224"}
            self.assertEqual(collect.current(environ), ("claude", SID + ":agent:a4fbc098e052c5224"))
            self.assertEqual(collect.current({"BSS_TASK_OWNER": "claude"}), ("claude", SID))
            # Anything but a plain agent id is ignored, never spliced into a path.
            bad = {"BSS_TASK_OWNER": "claude", "BSS_TELEMETRY_AGENT": "../x"}
            self.assertEqual(collect.current(bad), ("claude", SID))

    def test_parallel_subagents_are_charged_only_to_their_own_issue(self):
        # An orchestrator bound to TASK owns three children: one it spawned for this
        # issue, one that ran make start-task for another issue, one spawned earlier.
        parent = dict(
            task_id=TASK,
            client="claude",
            external_session_id=SID,
            bound_at=when(10),
            closed_at=None,
            next_bound_at=when(40),
        )
        mine, theirs, earlier = (SID + ":agent:" + n for n in ("mine", "theirs", "earlier"))
        starts = {mine: when(20), theirs: when(21), earlier: when(5)}
        rows = [parent]
        with (
            patch.object(collect, "sql", return_value="[]"),
            patch.object(collect, "collection_rows", side_effect=lambda task: list(rows)),
            patch.object(
                collect,
                "delegates",
                side_effect=lambda r: [mine, theirs, earlier] if r is parent else [],
            ),
            patch.object(collect, "explicitly_bound", return_value={theirs}),
            patch.object(collect, "first_seen", side_effect=lambda c, s: timestamp(starts[s])),
            patch.object(collect, "bind") as bind,
            patch.object(collect, "collect_one", return_value={}),
        ):
            collect.collect(TASK)
        bound = [c.args[2] for c in bind.call_args_list]
        self.assertEqual(bound, [mine])
        self.assertEqual(bind.call_args.kwargs["binding"], "delegated")

    def test_spawn_window_is_half_open_and_requires_a_start(self):
        row = dict(bound_at=when(10), closed_at=when(30), next_bound_at=None)
        self.assertTrue(collect.spawned_within(row, timestamp(when(10))))
        self.assertFalse(collect.spawned_within(row, timestamp(when(30))))
        self.assertFalse(collect.spawned_within(row, timestamp(when(9))))
        self.assertFalse(collect.spawned_within(row, None))
        self.assertTrue(collect.spawned_within({**row, "closed_at": None}, timestamp(when(59))))

    def test_explicit_binding_upgrades_delegated_and_only_explicit_closes_others(self):
        with patch.object(collect, "sql") as sql:
            collect.bind(TASK, "claude", SID, binding="delegated")
        delegated = sql.call_args.args[0]
        self.assertIn("'delegated'", delegated)
        self.assertIn("AND 'delegated'='explicit'", delegated)
        with patch.object(collect, "sql") as sql:
            collect.bind(TASK, "claude", SID)
        explicit = sql.call_args.args[0]
        self.assertIn("DO UPDATE SET binding='explicit'", explicit)
        with self.assertRaises(ValueError):
            collect.bind(TASK, "claude", SID, binding="guessed")

    def test_reset_removes_only_this_tasks_projection(self):
        with patch.object(collect, "sql") as sql:
            collect.reset(TASK)
        statement = sql.call_args.args[0]
        self.assertIn(f"task_id='{TASK}'", statement)
        self.assertIn(f"LIKE '{TASK}:usage:%'", statement)
        self.assertNotIn("DELETE FROM tasks", statement)
        self.assertNotIn("workflow_events", statement)
        with self.assertRaises(ValueError):
            collect.reset("issue-1-x'; DROP TABLE tasks; --")

    def test_claude_cache_writes_are_kept_apart_from_reads(self):
        path = self.source("claude")
        samples, _, _ = collect.project("claude", path, SID)
        self.assertEqual({s["cache_creation_tokens"] for s in samples}, {20})
        for s in samples:
            self.assertLessEqual(
                s["cached_input_tokens"] + s["cache_creation_tokens"], s["input_tokens"]
            )

    def test_synthetic_messages_and_tool_ended_turns(self):
        rows = fixture("claude")[:2]
        rows.append(
            dict(
                type="assistant",
                timestamp=when(2),
                sessionId=SID,
                message=dict(
                    id="local-1",
                    model="<synthetic>",
                    stop_reason="stop_sequence",
                    usage=dict(input_tokens=0, output_tokens=0),
                ),
            )
        )
        rows.append(dict(type="user", timestamp=when(3), toolEndsTurn=True, content=PRIVATE))
        samples, ended, reason, active = collect.project(
            "claude", self.source("claude", rows), SID, with_state=True
        )
        self.assertEqual([s["sample_id"] for s in samples], ["msg-1"])
        self.assertEqual(ended, [when(2), when(3)])
        self.assertFalse(active)
        self.assertIsNone(reason)

    def test_activity_counts_human_prompts_and_turn_time_without_content(self):
        def user(second, **extra):
            return dict(type="user", timestamp=when(second), sessionId=SID, **extra)

        def assistant(second, stop):
            return dict(
                type="assistant",
                timestamp=when(second),
                sessionId=SID,
                message=dict(id=f"m{second}", model="m", stop_reason=stop, content=PRIVATE),
            )

        rows = [
            user(10, message=dict(content=PRIVATE)),  # human prompt: turn starts
            assistant(12, "tool_use"),
            user(13, message=dict(content=[dict(type="tool_result", content=PRIVATE)])),
            assistant(20, "end_turn"),  # 10s turn
            user(30, isMeta=True, message=dict(content=PRIVATE)),  # not a prompt
            user(40, message=dict(content=[dict(type="text", text=PRIVATE)])),
            assistant(45, "end_turn"),  # 5s turn
        ]
        path = self.source("claude", rows)
        prompts, turns = collect.activity("claude", path, SID)
        self.assertEqual(prompts, [timestamp(when(10)), timestamp(when(40))])
        self.assertEqual(
            collect.window_activity(prompts, turns, timestamp(when(0)), None), (2, 15000)
        )
        # Clipped to the window: [15, 42) holds 5s of turn one, 2s of turn two.
        self.assertEqual(
            collect.window_activity(prompts, turns, timestamp(when(15)), timestamp(when(42))),
            (1, 7000),
        )
        agent_path = path.parent / SID / "subagents/agent-w.jsonl"
        agent_path.parent.mkdir(parents=True)
        agent_path.write_text(path.read_text())
        agent_prompts, agent_turns = collect.activity("claude", agent_path, SID + ":agent:w")
        self.assertEqual(agent_prompts, [])
        self.assertEqual(len(agent_turns), 2)

    def test_activity_for_codex_and_kimi(self):
        prompts, turns = collect.activity("codex", self.source("codex"), SID)
        self.assertEqual(prompts, [])
        self.assertEqual(turns, [(timestamp(when(1)), timestamp(when(4)))])
        rows = [dict(type="turn.prompt", time=1793620800000), *fixture("kimi")]
        prompts, turns = collect.activity("kimi", self.source("kimi", rows), SID)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(len(turns), 1)

    def test_first_seen_reads_the_first_dated_record(self):
        self.source("claude")
        with patch.object(Path, "home", return_value=self.home):
            self.assertEqual(collect.first_seen("claude", SID), timestamp(when(1)))
            self.assertIsNone(collect.first_seen("claude", SID + ":agent:none"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
