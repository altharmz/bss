"""P4e: TMF654 adjustBalance, a signed correction that credits or debits a prepaid bucket.

Every response is checked against the vendored schema by the `api` client."""

from decimal import Decimal as D

import pytest

from tests import apidata
from tests.test_api_topup import (
    BASE,
    TOPUP,
    current_balance,
    prepaid,
    remaining,
)
from tests.test_api_topup import post as topup
from tests.test_events import subscribe, types

ADJUST = f"{BASE}/adjustBalance"


def body(acc, value, **over):
    return {
        "@type": "AdjustBalance",
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": acc.bucket},
        **over,
    }


def post(api, acc, value, headers=None, **over):
    return api.post(ADJUST, body(acc, value, **over), headers=headers)


def counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM balance_action WHERE kind = 'adjustment'),"
        " (SELECT count(*) FROM funding_source), (SELECT count(*) FROM funding_consumption)"
    ).fetchone()


def test_a_credit_adds_spendable_funds_from_its_own_source(api, db_conn):
    _, acc = prepaid(api, db_conn)
    subscribe(api, "TMF654")
    response = post(api, acc, 12.5, reason="Goodwill", channel={"id": "admin-ui"})
    assert response.status_code == 201, response.text
    adjustment = response.json()
    assert adjustment["status"] == "completed" and adjustment["@type"] == "AdjustBalance"
    assert adjustment["amount"] == {"amount": 12.5, "units": "USD"}
    assert adjustment["adjustType"] == "oneTime" and adjustment["reason"] == "Goodwill"
    assert adjustment["partyAccount"]["id"] == acc.id and adjustment["bucket"]["id"] == acc.bucket
    assert response.headers["Location"] == f"{ADJUST}/{adjustment['id']}"
    assert remaining(api, acc) == current_balance(api, acc) == D("12.5")
    assert db_conn.execute("SELECT kind, amount FROM funding_source").fetchall() == [
        ("adjustment", D("12.50"))
    ]
    assert types(db_conn, "TMF654") == ["AdjustBalanceCreateEvent"]
    assert api.get(f"{ADJUST}/{adjustment['id']}").json()["id"] == adjustment["id"]
    listed = api.get(ADJUST, params={"partyAccount.id": acc.id, "status": "completed"})
    assert [a["id"] for a in listed.json()] == [adjustment["id"]]
    # a different action kind is a different resource
    assert api.get(f"{TOPUP}/{adjustment['id']}").status_code == 404
    assert api.get(TOPUP).json() == []


def test_a_debit_consumes_specific_sources_in_funding_order(api, db_conn):
    _, acc = prepaid(api, db_conn)
    assert topup(api, acc, 5).status_code == 201
    assert post(api, acc, 5).status_code == 201
    response = post(api, acc, -7)
    assert response.status_code == 201 and response.json()["status"] == "completed"
    assert response.json()["amount"]["amount"] == -7
    # the payment first, then the adjustment source, to the cent
    used = db_conn.execute(
        "SELECT (payment_id IS NOT NULL), amount FROM funding_consumption ORDER BY 1"
    ).fetchall()
    assert used == [(False, D(2)), (True, D(5))]
    assert remaining(api, acc) == current_balance(api, acc) == D(3)
    # only what is left can be debited
    assert post(api, acc, -3.01).json()["status"] == "failed"
    assert post(api, acc, -3).json()["status"] == "completed"
    assert remaining(api, acc) == 0


