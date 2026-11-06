"""Exercise the real shared PostgreSQL schema using a rolled-back transaction."""

# The only interpolated value is a locally generated UUID-based task slug.
# ruff: noqa: S608

import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import task_collect
from task_telemetry import event_sql, literal, sql
from task_usage import REPORT_SQL, usage_sql
from test_task_collect import PRIVATE, SID, fixture, when
from test_task_usage import sample


def main():
    task = f"issue-82-test-{uuid.uuid4().hex}"
    statements = []
    for _ in range(2):
        statements.append(
            event_sql(task, "run", 0, 1, {"CODEX_THREAD_ID": str(uuid.uuid4())})
            .removeprefix("BEGIN;")
            .removesuffix("COMMIT;")
        )
    usage = usage_sql(sample(task_id=task)).removeprefix("BEGIN;").removesuffix("COMMIT;")
    statements.extend([usage, usage])
    collected_task = f"issue-124-test-{uuid.uuid4().hex}"
    statements.append(
        event_sql(collected_task, "start-task", 0, 0, {}, client="codex")
        .removeprefix("BEGIN;")
        .removesuffix("COMMIT;")
    )
    with tempfile.TemporaryDirectory() as directory:
        for client, source in task_collect.CLIENTS.items():
            path = Path(directory) / (client + ".jsonl")
            path.write_text("".join(json.dumps(r) + "\n" for r in fixture(client)))
            row = dict(
                task_id=collected_task,
                client=client,
                external_session_id=SID,
                source=source,
                bound_at=when(1),
                closed_at=when(3),
            )
            statements.append(
                "INSERT INTO usage_collections(task_id,client,external_session_id,source,"
                "bound_at,closed_at) VALUES ("
                + ",".join(
                    literal(row[k])
                    for k in (
                        "task_id",
                        "client",
                        "external_session_id",
                        "source",
                        "bound_at",
                        "closed_at",
                    )
                )
                + ");"
            )
            with patch.object(task_collect, "locate", return_value=path):
                with patch.object(task_collect, "sql") as capture:
                    result = task_collect.collect_one(row)
                    statement = capture.call_args.args[0]
            if result["status"] != "complete" or PRIVATE in statement:
                raise AssertionError("machine projection or privacy regression")
            statement = statement.removeprefix("BEGIN;").removesuffix("COMMIT;")
            statements.extend([statement, statement])
    # Completion and disposable-worktree cleanup cannot delete retained collections.
    statements.append(
        event_sql(collected_task, "task-completed", 0, 0, {})
        .removeprefix("BEGIN;")
        .removesuffix("COMMIT;")
    )
    recovered_task = f"issue-124-recovered-{uuid.uuid4().hex}"
    with patch.object(task_collect, "sql") as capture:
        task_collect.bind(recovered_task, "codex", SID)
    statements.append(capture.call_args.args[0].removeprefix("BEGIN;").removesuffix("COMMIT;"))
    statements.append(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM tasks WHERE task_id='{recovered_task}') OR
           NOT EXISTS (SELECT 1 FROM usage_collections
                       WHERE task_id='{recovered_task}' AND status='unavailable') THEN
            RAISE EXCEPTION 'offline task registration recovery regression';
        END IF;
        END $$;""")
    statements.append(f"""DO $$ BEGIN
        IF (SELECT count(*) FROM usage_collections
            WHERE task_id='{collected_task}' AND status='complete') <> 3 OR
           (SELECT count(*) FROM usage_samples u JOIN agent_sessions a USING(session_id)
            WHERE a.task_id='{collected_task}') <> 7 OR
           (SELECT outcome FROM tasks WHERE task_id='{collected_task}') <> 'completed' THEN
            RAISE EXCEPTION 'collection replay, coverage or finish persistence regression';
        END IF;
        END $$;""")
    # One machine sample belongs to one task: a second task's overlapping window skips it.
    other_task = f"issue-232-other-{uuid.uuid4().hex}"
    statements.append(
        event_sql(other_task, "run", 0, 1, {}).removeprefix("BEGIN;").removesuffix("COMMIT;")
    )
    statements.append(
        usage_sql(sample(task_id=other_task)).removeprefix("BEGIN;").removesuffix("COMMIT;")
    )
    # A legacy row with an unknown cache-write count is filled from the source, once.
    legacy_task = f"issue-232-legacy-{uuid.uuid4().hex}"
    statements.append(
        event_sql(legacy_task, "run", 0, 1, {}).removeprefix("BEGIN;").removesuffix("COMMIT;")
    )
    legacy = sample(task_id=legacy_task, session_id="legacy-session")
    statements.append(usage_sql(legacy).removeprefix("BEGIN;").removesuffix("COMMIT;"))
    statements.append(
        usage_sql({**legacy, "cache_creation_tokens": 7})
        .removeprefix("BEGIN;")
        .removesuffix("COMMIT;")
    )
    statements.append(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_sessions a JOIN usage_samples u USING(session_id)
                   WHERE a.task_id='{other_task}') THEN
            RAISE EXCEPTION 'one sample was stored under two tasks';
        END IF;
        IF (SELECT cache_creation_tokens FROM usage_samples
            WHERE external_session_id='legacy-session') IS DISTINCT FROM 7 THEN
            RAISE EXCEPTION 'unknown cache-write count was not filled';
        END IF;
        END $$;""")
    changed_write = {**legacy, "cache_creation_tokens": 8}
    for changed_sample in (
        sample(task_id=task, output_tokens=51, total_tokens=1051),
        changed_write,
    ):
        conflict = usage_sql(changed_sample).removeprefix("BEGIN;").removesuffix("COMMIT;")
        statements.append(
            "DO $conflict$ BEGIN BEGIN EXECUTE " + literal(conflict) + "; "
            "RAISE EXCEPTION 'conflict was accepted'; EXCEPTION WHEN raise_exception THEN "
            "IF SQLERRM <> 'conflicting usage sample' THEN RAISE; END IF; "
            "END; END $conflict$;"
        )
    sql(
        "BEGIN;\n"
        + "\n".join(statements)
        + f"""
DO $$
DECLARE sid text;
BEGIN
    IF (SELECT count(*) FROM tasks WHERE task_id = '{task}') <> 1 OR
       (SELECT count(*) FROM agent_sessions WHERE task_id = '{task}') <> 3 OR
       (SELECT count(*) FROM workflow_events WHERE task_id = '{task}') <> 2 THEN
        RAISE EXCEPTION 'task/session/event cardinality';
    END IF;
    IF (SELECT count(*) FROM usage_samples u JOIN agent_sessions a USING(session_id)
        WHERE a.task_id = '{task}') <> 1 THEN
        RAISE EXCEPTION 'usage idempotency';
    END IF;
    SELECT session_id INTO sid FROM agent_sessions WHERE task_id = '{task}' LIMIT 1;
    INSERT INTO usage_samples(session_id) VALUES (sid);
    IF EXISTS (SELECT 1 FROM usage_samples WHERE session_id = sid AND sample_key IS NULL AND
               (input_tokens IS NOT NULL OR estimated_cost IS NOT NULL)) THEN
        RAISE EXCEPTION 'unknown usage must stay null';
    END IF;
    BEGIN
        INSERT INTO usage_samples(session_id, input_tokens) VALUES (sid, -1);
        RAISE EXCEPTION 'negative usage accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO workflow_events(task_id, event_type, result, metadata)
        VALUES ('{task}', 'run', 0, jsonb_build_object('data', repeat('x', 1025)));
        RAISE EXCEPTION 'unbounded metadata accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
END $$;
"""
        + REPORT_SQL
        + "ROLLBACK;"
    )
    print("telemetry: database regression checks passed (test rows rolled back)")


if __name__ == "__main__":
    main()
