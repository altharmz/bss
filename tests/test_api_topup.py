"""P4d: TMF654 topupBalance, its TMF676 payment, and the top-up a direct payment records.

Every response is checked against the vendored schema by the `api` client."""

from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import loop
from tests import apidata
from tests.apiclient import decimals
from tests.factories import NOW, payment_hold, prepaid_bucket
from tests.test_events import subscribe, types
from tests.test_number_persistence import number as unassigned_number
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_renewal_producer import tick

BASE = "/tmf-api/prepayBalanceManagement/v4"
TOPUP = f"{BASE}/topupBalance"
PAYMENT = "/tmf-api/payment/v4/payment"


class Acc(dict):
    """The account as the API answered it, with attribute access and its bucket id."""

    __getattr__ = dict.__getitem__


def prepaid(api, db_conn, funds="0"):
    """A prepaid account with its bucket (prepaid creation through the API waits for P5a)."""
    off, acc = shop(api, db_conn, funds)
    bucket = prepaid_bucket(db_conn, acc["id"], NOW)
    db_conn.commit()
    return off, Acc(acc, bucket=str(bucket["id"]))


def body(acc, value=50, **over):
    return {
        "@type": "TopupBalance",
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": acc.bucket},
        "partyAccount": {"id": acc.id, "@referredType": "BillingAccount"},
        **over,
    }


def post(api, acc, value=50, headers=None, **over):
    return api.post(TOPUP, body(acc, value, **over), headers=headers)


def remaining(api, acc):
    response = api.get(f"{BASE}/bucket/{acc.bucket}")
    assert response.status_code == 200, response.text
    return decimals(response)["remainingValue"]["amount"]


def current_balance(api, acc):
    (entry,) = decimals(api.get(f"{apidata.ACCOUNT}/{acc.id}"))["accountBalance"]
    return entry["amount"]["value"]


def counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM balance_action), (SELECT count(*) FROM payment)"
    ).fetchone()


def test_a_top_up_credits_the_bucket_with_its_payment(api, db_conn):
    _, acc = prepaid(api, db_conn)
    subscribe(api, "TMF654")
    subscribe(api, "TMF676")
    response = post(
        api,
        acc,
        50.25,
        reason="Cash at the counter",
        channel={"id": "admin-ui", "name": "Admin UI"},
        paymentMethod={"id": "till-3", "name": "Till 3"},
    )
    assert response.status_code == 201, response.text
    topup = decimals(response)
    assert topup["status"] == "completed" and topup["@type"] == "TopupBalance"
    assert topup["amount"] == {"amount": D("50.25"), "units": "USD"}
    assert topup["isAutoTopup"] is False and topup["reason"] == "Cash at the counter"
    assert topup["bucket"]["id"] == acc.bucket
    assert topup["partyAccount"]["id"] == acc.id
    assert topup["channel"] == {"id": "admin-ui", "name": "Admin UI"}
    assert {"requestedDate", "confirmationDate"} <= set(topup)
    assert response.headers["Location"] == f"{TOPUP}/{topup['id']}"
    assert api.get(f"{TOPUP}/{topup['id']}").json()["id"] == topup["id"]

    # one done payment, named for the method sent, and the money is spendable
    (payment,) = db_conn.execute(
        "SELECT p.id, p.amount, p.status, p.payment_method_name, p.payment_method_type,"
        " a.payment_id FROM payment p JOIN balance_action a ON a.payment_id = p.id"
    ).fetchall()
    assert payment[1:5] == (D("50.25"), "done", "Till 3", "PaymentMethodRef")
    assert remaining(api, acc) == current_balance(api, acc) == D("50.25")
    assert types(db_conn, "TMF654") == ["TopupBalanceCreateEvent"]
    assert types(db_conn, "TMF676") == ["PaymentCreateEvent"]

    listed = api.get(TOPUP, params={"partyAccount.id": acc.id, "status": "completed"})
    assert [t["id"] for t in listed.json()] == [topup["id"]]
    assert api.get(TOPUP, params={"status": "cancelled"}).json() == []