def test_a_debit_above_the_spendable_funds_is_stored_failed(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    subscribe(api, "TMF654")
    response = post(api, acc, -10.01)
    assert response.status_code == 201, response.text
    refused = response.json()
    assert refused["status"] == "failed" and "spendable" in refused["reason"]
    assert counts(db_conn) == (1, 0, 0)
    assert remaining(api, acc) == D(10)
    assert types(db_conn, "TMF654") == ["AdjustBalanceFailureEvent"]
    assert api.delete(f"{ADJUST}/{refused['id']}").status_code == 204
    assert counts(db_conn) == (0, 0, 0)


def test_unpaid_charges_and_holds_are_not_spendable(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    from tests.factories import NOW, payment_hold

    payment_id = db_conn.execute("SELECT payment_id FROM balance_action").fetchone()[0]
    payment_hold(db_conn, acc.id, payment_id, D(4), NOW)
    db_conn.commit()
    assert post(api, acc, -6.01).json()["status"] == "failed"
    assert post(api, acc, -6).json()["status"] == "completed"


def test_cancelling_a_credit_consumes_its_source_once(api, db_conn):
    _, acc = prepaid(api, db_conn)
    credit = post(api, acc, 9).json()
    subscribe(api, "TMF654")
    url = f"{ADJUST}/{credit['id']}"
    response = api.patch(url, {"@type": "AdjustBalance", "status": "cancelled", "reason": "Oops"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled" and response.json()["reason"] == "Oops"
    assert remaining(api, acc) == current_balance(api, acc) == 0
    assert db_conn.execute("SELECT amount FROM balance_action_reversal").fetchall() == [(D(-9),)]
    assert types(db_conn, "TMF654") == ["AdjustBalanceCancelEvent"]
    again = api.patch(url, {"status": "cancelled", "description": "Typo"})
    assert again.status_code == 200 and again.json()["description"] == "Typo"
    assert db_conn.execute("SELECT count(*) FROM balance_action_reversal").fetchone() == (1,)
    assert api.patch(url, {"status": "completed"}).status_code == 409
    assert api.delete(url).status_code == 409


def test_a_credit_that_was_spent_cannot_be_cancelled(api, db_conn):
    _, acc = prepaid(api, db_conn)
    credit = post(api, acc, 9).json()
    assert post(api, acc, -1).status_code == 201
    response = api.patch(f"{ADJUST}/{credit['id']}", {"status": "cancelled"})
    assert response.status_code == 409, response.text
    assert db_conn.execute("SELECT count(*) FROM balance_action_reversal").fetchone() == (0,)
    assert db_conn.execute(
        "SELECT status FROM balance_action WHERE id = %s", (credit["id"],)
    ).fetchone() == ("completed",)
    assert remaining(api, acc) == D(8)


def test_cancelling_a_debit_restores_the_exact_sources_once(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 5)
    post(api, acc, 5)
    debit = post(api, acc, -7).json()
    url = f"{ADJUST}/{debit['id']}"
    assert api.patch(url, {"status": "cancelled"}).status_code == 200
    assert remaining(api, acc) == current_balance(api, acc) == D(10)
    restored = db_conn.execute(
        "SELECT f.kind, f.amount, c.amount FROM funding_source f"
        " JOIN funding_consumption c ON c.id = f.consumption_id WHERE f.kind = 'reversal'"
        " ORDER BY f.amount"
    ).fetchall()
    assert restored == [("reversal", D(2), D(2)), ("reversal", D(5), D(5))]
    assert db_conn.execute("SELECT amount FROM balance_action_reversal").fetchall() == [(D(7),)]
    # repeating is not a second restoration, and the restored money is spendable again
    assert api.patch(url, {"status": "cancelled"}).status_code == 200
    assert db_conn.execute("SELECT count(*) FROM funding_source").fetchone()[0] == 3
    assert post(api, acc, -10).json()["status"] == "completed"
    assert remaining(api, acc) == 0


@pytest.mark.parametrize(
    "over",
    [
        {"usageType": "voice"},
        {"adjustType": "recurring"},
        {"adjustType": "weekly"},
        {"amount": {"amount": 0, "units": "USD"}},
        {"amount": {"amount": "5", "units": "USD"}},
        {"amount": {"amount": 5.001, "units": "USD"}},
        {"amount": {"amount": 5, "units": "EUR"}},
        {"bucket": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"partyAccount": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"product": [{"id": "00000000-0000-0000-0000-000000000000"}]},
        {"logicalResource": [{"id": "00000000-0000-0000-0000-000000000000"}]},
        {"status": "cancelled"},
        {"unknown": 1},
    ],
    ids=lambda o: next(iter(o)) + str(next(iter(o.values())))[:12],
)
def test_malformed_or_unsupported_input_stores_nothing(api, db_conn, over):
    _, acc = prepaid(api, db_conn)
    response = api.post(ADJUST, body(acc, 5, **over))
    assert response.status_code in (400, 422), response.text
    assert counts(db_conn) == (0, 0, 0)


def test_a_closed_account_refuses_both_directions(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()
    for value in (5, -5):
        refused = post(api, acc, value).json()
        assert refused["status"] == "failed" and "closed" in refused["reason"]
    assert counts(db_conn) == (2, 0, 0)


def test_patch_changes_only_descriptions_and_repeats_the_rest(api, db_conn):
    _, acc = prepaid(api, db_conn)
    adjustment = post(api, acc, 10, reason="first").json()
    url = f"{ADJUST}/{adjustment['id']}"
    ok = api.patch(url, {"status": "completed", "reason": "second", "amount": adjustment["amount"]})
    assert ok.status_code == 200 and ok.json()["reason"] == "second"
    for over in (
        {"amount": {"amount": 11, "units": "USD"}},
        {"bucket": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"channel": {"id": "other"}},
    ):
        assert api.patch(url, {"status": "completed", **over}).status_code == 409, over
    assert api.patch(url, {"reason": "no status"}).status_code in (400, 422)
    assert api.patch(url, {"status": "created"}).status_code == 409
    assert api.patch(f"{ADJUST}/{apidata.NIL}", {"status": "cancelled"}).status_code == 404


def test_the_same_key_and_body_replay_one_adjustment(api, db_conn):
    _, acc = prepaid(api, db_conn)
    key = {"Idempotency-Key": "adjust-1"}
    first = post(api, acc, 10, headers=key)
    again = post(api, acc, 10.00, headers=key)
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert post(api, acc, 11, headers=key).status_code == 409
    assert counts(db_conn) == (1, 1, 0)
    assert remaining(api, acc) == D(10)


def test_deleting_a_failed_adjustment_retires_its_key(api, db_conn):
    _, acc = prepaid(api, db_conn)
    key = {"Idempotency-Key": "adjust-2"}
    refused = post(api, acc, -1, headers=key).json()
    assert refused["status"] == "failed"
    assert api.delete(f"{ADJUST}/{refused['id']}").status_code == 204
    assert post(api, acc, -1, headers=key).status_code == 409
