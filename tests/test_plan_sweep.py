"""Plan sweep: literal-safe shape matching, useful flags, and real write rollback."""

import psycopg

from loadtest.plan_capture import example
from loadtest.plan_measure import (
    Catalog,
    inspect_plan,
    measure,
    parameter_columns,
    restore_sequences,
)
from loadtest.plan_sql import bind_literals, fingerprint, server_query


def test_normalized_server_shape_matches_application_binds_and_constants():
    app = "SELECT * FROM product WHERE id = %s AND status = 'active' LIMIT 20"
    query, values = server_query(app, ["unused-id"])
    normalized = "SELECT * FROM product WHERE id=$1 AND status=$2 LIMIT $3"
    assert fingerprint(query) == fingerprint(normalized)
    assert values == ["unused-id"]


def test_markers_inside_literals_identifiers_and_comments_are_not_bound():
    query = "SELECT '$1 %s', \"$2\", %(id)s, %(id)s -- %s $3\n"
    converted, values = server_query(query, {"id": 7})
    assert values == [7]
    assert converted == "SELECT '$1 %s', \"$2\", $1, $1 -- %s $3\n"
    assert bind_literals(converted, ["7"]) == "SELECT '$1 %s', \"$2\", 7, 7 -- %s $3\n"


def test_reused_parameters_are_distinct_from_two_independent_parameters():
    assert fingerprint("SELECT $1 + $1") != fingerprint("SELECT $1 + $2")
    assert fingerprint("SELECT $4 + $4") == fingerprint("SELECT $1 + $1")
    assert fingerprint("SELECT * FROM p WHERE (p.id IS NULL)") == fingerprint(
        "SELECT * FROM p WHERE p.id IS NULL"
    )


def test_executemany_keeps_a_one_shot_iterator_and_captures_its_shape(monkeypatch):
    from loadtest import plan_capture

    captured = []
    monkeypatch.setattr(plan_capture, "ORIGINAL_MANY", lambda self, query, rows: list(rows))
    monkeypatch.setattr(plan_capture, "record", lambda self, q, p: captured.append((q, p)))
    rows = iter([(1,), (2,), (3,)])
    assert plan_capture.executemany(None, "SELECT %s", rows) == [(1,), (2,), (3,)]
    assert captured == [("SELECT %s", (1,))]


def test_binding_hints_handle_aliases_arrays_and_insert_expressions():
    catalog = {"product": {"id": "uuid", "status": "text", "billing_account_id": "uuid"}}
    assert parameter_columns("SELECT p.* FROM product p WHERE p.id=ANY($1)", catalog) == {
        1: ("product", "id")
    }
    assert parameter_columns(
        "INSERT INTO product (id,status,billing_account_id) VALUES ($1,coalesce($2,'active'),$3)",
        catalog,
    ) == {1: ("product", "id"), 2: ("product", "status"), 3: ("product", "billing_account_id")}


def test_unqualified_update_id_belongs_to_outer_target_not_nested_select():
    catalog = {
        "billing_run": {"id": "uuid", "member_count": "int8"},
        "billing_account": {"id": "uuid", "opened_at": "timestamptz"},
    }
    query = (
        "UPDATE billing_run SET member_count=(SELECT count(*) FROM billing_account a"
        " WHERE a.opened_at < $1) WHERE id=$2"
    )
    assert parameter_columns(query, catalog)[2] == ("billing_run", "id")


def test_flags_find_nested_large_scans_and_estimate_errors_even_when_fast():
    document = {
        "Execution Time": 1.0,
        "Plan": {
            "Node Type": "Aggregate",
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "usage_record",
                    "Plan Rows": 1,
                    "Actual Rows": 500,
                    "Actual Loops": 1,
                    "Shared Read Blocks": 100,
                },
            ],
        },
    }
    result = inspect_plan(document, {"usage_record": 20000}, 5)
    assert any("sequential scan" in f for f in result["flags"])
    assert any("estimate error 500.0x" in f for f in result["flags"])
    assert not any("execution" in f for f in result["flags"])


def test_limit_does_not_turn_a_correct_full_scan_estimate_into_a_false_error():
    document = {
        "Execution Time": 1,
        "Plan": {
            "Node Type": "Limit",
            "Plan Rows": 21,
            "Actual Rows": 21,
            "Actual Loops": 1,
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Relation Name": "product",
                    "Plan Rows": 100000,
                    "Actual Rows": 21,
                    "Actual Loops": 1,
                }
            ],
        },
    }
    result = inspect_plan(document, {"product": 100000}, 5)
    assert not result["flags"]
    assert result["nodes"][1]["Estimate Comparable"] is False