def test_an_unspecified_method_is_not_implied_to_be_collected_money(api, db_conn):
    _, acc = prepaid(api, db_conn)
    assert post(api, acc).status_code == 201
    assert db_conn.execute(
        "SELECT payment_method_name, payment_method_type FROM payment"
    ).fetchone() == ("Unspecified recorded receipt", "PaymentMethodRef")


@pytest.mark.parametrize(
    "over",
    [
        {"usageType": "voice"},
        {"isAutoTopup": True},
        {"recurringPeriod": "monthly"},
        {"numberOfPeriods": 3},
        {"voucher": "ABC-123"},
        {"amount": {"amount": 0, "units": "USD"}},
        {"amount": {"amount": -5, "units": "USD"}},
        {"amount": {"amount": "5", "units": "USD"}},
        {"amount": {"amount": 5.001, "units": "USD"}},
        {"amount": {"amount": 5, "units": "EUR"}},
        {"amount": {"amount": 5}},
        {"bucket": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"partyAccount": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"paymentMethod": {"name": "no id"}},
        {"product": [{"id": "00000000-0000-0000-0000-000000000000"}]},
        {"logicalResource": [{"id": "00000000-0000-0000-0000-000000000000"}]},
        {"status": "cancelled"},
    ],
    ids=lambda o: next(iter(o)) + str(next(iter(o.values())))[:12],
)
def test_malformed_or_unsupported_input_stores_nothing(api, db_conn, over):
    _, acc = prepaid(api, db_conn)
    response = api.post(TOPUP, body(acc, **over))
    assert response.status_code in (400, 422), response.text
    assert counts(db_conn) == (0, 0)


def test_a_bucket_of_another_account_is_a_mismatch(api, db_conn):
    _, acc = prepaid(api, db_conn)
    other = apidata.account(api)
    response = api.post(TOPUP, body(acc, partyAccount={"id": other["id"]}))
    assert response.status_code == 422, response.text
    assert counts(db_conn) == (0, 0)


