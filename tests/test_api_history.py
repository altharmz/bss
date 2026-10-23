"""P4i: TMF654 balanceActionHistory, the events of the four actions, and refunds around holds.

Every response is checked against the vendored schema by the `api` client (the history's
receiverLogicalResource is waived for non-transfers, tests/contract.py)."""

from decimal import Decimal as D

import pytest

from app.events.types import EVENT_TYPES
from tests import apidata
from tests.test_api_adjust import ADJUST
from tests.test_api_adjust import post as adjust
from tests.test_api_reserve import RESERVE
from tests.test_api_reserve import post as reserve
from tests.test_api_topup import BASE, TOPUP, prepaid, remaining, unassigned_number
from tests.test_api_topup import post as topup
from tests.test_api_transfer import pair
from tests.test_api_transfer import post as transfer
from tests.test_events import subscribe, types

HISTORY = f"{BASE}/balanceActionHistory"
REFUND = "/tmf-api/payment/v4/refund"


def test_history_lists_every_action_once_with_its_id(api, db_conn):
    _, acc = prepaid(api, db_conn)
    first = topup(api, acc, 40).json()
    cancelled = topup(api, acc, 7).json()
    assert api.patch(f"{TOPUP}/{cancelled['id']}", {"status": "cancelled"}).status_code == 200
    credit = adjust(api, acc, 5).json()
    hold = reserve(api, acc, 3).json()
    failed = reserve(api, acc, 999).json()
    assert failed["status"] == "failed"
    # a direct payment on a prepaid account is a top-up too
    paid = apidata.payment(api, acc, 11)
    assert paid["status"] == "done"

    listed = api.get(HISTORY, params={"limit": 100})
    assert listed.status_code == 200, listed.text
    entries = listed.json()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)) == 6
    rows = db_conn.execute("SELECT id::text FROM balance_action").fetchall()
    assert sorted(ids) == sorted(r[0] for r in rows)
    assert listed.headers["X-Total-Count"] == "6"
    by_id = {e["id"]: e for e in entries}
    for action, kind in (
        (first, "TopupBalance"),
        (cancelled, "TopupBalance"),
        (credit, "AdjustBalance"),
        (hold, "ReserveBalance"),
        (failed, "ReserveBalance"),
    ):
        entry = by_id[action["id"]]
        assert entry["@type"] == kind and entry["@baseType"] == "BalanceActionHistory"
        assert entry["href"] == f"{HISTORY}/{action['id']}"
        assert entry["amount"] == action["amount"] and entry["bucket"]["id"] == acc.bucket
        assert "receiverLogicalResource" not in entry
    assert by_id[cancelled["id"]]["status"] == "cancelled"
    assert by_id[failed["id"]]["status"] == "failed"
    assert api.get(f"{HISTORY}/{first['id']}").json() == by_id[first["id"]]
    assert api.get(f"{HISTORY}/{apidata.NIL}").status_code == 404
    assert api.get(f"{HISTORY}/not-a-uuid").status_code in (400, 404)


def test_history_filters_pages_and_selects_fields(api, db_conn):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 40)
    adjust(api, acc, 5)
    reserve(api, acc, 3)
    page = api.get(HISTORY, params={"limit": 2})
    assert len(page.json()) == 2 and page.headers["X-Total-Count"] == "3"
    rest = api.get(HISTORY, params={"limit": 2, "offset": 2})
    assert len(rest.json()) == 1
    assert {e["id"] for e in page.json() + rest.json()} == {
        r[0] for r in db_conn.execute("SELECT id::text FROM balance_action").fetchall()
    }
    assert len(api.get(HISTORY, params={"status": "completed"}).json()) == 2
    assert len(api.get(HISTORY, params={"status": "created"}).json()) == 1
    assert len(api.get(HISTORY, params={"partyAccount.id": acc.id}).json()) == 3
    assert api.get(HISTORY, params={"partyAccount.id": apidata.NIL}).json() == []
    slim = api.get(f"{HISTORY}?fields=amount").json()[0]
    assert set(slim) == {"id", "href", "@type", "amount"}
    assert api.get(HISTORY, params={"bogus": "1"}).status_code == 400


def test_a_transfer_is_listed_with_its_receiver_and_both_sides_filter(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = transfer(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    entry = api.get(f"{HISTORY}/{moved['id']}").json()
    assert entry["@type"] == "TransferBalance" and entry["status"] == "created"
    assert entry["receiverLogicalResource"]["id"] == str(rx.number)
    assert entry["receiverBucket"]["id"] == rx.bucket
    assert entry["transferCost"] == {"unit": "USD", "value": 1}
    found = api.get(HISTORY, params={"receiverBucket.id": rx.bucket}).json()
    assert [e["id"] for e in found] == [moved["id"]]


def test_the_twelve_event_names_are_registered_and_eleven_are_sent(api, db_conn):
    expected = {
        f"{a}Balance{k}Event"
        for a in ("Topup", "Adjust", "Reserve", "Transfer")
        for k in ("Create", "Failure", "Cancel")
    }
    assert EVENT_TYPES["TMF654"] == expected and len(expected) == 12
    tx, rx = pair(api, db_conn)
    subscribe(api, "TMF654")
    # top-up: create, cancel, failure (a number that is not the account's)
    made = topup(api, tx, 5).json()
    assert api.patch(f"{TOPUP}/{made['id']}", {"status": "cancelled"}).status_code == 200
    stranger = unassigned_number(db_conn, "12025550188")
    db_conn.commit()
    assert topup(api, tx, 5, logicalResource=[{"id": str(stranger)}]).json()["status"] == "failed"
    # adjustment: create, cancel, failure (a debit above the spendable funds)
    credit = adjust(api, tx, 2).json()
    assert api.patch(f"{ADJUST}/{credit['id']}", {"status": "cancelled"}).status_code == 200
    assert adjust(api, tx, -9999).json()["status"] == "failed"
    # reservation: create, cancel, failure
    hold = reserve(api, tx, 2).json()
    released = api.patch(f"{RESERVE}/{hold['id']}", {"status": "cancelled", "reason": "Released"})
    assert released.status_code == 200
    assert reserve(api, tx, 9999).json()["status"] == "failed"
    # transfer: create, failure (sender and receiver bucket are one)
    assert transfer(api, tx, rx, 1).json()["status"] == "created"
    assert transfer(api, tx, tx, 1, receiverBucket={"id": tx.bucket}).json()["status"] == "failed"
    sent = set(types(db_conn, "TMF654"))  # every stored payload is checked against its listener
    assert sent == expected - {"TransferBalanceCancelEvent"}


@pytest.mark.parametrize("hold_amount, refund, status", [(30, 21, 409), (30, 20, 201)])
def test_a_refund_of_prepaid_cash_is_limited_to_funds_left_after_holds(
    api, db_conn, hold_amount, refund, status
):
    _, acc = prepaid(api, db_conn)
    topup(api, acc, 50)
    assert reserve(api, acc, hold_amount).status_code == 201
    response = api.post(
        REFUND,
        {
            "@type": "Refund",
            "account": {"id": acc.id},
            "totalAmount": {"unit": "USD", "value": refund},
            "paymentMethod": {"@type": "PaymentMethodRef", "name": "Cash"},
        },
    )
    assert response.status_code == status, response.text
    assert remaining(api, acc) == (D(20) - refund if status == 201 else D(20))