def test_a_blocking_sort_under_limit_still_checks_its_fully_consumed_input():
    document = {
        "Execution Time": 1,
        "Plan": {
            "Node Type": "Limit",
            "Plan Rows": 21,
            "Actual Rows": 21,
            "Actual Loops": 1,
            "Plans": [
                {
                    "Node Type": "Sort",
                    "Plan Rows": 1000,
                    "Actual Rows": 21,
                    "Actual Loops": 1,
                    "Plans": [
                        {
                            "Node Type": "Index Scan",
                            "Relation Name": "product",
                            "Plan Rows": 1000,
                            "Actual Rows": 200000,
                            "Actual Loops": 1,
                        }
                    ],
                }
            ],
        },
    }
    result = inspect_plan(document, {"product": 200000}, 5)
    assert any("estimate error 200.0x" in f for f in result["flags"])
    assert result["nodes"][2]["Estimate Comparable"] is True


def test_fixture_queries_through_generic_helpers_have_no_application_sql_origin():
    from types import SimpleNamespace as Frame

    from loadtest.plan_capture import attribution

    test = Frame(
        f_code=Frame(co_filename="/app/tests/test_fixture.py", co_name="test_fixture"),
        f_lineno=1,
        f_back=None,
    )
    helper = Frame(
        f_code=Frame(co_filename="/app/app/repository/_sql.py", co_name="fetch_one"),
        f_lineno=31,
        f_back=test,
    )
    assert attribution(helper)[1] == []
    helper.f_back = Frame(
        f_code=Frame(co_filename="/app/app/repository/billing.py", co_name="list_members"),
        f_lineno=320,
        f_back=test,
    )
    assert attribution(helper)[1] == ["app/repository/billing.py:320"]


def test_per_account_queries_keep_the_tight_budget_inside_a_tick(monkeypatch):
    import ast
    from pathlib import Path

    from loadtest.plan_measure import cadence

    path = "app/notify/events.py"
    functions = {
        node.name: node.lineno + 1
        for node in ast.walk(ast.parse(Path(path).read_text()))
        if isinstance(node, ast.FunctionDef)
    }
    once = f"{path}:{functions['_account_ids']}"
    per_account = f"{path}:{functions['_accounts']}"
    assert cadence([once])[1] == 100
    assert cadence([per_account])[1] == 5
    assert cadence([once, per_account])[1] == 5


def test_parameter_examples_do_not_persist_hashes_pins_or_opaque_credentials():
    assert example("a" * 64) == {"text": True}
    assert example("123456") == {"text": True}
    assert example(b"password") == {"type": "bytes"}
    assert example("pendingActive") == "pendingActive"


class ProbeCatalog:
    rows = {"sweep_probe": 1}

    def bindings(self, shape, types, profile):
        return bind_literals(shape["query"], ["2"]), []


def test_analyzed_writes_are_really_rolled_back_for_every_profile(migrated_db_url):
    with psycopg.connect(
        migrated_db_url, autocommit=True, row_factory=psycopg.rows.dict_row
    ) as conn:
        conn.execute("CREATE TEMP TABLE sweep_probe (id int PRIMARY KEY, n int)")
        conn.execute("INSERT INTO sweep_probe VALUES (1, 1)")
        shape = {
            "query": "UPDATE sweep_probe SET n=$1 WHERE id=1",
            "examples": [2],
            "locations": [],
            "tests": ["rollback probe"],
        }
        # Repetition crosses psycopg's automatic-prepare threshold. Cleanup must not
        # deallocate the driver's own cached statements.
        for _ in range(3):
            result = measure(conn, ProbeCatalog(), shape)
        assert len(result["samples"]) == 4
        assert conn.execute("SELECT n FROM sweep_probe").fetchone()["n"] == 1
        assert all(s["plan"]["Plan"]["Plans"][0]["Actual Rows"] == 1 for s in result["samples"])


