"""Exercise the real shared PostgreSQL schema using a rolled-back transaction."""

# The only interpolated value is a locally generated UUID-based task slug.
# ruff: noqa: S608

import uuid

from task_telemetry import event_sql, literal, sql
from task_usage import REPORT_SQL, usage_sql
from test_task_usage import pricing, sample


def main():
    task = f"issue-82-test-{uuid.uuid4().hex}"
    statements = []
    for _ in range(2):
        statements.append(
            event_sql(task, "run", 0, 1, {"CODEX_THREAD_ID": str(uuid.uuid4())})
            .removeprefix("BEGIN;")
            .removesuffix("COMMIT;")
        )
    usage = (
        usage_sql(sample(task_id=task), pricing()).removeprefix("BEGIN;").removesuffix("COMMIT;")
    )
    statements.extend([usage, usage])
    for changed_sample, changed_pricing in (
        (sample(task_id=task, output_tokens=51, total_tokens=1051), pricing()),
        (sample(task_id=task), pricing(version="fixture-v2", input_per_million="4")),
    ):
        conflict = (
            usage_sql(changed_sample, changed_pricing)
            .removeprefix("BEGIN;")
            .removesuffix("COMMIT;")
        )
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
        WHERE a.task_id = '{task}') <> 1 OR
       (SELECT estimated_cost FROM usage_samples u JOIN agent_sessions a USING(session_id)
        WHERE a.task_id = '{task}') <> 0.0021 THEN
        RAISE EXCEPTION 'usage idempotency or exact pricing';
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
