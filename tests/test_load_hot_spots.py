"""The per-account paths the 100k load-test seed found scanning (#292) use indexes, and a
new prepaid account is created without the UPDATE that fired the account-mode guard."""

import pytest

from app.repository import buckets, parties
from tests import apidata


def plan(conn, query, params) -> str:
    conn.execute("SET enable_seqscan = off")  # an index that can serve it must be chosen
    try:
        text = "\n".join(r[0] for r in conn.execute("EXPLAIN " + query, params))
    finally:
        conn.execute("RESET enable_seqscan")
    return text


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
    assert index in plan(db_conn, query, (apidata.NIL,))


def test_the_bucket_list_filters_by_account_through_an_index(api, db_conn, monkeypatch):
    seen = []
    real = buckets.fetch_all

    def recording(conn, query, params=None):
        seen.append((query, params))
        return real(conn, query, params)

    monkeypatch.setattr(buckets, "fetch_all", recording)
    acc = apidata.account(api)
    url = f"/tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id={acc['id']}"
    assert api.get(url).status_code == 200
    (query, params), *_ = seen
    text = plan(db_conn, query.as_string(db_conn), params)
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