def test_a_closed_account_refuses_with_a_failure_and_the_money_is_not_taken(api, db_conn):
    _, acc = prepaid(api, db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()
    subscribe(api, "TMF654")
    response = post(api, acc)
    assert response.status_code == 201, response.text
    refused = response.json()
    assert refused["status"] == "failed" and "closed" in refused["reason"]
    assert counts(db_conn) == (1, 0)
    assert types(db_conn, "TMF654") == ["TopupBalanceFailureEvent"]
    # a refused request is deleted like a rejected order; a client's own reason is kept
    assert api.delete(f"{TOPUP}/{refused['id']}").status_code == 204
    assert counts(db_conn) == (0, 0)
    assert post(api, acc, reason="mine").json()["reason"] == "mine"


def test_a_number_that_is_not_the_accounts_is_refused_not_rejected(api, db_conn):
    _, acc = prepaid(api, db_conn)
    number = unassigned_number(db_conn, "12025550177")
    db_conn.commit()
    response = post(api, acc, logicalResource=[{"id": str(number)}])
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert counts(db_conn) == (1, 0)


def test_a_suspended_account_still_receives_money(api, db_conn):
    _, acc = prepaid(api, db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Suspended' WHERE id = %s", (acc.id,))
    db_conn.commit()
    assert post(api, acc).json()["status"] == "completed"


def test_cancelling_reverses_unspent_money_once(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup = post(api, acc, 40).json()
    subscribe(api, "TMF654")
    url = f"{TOPUP}/{topup['id']}"
    response = api.patch(url, {"@type": "TopupBalance", "status": "cancelled", "reason": "Oops"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled" and response.json()["reason"] == "Oops"
    assert remaining(api, acc) == current_balance(api, acc) == 0
    # the payment is history: still done, never edited, no cash payout invented
    assert db_conn.execute("SELECT status, amount FROM payment").fetchall() == [("done", D(40))]
    assert db_conn.execute("SELECT count(*) FROM refund").fetchone() == (0,)
    assert db_conn.execute("SELECT amount FROM balance_action_reversal").fetchall() == [(D(-40),)]
    assert types(db_conn, "TMF654") == ["TopupBalanceCancelEvent"]
    # repeating the status is a descriptive edit, not a second reversal
    again = api.patch(url, {"status": "cancelled", "description": "Mistyped amount"})
    assert again.status_code == 200 and again.json()["description"] == "Mistyped amount"
    assert db_conn.execute("SELECT count(*) FROM balance_action_reversal").fetchone() == (1,)
    assert api.patch(url, {"status": "completed"}).status_code == 409
    assert api.delete(url).status_code == 409


def test_a_held_or_allocated_top_up_cannot_be_cancelled(api, db_conn):
    _, acc = prepaid(api, db_conn)
    held = post(api, acc, 40).json()
    payment_id = db_conn.execute("SELECT payment_id FROM balance_action").fetchone()[0]
    hold = payment_hold(db_conn, acc.id, payment_id, D(5), NOW)
    db_conn.commit()
    url = f"{TOPUP}/{held['id']}"
    assert api.patch(url, {"status": "cancelled"}).status_code == 409
    assert db_conn.execute("SELECT status FROM balance_action").fetchone() == ("completed",)
    assert remaining(api, acc) == D(35)

    db_conn.execute("UPDATE funding_hold SET released_at = %s WHERE id = %s", (NOW, hold))
    db_conn.commit()
    assert api.patch(url, {"status": "cancelled"}).status_code == 200
    assert funding.load(db_conn, UUID(acc.id)).spendable == 0


def test_a_top_up_whose_money_was_spent_stays(api, db_conn):
    off, acc = prepaid(api, db_conn)
    topup = post(api, acc, 24).json()
    purchase(api, off, acc)
    response = api.patch(f"{TOPUP}/{topup['id']}", {"status": "cancelled"})
    assert response.status_code == 409, response.text
    assert db_conn.execute("SELECT count(*) FROM balance_action_reversal").fetchone() == (0,)


def test_patch_changes_only_descriptions_and_repeats_the_rest(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup = post(api, acc, 10, reason="first").json()
    url = f"{TOPUP}/{topup['id']}"
    ok = api.patch(url, {"status": "completed", "reason": "second", "amount": topup["amount"]})
    assert ok.status_code == 200 and ok.json()["reason"] == "second"
    for over in (
        {"amount": {"amount": 11, "units": "USD"}},
        {"bucket": {"id": "00000000-0000-0000-0000-000000000000"}},
        {"channel": {"id": "other"}},
    ):
        assert api.patch(url, {"status": "completed", **over}).status_code == 409, over
    assert api.patch(url, {"reason": "no status"}).status_code in (400, 422)
    assert api.patch(url, {"status": "created"}).status_code == 409
    assert api.patch(f"{TOPUP}/{apidata.NIL}", {"status": "cancelled"}).status_code == 404


def test_a_failed_top_up_cannot_be_cancelled_and_a_completed_one_not_deleted(api, db_conn):
    _, acc = prepaid(api, db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()
    failed = post(api, acc).json()
    assert api.patch(f"{TOPUP}/{failed['id']}", {"status": "cancelled"}).status_code == 409


def test_the_same_key_and_body_replay_one_top_up(api, db_conn):
    _, acc = prepaid(api, db_conn)
    key = {"Idempotency-Key": "topup-1"}
    first = post(api, acc, 10, headers=key)
    again = post(api, acc, 10, headers=key)
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Location"] == first.headers["Location"]
    assert counts(db_conn) == (1, 1)
    assert post(api, acc, 11, headers=key).status_code == 409
    assert counts(db_conn) == (1, 1)
    assert remaining(api, acc) == D(10)


def test_deleting_a_failed_top_up_retires_its_key(api, db_conn):
    _, acc = prepaid(api, db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()
    key = {"Idempotency-Key": "topup-2"}
    refused = post(api, acc, headers=key).json()
    assert api.delete(f"{TOPUP}/{refused['id']}").status_code == 204
    assert post(api, acc, headers=key).status_code == 409


def test_a_direct_payment_records_exactly_one_linked_top_up(api, db_conn):
    _, acc = prepaid(api, db_conn)
    subscribe(api, "TMF654")
    response = api.post(PAYMENT, apidata.payment_body(acc, 30))
    assert response.status_code == 201, response.text
    (action,) = db_conn.execute(
        "SELECT status, amount, payment_id, operator FROM balance_action"
    ).fetchall()
    assert action[:3] == ("completed", D(30), UUID(response.json()["id"]))
    listed = api.get(TOPUP).json()
    assert [t["status"] for t in listed] == ["completed"]
    description = listed[0]["description"]
    assert response.json()["id"] in description and "bankTransfer" in description
    assert remaining(api, acc) == D(30)
    assert types(db_conn, "TMF654") == ["TopupBalanceCreateEvent"]
    # and, being a top-up, it can be cancelled while unspent
    assert api.patch(f"{TOPUP}/{listed[0]['id']}", {"status": "cancelled"}).status_code == 200
    assert remaining(api, acc) == 0


def test_a_replayed_payment_creates_no_second_top_up(api, db_conn):
    _, acc = prepaid(api, db_conn)
    payment = apidata.payment_body(acc, 30)
    key = {"Idempotency-Key": "pay-1"}
    first = api.post(PAYMENT, payment, headers=key)
    again = api.post(PAYMENT, payment, headers=key)
    assert first.status_code == again.status_code == 201 and again.json() == first.json()
    assert counts(db_conn) == (1, 1)
    assert api.post(PAYMENT, apidata.payment_body(acc, 31), headers=key).status_code == 409
    assert counts(db_conn) == (1, 1)


def test_other_payments_record_no_top_up(api, db_conn):
    postpaid = apidata.account(api)
    assert api.post(PAYMENT, apidata.payment_body(postpaid, 30)).status_code == 201
    # prepaid fixtures without a bucket (the API cannot create either yet) are untouched
    _, bare = shop(api, db_conn, "0")
    assert api.post(PAYMENT, apidata.payment_body(bare, 30)).status_code == 201
    assert counts(db_conn) == (0, 2)


def test_a_top_up_resumes_a_funding_suspended_renewal(api, db_conn, monkeypatch):
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = prepaid(api, db_conn)
    assert post(api, acc, 24).status_code == 201
    order = purchase(api, off, acc)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    due = db_conn.execute("SELECT max(upper(period)) FROM funded_interval").fetchone()[0]
    at_due = api.clock.now().replace(year=due.year, month=due.month, day=due.day, hour=0, minute=5)
    result = tick(db_conn, at_due)
    assert db_conn.execute("SELECT count(*) FROM product WHERE status = 'suspended'").fetchone()[0]
    pending = [c for c in result.prepaid_commands if commands.get(db_conn, c)["state"] == "pending"]
    assert len(pending) == 2
    api.clock.set(at_due + timedelta(hours=2))

    assert post(api, acc, 24).status_code == 201
    resumed = tick(db_conn, at_due + timedelta(hours=2, minutes=1))
    assert sorted(resumed.prepaid_commands) == sorted(pending)
    assert {commands.get(db_conn, c)["state"] for c in pending} == {"completed"}
    assert db_conn.execute(
        "SELECT count(*) FROM product WHERE status = 'suspended'"
    ).fetchone() == (0,)
