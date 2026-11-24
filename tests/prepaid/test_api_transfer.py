"""P4g: TMF654 transferBalance acceptance - validation under both account locks, the sender's
hold and one queued `transfer` command. Nothing moves until the worker completes it (P4h1;
with a fee, P4h2).

Every response is checked against the vendored schema by the `api` client."""

from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest

from app.repository import funding
from tests import apidata
from tests.builders.prepaid.api_adjust import post as adjust
from tests.builders.prepaid.api_topup import BASE, remaining
from tests.builders.prepaid.api_transfer import TRANSFER, body, pair, post, reserved
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.tmf.events import subscribe, types
from tests.factories import NOW, prepaid_bucket


def stored(conn):
    """(transfers, transfer commands, holds still outstanding)."""
    return conn.execute(
        "SELECT (SELECT count(*) FROM balance_action WHERE kind = 'transfer'),"
        " (SELECT count(*) FROM prepaid_command WHERE kind = 'transfer'),"
        " (SELECT count(*) FROM funding_hold WHERE released_at IS NULL AND amount > 0)"
    ).fetchone()


def test_an_accepted_transfer_holds_principal_and_fee_and_moves_nothing(api, db_conn):
    tx, rx = pair(api, db_conn)
    holds = stored(db_conn)[2]
    subscribe(api, "TMF654")
    response = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1})
    assert response.status_code == 201, response.text
    moved = response.json()
    assert moved["status"] == "created" and moved["@type"] == "TransferBalance"
    assert moved["amount"] == {"amount": 10, "units": "USD"}
    assert moved["transferCost"] == {"unit": "USD", "value": 1}
    assert moved["costOwner"] == "originator" and "confirmationDate" not in moved
    assert moved["partyAccount"]["id"] == tx.id
    assert moved["bucket"]["id"] == tx.bucket and moved["receiverBucket"]["id"] == rx.bucket
    assert response.headers["Location"] == f"{TRANSFER}/{moved['id']}"
    # sender: 39 spendable, 11 held; receiver unchanged; the ledger has not moved
    assert remaining(api, tx) == D(39) and reserved(api, tx) == 11
    assert remaining(api, rx) == D(8) and reserved(api, rx) == 0
    action, command_id, hold_id = db_conn.execute(
        "SELECT id, command_id, hold_id FROM balance_action WHERE kind = 'transfer'"
    ).fetchone()
    assert str(action) == moved["id"]
    assert db_conn.execute(
        "SELECT kind, state, billing_account_id::text, request->>'action_id'"
        " FROM prepaid_command WHERE id = %s",
        (command_id,),
    ).fetchone() == ("transfer", "pending", tx.id, str(action))
    assert db_conn.execute(
        "SELECT amount FROM funding_hold WHERE id = %s", (hold_id,)
    ).fetchone() == (D(11),)
    assert db_conn.execute(
        "SELECT count(*) FROM funding_command_hold WHERE command_id = %s", (command_id,)
    ).fetchone() == (1,)
    assert stored(db_conn)[2] == holds + 1
    assert types(db_conn, "TMF654") == ["TransferBalanceCreateEvent"]
    assert api.get(f"{TRANSFER}/{moved['id']}").json() == moved
    listed = api.get(TRANSFER, params={"partyAccount.id": tx.id, "status": "created"})
    assert [t["id"] for t in listed.json()] == [moved["id"]]
    assert [t["id"] for t in api.get(TRANSFER, params={"receiverBucket.id": rx.bucket}).json()] == [
        moved["id"]
    ]
    assert api.get(f"{BASE}/reserveBalance/{moved['id']}").status_code == 404


def test_the_worker_completes_an_accepted_fee_transfer_with_its_fee(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    tick(db_conn, api.clock.now())
    assert api.get(f"{TRANSFER}/{moved['id']}").json()["status"] == "completed"
    assert db_conn.execute(
        "SELECT state, attempts FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone() == ("completed", 1)
    assert remaining(api, tx) == D(39) and remaining(api, rx) == D(18)


def test_a_receiver_paid_fee_holds_only_the_principal(api, db_conn):
    tx, rx = pair(api, db_conn)
    response = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}, costOwner="receiver")
    assert response.status_code == 201, response.text
    assert response.json()["costOwner"] == "receiver"
    assert remaining(api, tx) == D(40) and reserved(api, tx) == 10
    # a fee equal to the principal is the limit
    assert post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 10}, costOwner="receiver")
    assert stored(db_conn)[:2] == (2, 2)


