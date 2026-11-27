"""TMF654 balance actions (top-up, adjustment, hold, transfer): an unknown id, and a failed
action another session deletes between the request's first look and its lock (#507)."""

from uuid import uuid4

import pytest

from app.repository import parties
from tests.builders.prepaid import api_adjust, api_reserve, api_topup, api_transfer
from tests.builders.prepaid.api_topup import prepaid


@pytest.fixture
def rival(db_conn):
    """Another session; it fails rather than waits on a lock the request holds."""
    db_conn.execute("SET lock_timeout = '5s'")
    db_conn.commit()
    return db_conn


def close(db_conn, acc):
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (acc.id,))
    db_conn.commit()


def failed_topup(api, db_conn):
    _, acc = prepaid(api, db_conn)
    close(db_conn, acc)
    return api_topup.TOPUP, api_topup.post(api, acc)


def failed_adjustment(api, db_conn):
    _, acc = prepaid(api, db_conn)
    close(db_conn, acc)
    return api_adjust.ADJUST, api_adjust.post(api, acc, 5)


def failed_hold(api, db_conn):
    _, acc = prepaid(api, db_conn)
    close(db_conn, acc)
    return api_reserve.RESERVE, api_reserve.post(api, acc, 1)


def failed_transfer(api, db_conn):
    tx, rx = api_transfer.pair(api, db_conn)
    close(db_conn, tx)
    return api_transfer.TRANSFER, api_transfer.post(api, tx, rx, 5)


KINDS = {
    "topupBalance": failed_topup,
    "adjustBalance": failed_adjustment,
    "reserveBalance": failed_hold,
    "transferBalance": failed_transfer,
}


# the PATCH bodies the specs require: the id is looked up after them
PATCHES = {
    "reserveBalance": {"status": "cancelled", "reason": "x"},
    "transferBalance": {"reason": "x", "channel": {"id": "admin-ui"},
                        "logicalResource": [{"id": str(uuid4())}],
                        "receiverLogicalResource": {"id": str(uuid4())}},
}  # fmt: skip


@pytest.mark.parametrize("resource", sorted(KINDS))
def test_an_unknown_action_is_not_found(api, resource):
    url = f"{api_topup.BASE}/{resource}"
    nobody = str(uuid4())
    gone = api.delete(f"{url}/{nobody}")
    assert gone.status_code == 404, gone.text
    assert gone.json()["message"] == f"{resource} {nobody} not found"
    if resource in PATCHES:
        patched = api.patch(f"{url}/{nobody}", PATCHES[resource])
        assert patched.status_code == 404, patched.text
        assert patched.json()["message"] == f"{resource} {nobody} not found"


@pytest.mark.parametrize("resource", sorted(KINDS))
def test_a_failed_action_deleted_before_its_lock_is_not_found(
    api, db_conn, rival, monkeypatch, resource
):
    url, response = KINDS[resource](api, db_conn)
    assert response.status_code == 201 and response.json()["status"] == "failed", response.text
    uid = response.json()["id"]
    real = parties.get_billing_account

    def raced(conn, account_id, lock=None):
        if lock is not None:
            rival.execute("DELETE FROM balance_action WHERE id = %s", (uid,))
            rival.commit()
        return real(conn, account_id, lock)

    monkeypatch.setattr(parties, "get_billing_account", raced)
    gone = api.delete(f"{url}/{uid}")
    assert gone.status_code == 404, gone.text
    assert gone.json()["message"] == f"{resource} {uid} not found"
