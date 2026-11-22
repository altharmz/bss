"""P4h: the worker completes a transfer (P4h1 fee-free, P4h2 with its fee): principal moves,
the hold is released, the action and its command complete in one transaction; revalidation
failure fails the action and releases the hold. Lock order is the API's, so races cannot
deadlock either way."""

import base64
import json
import threading
import time
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql

from alembic import command
from app.clock import FixedClock
from app.repository import funding
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from app.worker import prepaid
from tests.builders.api_topup import remaining
from tests.builders.api_transfer import TRANSFER, pair, post, reserved
from tests.builders.events import subscribe, types
from tests.builders.prepaid_posting import TZ
from tests.builders.prepaid_renewal_producer import tick
from tests.builders.settlement_db import make_payment

BILL = "/tmf-api/customerBillManagement/v5"


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


def fee_rows(conn, action_id):
    """The fee's ABR and bill of a transfer, or None."""
    return conn.execute(
        "SELECT a.billing_account_id, a.charge_type::text, a.product_id, a.product_offering_id,"
        " a.amount, a.tax_amount, a.period, a.customer_bill_id, b.state::text, b.run_type,"
        " b.amount_due, b.billing_run_id, a.billing_run_id"
        " FROM applied_customer_billing_rate a JOIN customer_bill b ON b.id = a.customer_bill_id"
        " WHERE a.balance_action_id = %s",
        (action_id,),
    ).fetchone()


def test_an_originator_fee_is_charged_and_settled_from_the_rest_of_the_hold(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    assert remaining(api, tx) == D(39) and reserved(api, tx) == 11
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "completed"
    assert remaining(api, tx) == D(39) and reserved(api, tx) == 0
    assert remaining(api, rx) == D(18)
    payer, kind, product, offering, amount, tax, period, bill, state, run_type, due, r1, r2 = (
        fee_rows(db_conn, moved["id"])
    )
    today = api.clock.now().astimezone(TZ).date()
    assert (str(payer), kind, product, offering) == (tx.id, "oneTime", None, None)
    assert (amount, tax, state, run_type, due, r1 == r2) == (
        D(1),
        D(0),
        "settled",
        "offCycle",
        1,
        True,
    )
    assert (period.lower, period.upper) == (today, today + timedelta(days=1))
    # the whole hold is spent: 10 consumed as principal, 1 allocated to the fee bill
    assert db_conn.execute(
        "SELECT (SELECT sum(amount) FROM funding_consumption WHERE action_id = %(a)s),"
        " (SELECT sum(amount) FROM bill_allocation WHERE customer_bill_id = %(b)s)",
        {"a": moved["id"], "b": bill},
    ).fetchone() == (D(10), D(1))
    assert funding.load(db_conn, UUID(tx.id)).ledger == D(39)
    assert funding.load(db_conn, UUID(rx.id)).ledger == D(18)
    result = db_conn.execute(
        "SELECT result FROM prepaid_command WHERE kind = 'transfer'"
    ).fetchone()[0]
    assert result["fee_bill_id"] == str(bill) and result["fee_payer_id"] == tx.id
    assert db_conn.execute(
        "SELECT status::text, kind FROM billing_run WHERE id = %s", (r1,)
    ).fetchone() == ("completed", "prepaidCommand")
    tick(db_conn, api.clock.now())  # nothing is posted twice
    assert db_conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone()[0] >= 1
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE charge_type::text = 'oneTime'"
    ).fetchone() == (1,)


def test_a_fee_splits_a_source_between_the_principal_and_the_fee(api, db_conn):
    """The hold's sources are walked in order: the principal first, the rest pays the fee."""
    tx, rx = pair(api, db_conn)
    make_payment(db_conn, {"id": tx.id, "currency": "USD"}, "5")
    db_conn.commit()
    assert remaining(api, tx) == D(55)
    moved = post(api, tx, rx, 52, transferCost={"unit": "USD", "value": 1}).json()
    assert moved["status"] == "created", moved
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "completed"
    assert remaining(api, tx) == D(2) and remaining(api, rx) == D(60)
    both = db_conn.execute(
        "SELECT count(*) FROM (SELECT payment_id FROM funding_consumption WHERE action_id = %(a)s"
        " INTERSECT SELECT payment_id FROM bill_allocation WHERE customer_bill_id = ("
        " SELECT customer_bill_id FROM applied_customer_billing_rate"
        " WHERE balance_action_id = %(a)s)) x",
        {"a": moved["id"]},
    ).fetchone()[0]
    assert both == 1  # one payment gave part of its money to each


