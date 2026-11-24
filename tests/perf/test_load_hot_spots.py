"""The per-account paths the 100k load-test seed found scanning (#292) use indexes, and a
new prepaid account is created without the UPDATE that fired the account-mode guard."""

import pytest

from app.repository import billing, buckets, parties
from tests import apidata
from tests.perf.guard import NO_SEQSCAN, plan, sent


@pytest.mark.parametrize(
    ("query", "index"),
    [
        (
            "SELECT * FROM funded_interval WHERE product_id = %s"
            " ORDER BY lower(period) DESC, authorized_at DESC LIMIT 1",
            "funded_interval_latest_idx",
        ),
        (
            "SELECT * FROM funding_hold_source WHERE hold_id = %s ORDER BY id",
            "funding_hold_source_hold_id_idx",
        ),
        (
            "SELECT * FROM funding_conversion_source WHERE billing_account_id = %s",
            "funding_conversion_source_billing_account_id_idx",
        ),
    ],
)
def test_the_hot_lookups_have_an_index(db_conn, query, index):
    assert index in plan(db_conn, query, (apidata.NIL,), off=NO_SEQSCAN)


def test_the_bucket_list_filters_by_account_through_an_index(api, db_conn, monkeypatch):
    seen = sent(monkeypatch, buckets)
    acc = apidata.account(api)
    url = f"/tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id={acc['id']}"
    assert api.get(url).status_code == 200
    (query, params), *_ = seen
    text = plan(db_conn, query, params, off=NO_SEQSCAN)
    assert "Seq Scan on prepaid_bucket" not in text, text


def test_a_prepaid_account_is_created_without_changing_its_mode(api, monkeypatch):
    """rating_type is in the INSERT: no UPDATE of it, which would run the account-mode guard
    (billing_account_mode_history, BEFORE UPDATE OF rating_type)."""
    real = parties.update_billing_account
    changed = []

    def recording(conn, account_id, changes):
        changed.extend(changes)
        return real(conn, account_id, changes)

    monkeypatch.setattr(parties, "update_billing_account", recording)
    customer = apidata.customer(api)
    response = api.post(apidata.ACCOUNT, apidata.account_body(customer, ratingType="prepaid"))
    assert response.status_code == 201, response.text
    assert response.json()["ratingType"] == "prepaid"
    assert "rating_type" not in changed


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_a_page_of_failed_or_blocked_outcomes_reads_the_unresolved_index(
    db_conn, monkeypatch, status
):
    """#333: the status filter compares the enum, so the partial index of the not-ok
    outcomes serves it; as text it read every outcome of a 100k-account run (37 ms)."""
    seen = sent(monkeypatch, billing)
    billing.page_run_outcomes(db_conn, apidata.NIL, status=status, offset=0, limit=20)
    ((query, params),) = seen
    text = plan(db_conn, query, params, off=NO_SEQSCAN)
    assert "billing_run_account_unresolved" in text, text


def test_a_page_of_cycles_reads_only_its_runs(db_conn, monkeypatch):
    """#333: the page's run ids restrict billing_run too; with the bills' side alone the
    planner hashed every run, prepaid command runs included (145 ms at 100k accounts)."""
    import uuid

    seen = sent(monkeypatch, billing)
    billing.run_payment_due_dates(db_conn, [uuid.uuid4(), uuid.uuid4()])
    ((query, params),) = seen
    text = plan(db_conn, query, params, off=NO_SEQSCAN)
    assert " on billing_run r" in text, text
    run_node = text.split(" on billing_run r", 1)[1].split("->", 1)[0]
    assert "ANY" in run_node, text
