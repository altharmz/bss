"""Renewals are produced at paid-through; shortfalls suspend and credits resume."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import pytest
from psycopg.types.range import Range

from app.clock import FixedClock
from app.repository import catalog, payments
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from app.worker import loop
from app.worker import prepaid as worker
from tests.factories import NOW, T0
from tests.test_events import stored, subscribe
from tests.test_prepaid_dispatch import dispatch
from tests.test_prepaid_orders import purchase, shop
from tests.test_prepaid_posting import TZ, post, prepared


def tick(conn, now):
    result = loop.tick(conn, clock=FixedClock(now), tz=TZ, run_at=time(0, 30))
    conn.commit()
    return result


def pay(conn, account_id, amount, at):
    payments.insert_payment(
        conn,
        billing_account_id=account_id,
        amount=D(amount),
        currency="USD",
        received_at=at,
        payment_method="cash",
    )
    conn.commit()


def granted(conn):
    """One funded month and a current price, with no renewal produced yet."""
    acc, prod, cmd, _ = prepared(conn)
    assert worker_db.acquire_ownership(conn)
    first = post(conn, cmd)
    catalog.create_product_offering_price(
        conn,
        product_offering_id=prod["product_offering_id"],
        name="Current",
        price_type="recurring",
        amount=D(5),
        currency="USD",
        valid_for=Range(T0, None, "[)"),
        recurring_period="monthly",
        recurring_period_length=1,
        details={"tax": [{"taxCategory": "VAT", "taxRate": 20}]},
    )
    conn.commit()
    return first["interval_id"], NOW.replace(month=2)


def produce(conn, now):
    return worker.produce_renewals(conn, clock=FixedClock(now), tz=TZ)


def test_purchase_renew_run_out_top_up_resume(api, db_conn, monkeypatch):
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = shop(api, db_conn, funds="36")
    order = purchase(api, off, acc)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    grants = db_conn.execute(
        "SELECT id, product_id, upper(period) FROM funded_interval ORDER BY product_id"
    ).fetchall()
    due = grants[0][2]
    first, second = (g[1] for g in grants)
    subscribe(api, "TMF637", "ProductStateChangeEvent")

    at_due = datetime.combine(due, time(0, 5), TZ)
    result = tick(db_conn, at_due)
    renewals = [worker.renewal_id(g[0]) for g in grants]
    assert result.prepaid_renewals == renewals
    assert result.prepaid_commands == renewals
    assert commands.get(db_conn, renewals[0])["state"] == "completed"
    assert commands.get(db_conn, renewals[1])["state"] == "pending"
    assert commands.needs_topup(db_conn, renewals[1])["required"] == D(12)
    status = dict(db_conn.execute("SELECT id, status FROM product").fetchall())
    assert status == {first: "active", second: "suspended"}
    api.clock.set(at_due)
    view = api.get(f"/internal/v1/billingAccount/{acc['id']}/products").json()
    suspended = next(p for p in view if p["id"] == str(second))
    assert suspended["suspensionCause"] == "funding"
    assert suspended["paidThrough"] == due.isoformat()
    assert suspended["needs_topup"]["shortfall"] == 12

    # Same business date, no credit: nothing is produced or attempted again.
    later = tick(db_conn, at_due + timedelta(hours=1))
    assert later.prepaid_renewals == [] and later.prepaid_commands == []
    # Next business date: the once-per-day rescan tries again, still short.
    next_day = at_due + timedelta(days=1)
    assert tick(db_conn, next_day).prepaid_commands == [renewals[1]]
    assert db_conn.execute("SELECT count(*) FROM prepaid_funding_shortfall").fetchone() == (2,)
    assert tick(db_conn, next_day + timedelta(hours=1)).prepaid_commands == []

    pay(db_conn, UUID(acc["id"]), "12", next_day + timedelta(hours=2))
    resumed_at = next_day + timedelta(hours=2, minutes=1)
    assert tick(db_conn, resumed_at).prepaid_commands == [renewals[1]]
    assert commands.get(db_conn, renewals[1])["state"] == "completed"
    assert db_conn.execute(
        "SELECT lower(period), interval_index FROM funded_interval WHERE command_id = %s",
        (renewals[1],),
    ).fetchone() == (next_day.date(), 0)
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE product_id = %s", (second,)
    ).fetchone() == (2,)
    assert db_conn.execute("SELECT status FROM product WHERE id = %s", (second,)).fetchone() == (
        "active",
    )
    changes = [
        (e["event"]["product"]["id"], e["event"]["product"]["status"])
        for _, e in stored(db_conn, "TMF637")
    ]
    assert changes == [(str(second), "suspended"), (str(second), "active")]


def test_production_is_idempotent_and_keeps_paid_through_when_late(db_conn):
    first, now = granted(db_conn)
    assert produce(db_conn, now - timedelta(days=1)) == []
    late = now + timedelta(days=3)
    produced = produce(db_conn, late)
    assert produced == [worker.renewal_id(UUID(first))]
    assert produce(db_conn, late) == []
    row = commands.get(db_conn, produced[0])
    assert (row["kind"], row["due_date"], row["request"]) == (
        "renewal",
        now.date(),
        {"previous_interval_id": first},
    )
    assert dispatch(db_conn, late) == produced
    assert db_conn.execute(
        "SELECT lower(period), interval_index FROM funded_interval WHERE command_id = %s",
        (produced[0],),
    ).fetchone() == (late.date(), 0)


BLOCKS = {
    "manual": "UPDATE product SET manually_suspended = true",
    "account": "UPDATE billing_account SET state = 'Suspended'",
    "terminated": "UPDATE product SET status = 'terminated'",
}


@pytest.mark.parametrize("block", BLOCKS)
def test_suspension_causes_block_production_and_dispatch(db_conn, block):
    _, now = granted(db_conn)
    db_conn.execute(BLOCKS[block])
    db_conn.commit()
    assert produce(db_conn, now) == []
    db_conn.execute(BLOCKS[block].replace("true", "false").replace("'Suspended'", "'Active'"))
    if block == "terminated":
        db_conn.execute("UPDATE product SET status = 'active'")
    db_conn.commit()
    [produced] = produce(db_conn, now)
    db_conn.execute(BLOCKS[block])
    db_conn.commit()
    if block == "terminated":  # an ended product retires its renewal (P3g)
        assert dispatch(db_conn, now) == [produced]
        assert commands.get(db_conn, produced)["result"]["outcome"] == "cancelled"
        return
    assert dispatch(db_conn, now) == []
    assert commands.get(db_conn, produced)["attempts"] == 0
