"""P4f: TMF654 reserveBalance, an operator hold on a prepaid bucket's spendable funds.

Every response is checked against the vendored schema by the `api` client."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

from app.repository import funding
from tests import apidata
from tests.apidata import ORDER
from tests.builders.api_adjust import post as adjust
from tests.builders.api_reserve import RESERVE, post
from tests.builders.api_resource_inventory import URL as NUMBERS
from tests.builders.api_resource_inventory import number
from tests.builders.api_topup import BASE, prepaid, remaining
from tests.builders.api_topup import post as topup
from tests.builders.events import subscribe, types
from tests.builders.number_orders import refs
from tests.builders.prepaid_orders import purchase
from tests.builders.prepaid_renewal_producer import tick


def ends_in(api, **delta):
    return {"endDateTime": (api.clock.now() + timedelta(**delta)).isoformat()}


def cancel(api, hold, **over):
    return api.patch(
        f"{RESERVE}/{hold['id']}", {"status": "cancelled", "reason": "Released", **over}
    )


def order(api, off, acc):
    """The prepaid purchase of two products worth 24 with tax, on the same two numbers."""
    items = []
    for i in range(2):
        item = apidata.add_item(off, str(i + 1))
        found = api.get(f"{NUMBERS}?value=7999123400{i}").json()
        number_id = found[0]["id"] if found else number(api, value=f"7999123400{i}")["id"]
        item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number_id)}
        items.append(item)
    response = api.post(ORDER, apidata.order_body(acc, *items))
    assert response.status_code == 201, response.text
    return response.json()


def reserved(api, acc):
    return api.get(f"{BASE}/bucket/{acc.bucket}").json()["reservedValue"]["amount"]


def test_a_hold_pins_spendable_funds_without_moving_the_ledger(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    subscribe(api, "TMF654")
    response = post(api, acc, 4, reason="Dispute", validFor=ends_in(api, days=2))
    assert response.status_code == 201, response.text
    hold = response.json()
    assert hold["status"] == "created" and hold["@type"] == "ReserveBalance"
    assert hold["amount"] == {"amount": 4, "units": "USD"} and hold["reason"] == "Dispute"
    assert hold["partyAccount"]["id"] == acc.id and hold["validFor"]["endDateTime"]
    assert "confirmationDate" not in hold
    assert response.headers["Location"] == f"{RESERVE}/{hold['id']}"
    assert remaining(api, acc) == D(6) and reserved(api, acc) == 4
    assert db_conn.execute(
        "SELECT count(*) FROM funding_hold WHERE released_at IS NULL"
    ).fetchone() == (1,)
    assert types(db_conn, "TMF654") == ["ReserveBalanceCreateEvent"]
    assert api.get(f"{RESERVE}/{hold['id']}").json()["id"] == hold["id"]
    listed = api.get(RESERVE, params={"partyAccount.id": acc.id, "status": "created"})
    assert [h["id"] for h in listed.json()] == [hold["id"]]
    assert api.get(f"{BASE}/topupBalance/{hold['id']}").status_code == 404


def test_a_hold_above_the_spendable_funds_is_stored_failed(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    assert post(api, acc, 6).status_code == 201
    subscribe(api, "TMF654")
    response = post(api, acc, 4.01)
    assert response.status_code == 201, response.text
    refused = response.json()
    assert refused["status"] == "failed" and "spendable" in refused["reason"]
    assert reserved(api, acc) == 6
    assert types(db_conn, "TMF654") == ["ReserveBalanceFailureEvent"]
    assert api.delete(f"{RESERVE}/{refused['id']}").status_code == 204
    assert api.get(f"{RESERVE}/{refused['id']}").status_code == 404


def test_a_hold_on_a_closed_account_or_a_foreign_number_is_stored_failed(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    other = post(api, acc, 1, logicalResource=[{"id": str(uuid4())}])
    assert other.status_code == 422, other.text
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()
    closed = post(api, acc, 1)
    assert closed.status_code == 201 and closed.json()["status"] == "failed"
    assert "closed" in closed.json()["reason"]


def test_malformed_holds_store_nothing(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    bad = [
        {"amount": {"amount": 0, "units": "USD"}},
        {"amount": {"amount": -1, "units": "USD"}},
        {"amount": {"amount": 1.234, "units": "USD"}},
        {"amount": {"amount": 1, "units": "EUR"}},
        {"usageType": "bundle"},
        {"partyAccount": {"id": str(uuid4())}},
        {"bucket": {"id": str(uuid4())}},
        {"validFor": ends_in(api, seconds=-1)},
        {
            "validFor": {
                **ends_in(api, days=1),
                "startDateTime": ends_in(api, hours=1)["endDateTime"],
            }
        },
        {"validFor": {"endDateTime": "tomorrow"}},
    ]
    for over in bad:
        response = post(api, acc, 1, **over)
        assert response.status_code in (400, 422), (over, response.text)
    assert db_conn.execute("SELECT count(*) FROM balance_action").fetchone()[0] == 1  # the top-up


def test_a_hold_stops_a_debit_until_it_is_cancelled(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    hold = post(api, acc, 7).json()
    assert adjust(api, acc, -3.01).json()["status"] == "failed"
    subscribe(api, "TMF654")
    released = cancel(api, hold, description="Settled")
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "cancelled" and released.json()["reason"] == "Released"
    assert released.json()["description"] == "Settled"
    assert types(db_conn, "TMF654") == ["ReserveBalanceCancelEvent"]
    assert reserved(api, acc) == 0 and remaining(api, acc) == D(10)
    assert adjust(api, acc, -10).json()["status"] == "completed"
    # cancelling again changes nothing and sends nothing
    again = cancel(api, hold, description="Typo")
    assert again.status_code == 200 and again.json()["description"] == "Typo"
    assert types(db_conn, "TMF654").count("ReserveBalanceCancelEvent") == 1


def test_patch_states_and_deletes(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    hold = post(api, acc, 2).json()
    url = f"{RESERVE}/{hold['id']}"
    assert api.patch(url, {"status": "created"}).status_code in (400, 422)  # reason is required
    same = api.patch(url, {"status": "created", "reason": "again", "amount": hold["amount"]})
    assert same.status_code == 200 and same.json()["reason"] == "again"
    for over in ({"amount": {"amount": 3, "units": "USD"}}, {"bucket": {"id": str(uuid4())}}):
        assert api.patch(url, {"status": "created", "reason": "x", **over}).status_code == 409
    assert api.patch(url, {"status": "completed", "reason": "x"}).status_code == 409
    assert api.delete(url).status_code == 409  # cancel a created hold instead
    assert cancel(api, hold).status_code == 200
    assert api.patch(url, {"status": "created", "reason": "x"}).status_code == 409
    assert api.delete(url).status_code == 409  # a cancelled hold is history


def test_a_hold_lapses_at_its_end_before_the_next_spend(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    hold = post(api, acc, 8, validFor=ends_in(api, hours=1)).json()
    subscribe(api, "TMF654")
    assert adjust(api, acc, -5).json()["status"] == "failed"
    # half-open: at the end instant it has lapsed
    api.clock.set(api.clock.now() + timedelta(hours=1))
    spent = adjust(api, acc, -5)
    assert spent.json()["status"] == "completed", spent.text
    gone = api.get(f"{RESERVE}/{hold['id']}").json()
    assert gone["status"] == "cancelled"
    assert types(db_conn, "TMF654")[-2:] == [
        "ReserveBalanceCancelEvent",
        "AdjustBalanceCreateEvent",
    ]
    assert reserved(api, acc) == 0 and remaining(api, acc) == D(5)


def test_a_lapsed_hold_cannot_be_cancelled_again_but_is_read_as_cancelled(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    hold = post(api, acc, 8, validFor=ends_in(api, minutes=5)).json()
    api.clock.set(api.clock.now() + timedelta(minutes=6))
    response = cancel(api, hold)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert db_conn.execute(
        "SELECT count(*) FROM funding_hold WHERE released_at IS NULL"
    ).fetchone() == (0,)


def test_the_worker_releases_a_lapsed_hold_without_any_spend(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    kept = post(api, acc, 3).json()
    hold = post(api, acc, 4, validFor=ends_in(api, hours=1)).json()
    subscribe(api, "TMF654")
    tick(db_conn, api.clock.now() + timedelta(minutes=30))
    assert api.get(f"{RESERVE}/{hold['id']}").json()["status"] == "created"
    tick(db_conn, api.clock.now() + timedelta(hours=1, minutes=1))
    assert api.get(f"{RESERVE}/{hold['id']}").json()["status"] == "cancelled"
    assert api.get(f"{RESERVE}/{kept['id']}").json()["status"] == "created"  # no end: stays
    assert types(db_conn, "TMF654") == ["ReserveBalanceCancelEvent"]
    assert funding.load(db_conn, UUID(acc.id)).reserved == D(3)


def test_a_hold_keeps_an_order_from_spending_it_until_cancelled(api, db_conn):
    off, acc = prepaid(api, db_conn, funds="24")
    hold = post(api, acc, 1).json()
    short = order(api, off, acc)
    assert short["state"] == "rejected"
    assert short["productOrderErrorMessage"][0]["code"] == "insufficientFunds"
    assert cancel(api, hold).status_code == 200
    assert order(api, off, acc)["state"] == "inProgress"


def test_a_lapsed_hold_does_not_keep_an_order_from_spending(api, db_conn):
    off, acc = prepaid(api, db_conn, funds="24")
    lapsing = post(api, acc, 1, validFor=ends_in(api, hours=1)).json()
    api.clock.set(api.clock.now() + timedelta(hours=2))
    assert purchase(api, off, acc)["state"] == "inProgress"
    assert api.get(f"{RESERVE}/{lapsing['id']}").json()["status"] == "cancelled"


def test_a_hold_an_order_owns_is_not_this_resources_to_change(api, db_conn):
    off, acc = prepaid(api, db_conn, funds="24")
    purchase(api, off, acc)
    hold_id, command_id = db_conn.execute(
        "SELECT hold_id, command_id FROM funding_command_hold"
    ).fetchone()
    owned = db_conn.execute(
        "INSERT INTO balance_action (kind, status, billing_account_id, currency, bucket_id,"
        " request, amount, operator, requested_at, hold_id, command_id)"
        " SELECT 'reservation', 'created', billing_account_id, currency, %s, '{}', amount,"
        " 'system', held_at, id, %s FROM funding_hold WHERE id = %s RETURNING id",
        (acc.bucket, command_id, hold_id),
    ).fetchone()[0]
    db_conn.commit()
    response = api.patch(f"{RESERVE}/{owned}", {"status": "cancelled", "reason": "x"})
    assert response.status_code == 409, response.text
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold_id,)
    ).fetchone() == (None,)


def test_idempotency_key_replays_one_hold(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 10)
    first = post(api, acc, 4, headers={"Idempotency-Key": "reserve-1"})
    replay = post(api, acc, 4, headers={"Idempotency-Key": "reserve-1"})
    assert first.status_code == replay.status_code == 201 and first.json() == replay.json()
    assert reserved(api, acc) == 4
    changed = post(api, acc, 5, headers={"Idempotency-Key": "reserve-1"})
    assert changed.status_code == 409
