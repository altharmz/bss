"""P4h1: the worker completes a fee-free transfer - principal moves, the hold is released, the
action and its command complete in one transaction; revalidation failure fails the action
and releases the hold. Lock order is the API's, so races cannot deadlock either way."""

import threading
import time
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest

from app.clock import FixedClock
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from app.worker import prepaid
from tests.test_api_topup import remaining
from tests.test_api_transfer import TRANSFER, pair, post, reserved
from tests.test_events import subscribe, types
from tests.test_prepaid_posting import TZ
from tests.test_prepaid_renewal_producer import tick


def status(api, moved):
    return api.get(f"{TRANSFER}/{moved['id']}").json()


def test_the_worker_moves_the_principal_and_completes_the_transfer(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    assert remaining(api, tx) == D(40) and reserved(api, tx) == 10
    tick(db_conn, api.clock.now())
    done = status(api, moved)
    assert done["status"] == "completed" and "confirmationDate" in done
    assert remaining(api, tx) == D(40) and reserved(api, tx) == 0
    assert remaining(api, rx) == D(18) and reserved(api, rx) == 0
    assert db_conn.execute(
        "SELECT state, result->>'outcome' FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone() == ("completed", "completed")
    assert db_conn.execute(
        "SELECT (SELECT coalesce(sum(amount), 0) FROM funding_consumption WHERE action_id = %(a)s),"
        " (SELECT count(*) FROM funding_source WHERE action_id = %(a)s AND kind = 'transfer'"
        "  AND billing_account_id = %(r)s AND amount = 10),"
        " (SELECT count(*) FROM funding_hold WHERE id = (SELECT hold_id FROM balance_action"
        "  WHERE id = %(a)s) AND released_at IS NOT NULL)",
        {"a": moved["id"], "r": rx.id},
    ).fetchone() == (D(10), 1, 1)
    # the ledger: the sender lost the principal, the receiver gained it
    assert funding.load(db_conn, UUID(tx.id)).ledger == D(40)
    assert funding.load(db_conn, UUID(rx.id)).ledger == D(18)
    # a second wake-up changes nothing
    tick(db_conn, api.clock.now())
    assert remaining(api, tx) == D(40) and remaining(api, rx) == D(18)
    assert db_conn.execute("SELECT count(*) FROM funding_consumption").fetchone()[0] >= 1


def test_the_received_money_is_spendable_and_can_be_sent_back(api, db_conn):
    tx, rx = pair(api, db_conn)
    post(api, tx, rx, 10)
    tick(db_conn, api.clock.now())
    back = post(api, rx, tx, 18).json()
    assert back["status"] == "created"
    tick(db_conn, api.clock.now())
    assert status(api, back)["status"] == "completed"
    assert remaining(api, rx) == D(0) and remaining(api, tx) == D(58)


def test_a_transfer_with_a_fee_stays_queued_until_its_fee_posting_exists(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "created"
    assert db_conn.execute(
        "SELECT state, attempts FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone() == ("pending", 0)
    assert remaining(api, tx) == D(39) and remaining(api, rx) == D(8)


@pytest.mark.parametrize("who, state", [("tx", "Suspended"), ("tx", "Closed"), ("rx", "Closed")])
def test_revalidation_failure_fails_the_action_and_releases_the_hold(api, db_conn, who, state):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    subscribe(api, "TMF654")
    db_conn.execute(
        "UPDATE billing_account SET state = %s WHERE id = %s",
        (state, (tx if who == "tx" else rx).id),
    )
    db_conn.commit()
    prepaid.process_transfers(db_conn, clock=FixedClock(api.clock.now()), tz=TZ)
    db_conn.commit()
    failed = status(api, moved)
    assert failed["status"] == "failed" and failed["description"]
    assert reserved(api, tx) == 0 and remaining(api, tx) == D(50) and remaining(api, rx) == D(8)
    assert db_conn.execute(
        "SELECT state, result->>'outcome' FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone() == ("completed", "failed")
    assert db_conn.execute("SELECT count(*) FROM funding_consumption").fetchone() == (0,)
    assert types(db_conn, "TMF654") == ["TransferBalanceFailureEvent"]
    assert api.delete(f"{TRANSFER}/{moved['id']}").status_code == 204


def test_a_crash_before_the_end_leaves_the_transfer_exactly_as_accepted(api, db_conn, monkeypatch):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10).json()
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after completion")

    monkeypatch.setattr(commands, "complete", crash)
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "created"
    assert reserved(api, tx) == 10 and remaining(api, tx) == D(40) and remaining(api, rx) == D(8)
    assert db_conn.execute(
        "SELECT (SELECT count(*) FROM funding_consumption),"
        " (SELECT count(*) FROM funding_source WHERE kind = 'transfer')"
    ).fetchone() == (0, 0)
    assert db_conn.execute(
        "SELECT state, attempts, error FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone() == ("failedTechnical", 1, "after completion")
    # the retry window opens, the hold is intact, and the second attempt completes it
    monkeypatch.setattr(commands, "complete", original)
    later = api.clock.now().replace(microsecond=0) + timedelta(minutes=1)
    tick(db_conn, later)
    assert status(api, moved)["status"] == "completed"
    assert remaining(api, rx) == D(18) and reserved(api, tx) == 0


@pytest.mark.parametrize("sender_is_lower", [True, False])
def test_completion_and_an_api_style_locker_cannot_deadlock(
    api, db_conn, migrated_db_url, sender_is_lower
):
    """The locker takes the lower account, then the higher (the API's order) while the
    worker completes a transfer between the same two accounts. If the worker locked its
    sender first, a higher-id sender would hold the higher account and wait for the lower:
    a deadlock. In id order the locker's second lock is granted at once."""
    first, second = pair(api, db_conn)
    low, high = sorted((first, second), key=lambda a: UUID(a.id))
    sender, receiver = (low, high) if sender_is_lower else (high, low)
    moved = post(api, sender, receiver, 5).json()
    assert moved["status"] == "created"
    worker_db.release_ownership(db_conn)
    db_conn.commit()
    errors = []

    def worker():
        try:
            with psycopg.connect(migrated_db_url) as conn:
                prepaid.process_transfers(conn, clock=FixedClock(api.clock.now()), tz=TZ)
        except Exception as exc:
            errors.append(exc)

    with psycopg.connect(migrated_db_url) as locker:
        locker.execute("SELECT 1 FROM billing_account WHERE id = %s FOR UPDATE", (low.id,))
        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(1.5)
        assert thread.is_alive(), "the worker must wait for the lower account"
        locker.execute("SET LOCAL lock_timeout = '5s'")
        locker.execute("SELECT 1 FROM billing_account WHERE id = %s FOR UPDATE", (high.id,))
        locker.commit()
    thread.join(timeout=15)
    assert not thread.is_alive() and errors == []
    assert status(api, moved)["status"] == "completed"
