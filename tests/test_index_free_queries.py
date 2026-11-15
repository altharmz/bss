"""The queries of #311 that an index alone could not fix read their index: the latest grant
per product without a DISTINCT ON over every grant, uuid filters compared as uuid, and the
/metrics queue counts with their conditions in WHERE."""

from datetime import date
from uuid import UUID

import pytest

from app import metrics
from app.notify import events
from app.repository import _sql, balance_actions, numbers, prepaid_command_views
from app.worker import prepaid
from tests import apidata
from tests.test_hot_path_indexes import plan

NIL = UUID(apidata.NIL)
TODAY = date(2026, 11, 4)


def captured(monkeypatch, module, needle):
    """Run the statements of `module` as usual; keep the (query, params) of those whose
    text has `needle`."""
    real = module.fetch_all
    seen = []

    def recording(conn, query, params=None):
        text = query.as_string(conn) if hasattr(query, "as_string") else query
        if needle in text:
            seen.append((text, params))
        return real(conn, query, params)

    monkeypatch.setattr(module, "fetch_all", recording)
    return seen


@pytest.mark.parametrize(
    ("query", "params"),
    [
        (prepaid._DUE_RENEWALS, {"today": TODAY, "account": None}),
        (events._PLAN_EXPIRING, {"today": TODAY, "limit": date(2026, 11, 7)}),
    ],
)
def test_the_latest_grants_are_found_without_a_distinct_on(db_conn, query, params):
    """The renewal producer and the plan-expiring detector sorted every funded interval to
    find each product's latest (0.5 s per wake-up at 100k, growing with history); now each
    grant read asks its product's latest index whether a later grant follows."""
    text = plan(db_conn, query, params)
    assert "funded_interval_latest_idx on funded_interval l" in text, text
    assert "Unique" not in text, text


def test_the_plan_expiring_window_is_read_by_the_grants_end(db_conn):
    """The notice window is a range of upper(period), so only its grants are read as the
    grant history grows. (The producer reads every grant ended by today; with no history
    an empty table's estimates may drive it from the accounts instead.)"""
    text = plan(db_conn, events._PLAN_EXPIRING, {"today": TODAY, "limit": date(2026, 11, 7)})
    assert "funded_interval_end_idx on funded_interval g" in text, text


def test_a_balance_action_list_compares_the_account_as_a_uuid(db_conn, monkeypatch):
    """`billing_account_id::text = ...` read the newest top-ups of every account (109 ms
    for one account's top-ups at 100k against 1.6 ms typed)."""
    seen = captured(monkeypatch, balance_actions, "FROM balance_action")
    balance_actions.page(db_conn, {"billing_account_id": NIL, "status": "approved"}, 0, 20)
    ((query, params),) = seen
    assert "::text" not in query
    # typed, so balance_action_billing_account_id_idx can serve it (an empty table's
    # estimates prefer the kind index, so the plan alone does not show it)
    text = plan(db_conn, query, params)
    assert f"billing_account_id = '{NIL}'::uuid" in text, text


def test_a_resource_list_compares_the_id_as_a_uuid(db_conn, monkeypatch):
    seen = captured(monkeypatch, numbers, "FROM inventory")
    numbers.page(db_conn, {"id": NIL, "allocation_status": "available"}, 0, 20, TODAY, 0)
    ((query, params),) = seen
    assert "::text" not in query
    assert "Index Cond: (id = " in plan(db_conn, query, params)


def test_a_resource_list_state_that_is_no_label_matches_nothing_without_a_query(
    db_conn, monkeypatch
):
    seen = captured(monkeypatch, numbers, "FROM inventory")
    rows, more = numbers.page(db_conn, {"administrative_state": "nonsense"}, 0, 20, TODAY, 0)
    assert (rows, more, seen) == ([], False, [])


def test_a_commands_renewed_grant_is_found_by_its_key(db_conn, monkeypatch):
    seen = captured(monkeypatch, prepaid_command_views, "FROM funded_interval")
    renewal = {"id": NIL, "kind": "renewal", "request": {"previous_interval_id": str(NIL)}}
    prepaid_command_views.links(db_conn, [renewal])
    ((query, params),) = seen
    assert "::text" not in query
    # an OR of two keys needs a bitmap scan, which plan() turns off
    db_conn.execute("SET enable_seqscan = off")
    try:
        text = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query, params))
    finally:
        db_conn.execute("RESET enable_seqscan")
    assert "Index Cond: (id = ANY (" in text, text


@pytest.mark.parametrize("queue", list(metrics.QUEUES))
def test_a_metrics_queue_count_reads_its_partial_indexes(db_conn, queue):
    """count(*) FILTER (...) read every row ever queued (prepaid_command: 150 ms at 100k)."""
    text = plan(db_conn, metrics.queue_query(queue))
    assert "Seq Scan" not in text, text


def test_a_uuid_filter_keeps_its_type_and_an_unknown_label_matches_nothing(db_conn):
    conds, params = _sql.conditions(db_conn, "balance_action", {"billing_account_id": NIL})
    assert [c.as_string(db_conn) for c in conds] == ['"billing_account_id" = %s']
    assert params == [NIL]
    assert _sql.conditions(db_conn, "subscriber_number", {"administrative_state": "x"}) is None