def test_failed_write_is_unbound_instead_of_a_successful_empty_plan(migrated_db_url):
    with psycopg.connect(
        migrated_db_url, autocommit=True, row_factory=psycopg.rows.dict_row
    ) as conn:
        conn.execute("CREATE TEMP TABLE sweep_probe (id int PRIMARY KEY)")
        conn.execute("INSERT INTO sweep_probe VALUES (2)")
        shape = {
            "query": "INSERT INTO sweep_probe VALUES ($1)",
            "examples": [2],
            "locations": [],
            "tests": ["constraint probe"],
        }
        result = measure(conn, ProbeCatalog(), shape)
        assert result["status"] == "unbound"
        assert len(result["errors"]) == 4
        assert not result["samples"]


def test_a_nullable_dataset_value_is_not_replaced_with_an_unrelated_foreign_key(migrated_db_url):
    from uuid import uuid4

    ident = uuid4()
    with psycopg.connect(
        migrated_db_url, autocommit=True, row_factory=psycopg.rows.dict_row
    ) as conn:
        conn.execute(
            "CREATE TEMP TABLE sweep_probe (id uuid PRIMARY KEY, parent_id uuid,"
            " CHECK (parent_id IS DISTINCT FROM id))"
        )
        conn.execute("INSERT INTO sweep_probe VALUES (%s,NULL)", (ident,))
        catalog = Catalog.__new__(Catalog)
        catalog.conn = conn
        catalog.rows = {"sweep_probe": 1}
        catalog.columns = {"sweep_probe": {"id": "uuid", "parent_id": "uuid"}}
        catalog.foreign = {("sweep_probe", "parent_id"): ("sweep_probe", "id")}
        catalog.unique = set()
        catalog.enum = {}
        catalog.accounts = {"typical": {"id": ident}}
        catalog.sample = lambda table, profile: {"id": ident, "parent_id": None}
        shape = {
            "query": "UPDATE sweep_probe SET parent_id=$1 WHERE id=$2",
            "examples": [None, None],
            "locations": [],
            "tests": ["nullable FK probe"],
        }
        result = measure(conn, catalog, shape)
        assert len(result["samples"]) == 4
        assert conn.execute("SELECT parent_id FROM sweep_probe").fetchone()["parent_id"] is None


def test_expression_types_reject_misleading_nearby_column_hints():
    from datetime import date, timedelta

    catalog = Catalog.__new__(Catalog)
    assert not catalog.compatible(30, "date")
    assert catalog.compatible(date(2026, 11, 4), "date")
    assert not catalog.compatible("sweep", "boolean")
    assert catalog.compatible(timedelta(days=1), "interval")


def test_run_samples_use_latest_daily_run_and_keep_command_runs_separate(migrated_db_url):
    from datetime import date
    from uuid import uuid4

    daily_old, daily_latest, command_run = uuid4(), uuid4(), uuid4()
    with psycopg.connect(
        migrated_db_url, autocommit=True, row_factory=psycopg.rows.dict_row
    ) as conn:
        conn.execute("CREATE TEMP TABLE billing_run (id uuid, kind text, run_date date)")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO billing_run VALUES (%s,%s,%s)",
                [
                    (daily_old, "daily", date(2026, 11, 3)),
                    (daily_latest, "daily", date(2026, 11, 4)),
                    (command_run, "prepaidCommand", date(2026, 11, 10)),
                ],
            )
        catalog = Catalog.__new__(Catalog)
        catalog.conn, catalog.cache = conn, {}
        catalog.columns = {"billing_run": {"id": "uuid", "kind": "text", "run_date": "date"}}
        catalog.accounts = {"typical": {"id": uuid4()}}
        assert catalog.sample("billing_run", "typical")["id"] == daily_latest
        catalog.query = "SELECT * FROM billing_run WHERE kind='prepaidCommand'"
        assert catalog.sample("billing_run", "typical")["id"] == command_run


def test_sequence_restoration_covers_the_side_effect_that_rollback_does_not(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute("CREATE TEMP SEQUENCE sweep_probe_seq")
        before = conn.execute("SELECT last_value,is_called FROM sweep_probe_seq").fetchone()
        conn.execute("BEGIN")
        conn.execute("SELECT nextval('sweep_probe_seq')")
        conn.rollback()
        assert conn.execute("SELECT is_called FROM sweep_probe_seq").fetchone()[0] is True
        restore_sequences(conn, [('"sweep_probe_seq"', *before)])
        assert conn.execute("SELECT last_value,is_called FROM sweep_probe_seq").fetchone() == before