def test_no_fee_means_zero_paid_by_the_originator(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    assert moved["transferCost"] == {"unit": "USD", "value": 0}
    assert moved["costOwner"] == "originator"
    assert reserved(api, tx) == 10


def test_the_transfer_must_fit_the_spendable_funds_with_its_fee(api, db_conn):
    tx, rx = pair(api, db_conn)
    subscribe(api, "TMF654")
    refused = post(api, tx, rx, 50, transferCost={"unit": "USD", "value": 1}).json()
    assert refused["status"] == "failed" and "spendable" in refused["description"]
    assert stored(db_conn) == (1, 0, stored(db_conn)[2])
    assert reserved(api, tx) == 0
    assert types(db_conn, "TMF654") == ["TransferBalanceFailureEvent"]
    # the receiver pays the fee: 50 fits exactly
    assert post(api, tx, rx, 50, transferCost={"unit": "USD", "value": 1}, costOwner="receiver")
    assert remaining(api, tx) == D(0) and reserved(api, tx) == 50
    assert post(api, tx, rx, 1).json()["status"] == "failed"


def test_a_hold_of_the_transfer_stops_other_spending(api, db_conn):
    tx, rx = pair(api, db_conn)
    assert post(api, tx, rx, 45).json()["status"] == "created"
    assert adjust(api, tx, -6).json()["status"] == "failed"
    assert adjust(api, tx, -5).json()["status"] == "completed"
    assert funding.load(db_conn, UUID(tx.id)).reserved == D(45)


def test_refusals_are_stored_failed_without_a_hold_or_a_command(api, db_conn):
    tx, rx = pair(api, db_conn)
    eur = apidata.account(api)
    db_conn.execute("UPDATE billing_account SET currency = 'EUR' WHERE id = %s", (eur["id"],))
    eur_bucket = prepaid_bucket(db_conn, eur["id"], NOW)
    db_conn.commit()
    cases = {
        "sameBucket": {"receiverBucket": {"id": tx.bucket}},
        "currency": {"receiverBucket": {"id": str(eur_bucket["id"])}},
        # the number is not the receiving account's
        "number": {"receiverLogicalResource": {"id": str(tx.number)}},
        "logicalResource": {"logicalResource": [{"id": str(rx.number)}]},
    }
    subscribe(api, "TMF654")
    for code, over in cases.items():
        response = post(api, tx, rx, 5, **over)
        assert response.status_code == 201, (code, response.text)
        refused = response.json()
        assert refused["status"] == "failed" and code in refused["description"], (code, refused)
        assert api.delete(f"{TRANSFER}/{refused['id']}").status_code == 204
    assert stored(db_conn)[:2] == (0, 0) and reserved(api, tx) == 0
    assert types(db_conn, "TMF654") == ["TransferBalanceFailureEvent"] * len(cases)


@pytest.mark.parametrize("state, who", [("Closed", "tx"), ("Suspended", "tx"), ("Closed", "rx")])
def test_account_states_refuse_a_transfer(api, db_conn, state, who):
    tx, rx = pair(api, db_conn)
    db_conn.execute(
        "UPDATE billing_account SET state = %s WHERE id = %s",
        (state, (tx if who == "tx" else rx).id),
    )
    db_conn.commit()
    refused = post(api, tx, rx, 5)
    assert refused.status_code == 201 and refused.json()["status"] == "failed", refused.text
    assert stored(db_conn)[:2] == (1, 0)


def test_a_suspended_receiver_still_receives(api, db_conn):
    tx, rx = pair(api, db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Suspended' WHERE id = %s", (rx.id,))
    db_conn.commit()
    assert post(api, tx, rx, 5).json()["status"] == "created"


def test_malformed_transfers_store_nothing(api, db_conn):
    tx, rx = pair(api, db_conn)
    cost = {"unit": "USD", "value": 1}
    bad = [
        {"amount": {"amount": 0, "units": "USD"}},
        {"amount": {"amount": -1, "units": "USD"}},
        {"amount": {"amount": 1.234, "units": "USD"}},
        {"amount": {"amount": 1, "units": "EUR"}},
        {"usageType": "bundle"},
        {"receiverBucketUsageType": "bundle"},
        {"bucket": {"id": str(uuid4())}},
        {"receiverBucket": {"id": str(uuid4())}},
        {"receiverLogicalResource": {"id": str(uuid4())}},
        {"logicalResource": [{"id": str(uuid4())}]},
        {"logicalResource": []},
        {"receiverProduct": {"id": str(uuid4())}},
        {"transferCost": {"unit": "EUR", "value": 1}},
        {"transferCost": {"unit": "USD", "value": -1}},
        {"transferCost": {"unit": "USD", "value": 0.005}},
        {"transferCost": {"unit": "USD", "value": 11}, "costOwner": "receiver"},
        {"transferCost": cost, "costOwner": "network"},
        {"partyAccount": {"id": tx.id}},
    ]
    for over in bad:
        response = post(api, tx, rx, 10, **over)
        assert response.status_code in (400, 422), (over, response.text)
    for missing in ("reason", "channel", "logicalResource", "receiverLogicalResource", "amount"):
        data = body(tx, rx)
        del data[missing]
        assert api.post(TRANSFER, data).status_code in (400, 422), missing
    assert stored(db_conn)[:2] == (0, 0)


def test_patch_changes_only_the_reason_and_a_transfer_is_final(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    url = f"{TRANSFER}/{moved['id']}"
    keep = {
        "reason": "Corrected reason",
        "channel": moved["channel"],
        "logicalResource": moved["logicalResource"],
        "receiverLogicalResource": moved["receiverLogicalResource"],
    }
    response = api.patch(url, keep)
    assert response.status_code == 200, response.text
    assert response.json()["reason"] == "Corrected reason"
    assert response.json()["status"] == "created"
    repeated = api.patch(url, {**keep, "amount": moved["amount"], "status": "created"})
    assert repeated.status_code == 200, repeated.text
    for over in (
        {"amount": {"amount": 11, "units": "USD"}},
        {"bucket": {"id": rx.bucket}},
        {"receiverBucket": {"id": tx.bucket}},
        {"transferCost": {"unit": "USD", "value": 2}},
        {"costOwner": "receiver"},
        {"channel": {"id": "other", "name": "Other"}},
        {"receiverLogicalResource": {"id": str(tx.number)}},
    ):
        assert api.patch(url, {**keep, **over}).status_code == 409, over
    # there is no cancelling: no status but its own, and no delete
    assert api.patch(url, {**keep, "status": "cancelled"}).status_code in (409, 422)
    assert api.delete(url).status_code == 409
    for missing in keep:
        partial = {k: v for k, v in keep.items() if k != missing}
        assert api.patch(url, partial).status_code in (400, 422), missing
    assert api.get(url).json()["status"] == "created" and reserved(api, tx) == 10
    assert stored(db_conn)[:2] == (1, 1)


def test_idempotency_key_replays_one_transfer(api, db_conn):
    tx, rx = pair(api, db_conn)
    first = post(api, tx, rx, 10, headers={"Idempotency-Key": "transfer-1"})
    replay = post(api, tx, rx, 10, headers={"Idempotency-Key": "transfer-1"})
    assert first.status_code == replay.status_code == 201 and first.json() == replay.json()
    assert stored(db_conn)[:2] == (1, 1) and reserved(api, tx) == 10
    assert post(api, tx, rx, 11, headers={"Idempotency-Key": "transfer-1"}).status_code == 409


def test_transfers_in_both_directions_each_hold_their_own_sender(api, db_conn):
    tx, rx = pair(api, db_conn)
    forward = post(api, tx, rx, 10)
    backward = post(api, rx, tx, 8)
    assert forward.json()["status"] == backward.json()["status"] == "created"
    assert reserved(api, tx) == 10 and reserved(api, rx) == 8
    assert remaining(api, tx) == D(40) and remaining(api, rx) == D(0)
    assert post(api, rx, tx, 1).json()["status"] == "failed"


def test_an_account_with_a_queued_transfer_cannot_be_closed(api, db_conn):
    tx, rx = pair(api, db_conn)
    post(api, tx, rx, 10)
    response = api.patch(f"{apidata.ACCOUNT}/{tx.id}", {"state": "Closed"})
    assert response.status_code == 409, response.text