def test_a_receiver_fee_is_paid_from_the_received_source(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(
        api, tx, rx, 10, transferCost={"unit": "USD", "value": 3}, costOwner="receiver"
    ).json()
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "completed"
    assert remaining(api, tx) == D(40) and remaining(api, rx) == D(15)
    payer, kind, *_, state, run_type, due, _, _ = fee_rows(db_conn, moved["id"])
    assert (str(payer), kind, state, run_type, due) == (rx.id, "oneTime", "settled", "offCycle", 3)
    assert db_conn.execute(
        "SELECT f.kind, a.amount FROM bill_allocation a JOIN funding_source f"
        " ON f.id = a.funding_source_id WHERE a.billing_account_id = %s",
        (rx.id,),
    ).fetchall() == [("transfer", D(3))]
    assert funding.load(db_conn, UUID(rx.id)).ledger == D(15)
    assert funding.load(db_conn, UUID(tx.id)).ledger == D(40)


def test_a_receiver_fee_equal_to_the_principal_nets_nothing(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(
        api, tx, rx, 10, transferCost={"unit": "USD", "value": 10}, costOwner="receiver"
    ).json()
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "completed"
    assert remaining(api, tx) == D(40) and remaining(api, rx) == D(8)


def test_a_receiver_fee_above_the_principal_is_refused_at_acceptance(api, db_conn):
    tx, rx = pair(api, db_conn)
    response = post(
        api, tx, rx, 10, transferCost={"unit": "USD", "value": 11}, costOwner="receiver"
    )
    assert response.status_code == 422
    assert remaining(api, tx) == D(50) and reserved(api, tx) == 0


def test_two_fee_transfers_from_one_payer_on_one_day_both_complete(api, db_conn):
    tx, rx = pair(api, db_conn)
    first = post(api, tx, rx, 5, transferCost={"unit": "USD", "value": 1}).json()
    second = post(api, tx, rx, 6, transferCost={"unit": "USD", "value": 2}).json()
    tick(db_conn, api.clock.now())
    assert status(api, first)["status"] == status(api, second)["status"] == "completed"
    assert remaining(api, tx) == D(36) and remaining(api, rx) == D(19)
    assert db_conn.execute(
        "SELECT count(*), count(DISTINCT customer_bill_id) FROM applied_customer_billing_rate"
        " WHERE charge_type::text = 'oneTime'"
    ).fetchone() == (2, 2)


def test_a_failed_revalidation_posts_no_fee_and_releases_the_whole_hold(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    db_conn.execute("UPDATE billing_account SET state = 'Closed' WHERE id = %s", (rx.id,))
    db_conn.commit()
    prepaid.process_transfers(db_conn, clock=FixedClock(api.clock.now()), tz=TZ)
    db_conn.commit()
    assert status(api, moved)["status"] == "failed"
    assert reserved(api, tx) == 0 and remaining(api, tx) == D(50)
    assert fee_rows(db_conn, moved["id"]) is None


def test_a_crash_after_the_fee_posting_rolls_the_fee_back_too(api, db_conn, monkeypatch):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after completion")

    monkeypatch.setattr(commands, "complete", crash)
    tick(db_conn, api.clock.now())
    assert status(api, moved)["status"] == "created"
    assert reserved(api, tx) == 11 and remaining(api, tx) == D(39)
    assert fee_rows(db_conn, moved["id"]) is None
    assert db_conn.execute(
        "SELECT count(*) FROM billing_run WHERE command_id = (SELECT id FROM prepaid_command"
        " WHERE kind = 'transfer')"
    ).fetchone() == (0,)


def test_the_fee_reads_through_every_surface_without_a_product(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    tick(db_conn, api.clock.now())
    abr_id = db_conn.execute(
        "SELECT id FROM applied_customer_billing_rate WHERE balance_action_id = %s",
        (moved["id"],),
    ).fetchone()[0]
    bill_id = db_conn.execute(
        "SELECT customer_bill_id FROM applied_customer_billing_rate WHERE id = %s", (abr_id,)
    ).fetchone()[0]
    # TMF678 (the client validates each response against the vendored schema)
    rate = api.get(f"{BILL}/appliedCustomerBillingRate/{abr_id}").json()
    assert rate["appliedBillingRateType"] == "appliedBillingCharge" and "product" not in rate
    assert rate["name"] == "Transfer fee" and rate["isBilled"] is True
    assert rate["taxIncludedAmount"] == {"unit": "USD", "value": 1} and rate["appliedTax"] == []
    listed = api.get(f"{BILL}/appliedCustomerBillingRate", params={"billingAccount.id": tx.id})
    assert str(abr_id) in [r["id"] for r in listed.json()]
    bill = api.get(f"{BILL}/customerBill/{bill_id}").json()
    assert bill["state"] == "settled" and bill["runType"] == "offCycle"
    document = json.loads(base64.b64decode(bill["billDocument"][0]["content"]))
    assert [(c["name"], c["amount"]["value"]) for c in document["charges"]] == [
        ("Transfer fee", D(1))
    ]
    # the internal bill view, the account's charges and its running ledger
    detail = api.get(f"/internal/v1/customerBill/{bill_id}").json()
    assert [(c["kind"], c["product"], c["productOffering"]) for c in detail["charges"]] == [
        ("charge", None, None)
    ]
    charges = api.get(f"/internal/v1/billingAccount/{tx.id}/charges").json()
    fee = next(c for c in charges if c["id"] == str(abr_id))
    assert (fee["kind"], fee["product"], fee["productOffering"]) == ("charge", None, None)
    assert (fee["billableDays"], fee["periodDays"], fee["prorated"]) == (1, 1, False)
    assert fee["amount"] == {"unit": "USD", "value": 1}
    ledger = api.get(f"/internal/v1/billingAccount/{tx.id}/transactions").json()
    entry = next(t for t in ledger if t["id"] == str(abr_id))
    assert (entry["type"], entry["note"]) == ("charge", "Transfer fee")
    assert funding.load(db_conn, UUID(tx.id)).ledger == D(39)


def test_a_daily_run_over_an_account_holding_a_fee_stays_ok(api, db_conn):
    tx, rx = pair(api, db_conn)
    post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1})
    tick(db_conn, api.clock.now())
    assert tick(db_conn, api.clock.now() + timedelta(days=1)) is not None
    assert (
        db_conn.execute(
            "SELECT count(*) FROM billing_run_account"
            " WHERE status = 'ok' AND billing_account_id = %s",
            (tx.id,),
        ).fetchone()[0]
        >= 1
    )
    assert db_conn.execute(
        "SELECT count(*) FROM billing_run_account WHERE status <> 'ok'"
    ).fetchone() == (0,)


def test_the_database_scopes_the_checks_by_charge_type(api, db_conn):
    tx, rx = pair(api, db_conn)
    moved = post(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    tick(db_conn, api.clock.now())
    other = post(api, tx, rx, 5, transferCost={"unit": "USD", "value": 1}).json()  # no fee yet
    fee = db_conn.execute(
        "SELECT * FROM applied_customer_billing_rate WHERE balance_action_id = %s",
        (moved["id"],),
    ).fetchone()
    cols = [
        d.name
        for d in db_conn.execute("SELECT * FROM applied_customer_billing_rate LIMIT 0").description
    ]
    fee = dict(zip(cols, fee, strict=True))
    product = db_conn.execute(
        "SELECT id FROM product WHERE billing_account_id = %s", (tx.id,)
    ).fetchone()[0]
    base = {
        k: fee[k]
        for k in (
            "billing_account_id",
            "charge_type",
            "amount",
            "currency",
            "period",
            "billing_run_id",
            "applied_at",
            "product_id",
            "product_offering_id",
            "balance_action_id",
        )
    }

    def insert(**over):
        values = base | {"balance_action_id": None} | over
        with db_conn.transaction():
            db_conn.execute(
                sql.SQL("INSERT INTO applied_customer_billing_rate ({}) VALUES ({})").format(
                    sql.SQL(", ").join(map(sql.Identifier, values)),
                    sql.SQL(", ").join([sql.Placeholder()] * len(values)),
                ),
                tuple(values.values()),
            )

    offering = db_conn.execute(
        "SELECT product_offering_id FROM product WHERE id = %s", (product,)
    ).fetchone()[0]
    day = fee["period"]
    check = psycopg.errors.CheckViolation
    free = UUID(other["id"])
    # a oneTime rate needs its transfer, and has no product or offering
    with pytest.raises(check, match="abr_fee_scope"):
        insert()
    with pytest.raises(check, match="abr_fee_scope"):
        insert(balance_action_id=free, product_id=product)
    with pytest.raises(check, match="abr_fee_scope"):
        insert(balance_action_id=free, product_offering_id=offering)
    # recurring and refund rates still need both
    with pytest.raises(check, match="abr_fee_scope"):
        insert(charge_type="recurring", product_id=None, product_offering_id=None)
    with pytest.raises(check, match="abr_fee_scope"):
        insert(
            charge_type="recurring",
            balance_action_id=free,
            product_id=product,
            product_offering_id=offering,
        )
    # a fee is positive and lasts one day
    with pytest.raises(check, match="abr_fee_amount"):
        insert(balance_action_id=free, amount=D(0))
    with pytest.raises(check, match="abr_fee_amount"):
        insert(balance_action_id=free, amount=D(-1))
    with pytest.raises(check, match="abr_fee_amount"):
        insert(
            balance_action_id=free,
            period=psycopg.types.range.Range(day.lower, day.upper + timedelta(days=1), "[)"),
        )
    # one fee per transfer
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(balance_action_id=moved["id"])
    insert(balance_action_id=free)  # the same row shape is fine for a transfer without a fee


@pytest.mark.migration_history
def test_the_migration_refuses_to_lose_a_fee(temp_db_url):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0047")
    command.downgrade(cfg, "0046")
    command.upgrade(cfg, "0047")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("SET session_replication_role = replica")  # no whole transfer needed
        conn.execute(
            "INSERT INTO applied_customer_billing_rate (billing_account_id, charge_type, amount,"
            " currency, period, billing_run_id, applied_at, balance_action_id)"
            " VALUES (gen_random_uuid(), 'oneTime', 1, 'USD', '[2026-01-01,2026-01-02)',"
            " gen_random_uuid(), now(), gen_random_uuid())"
        )
        conn.commit()
    with pytest.raises(Exception, match="transfer fee charges must be retained"):
        command.downgrade(cfg, "0046")


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
