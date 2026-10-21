"""Atomic posting behind unavailable prepaid creation, with no dispatcher."""

import base64
import json
from datetime import UTC, date, timedelta
from decimal import Decimal as D
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from psycopg import sql

from app.billing import prepaid
from app.repository import billing, funding
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests.factories import NOW, account, offering, product
from tests.test_settlement_db import make_payment

TZ = ZoneInfo("Europe/Moscow")


def prepared(conn, *, amount="10", tax="2", request_changes=None):
    acc = account(conn)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    off = offering(conn)
    prod = product(conn, acc["id"], off["id"], NOW.date())
    make_payment(conn, acc, "30")
    request = dict(
        product_id=str(prod["id"]),
        offering_id=str(off["id"]),
        price_id=str(uuid4()),
        currency="USD",
        amount=D(amount),
        taxes=[dict(category="VAT", rate=D(20), amount=D(tax))],
        anchor=NOW.date().isoformat(),
        index=0,
        unit="monthly",
        length=1,
    )
    request.update(request_changes or {})
    cmd = commands.enqueue(
        conn, uuid4(), acc["id"], kind="order", due_date=NOW.date(), request=request, now=NOW
    )
    hold = funding.reserve_for_command(
        conn, acc["id"], D(amount) + D(tax), command_id=cmd["id"], held_at=NOW
    )
    return acc, prod, cmd, hold


def post(conn, cmd, now=NOW):
    return prepaid.post_interval(conn, cmd["id"], now=now, tz=TZ)


def test_posting_tax_document_replay_and_daily_isolation(db_conn, api):
    acc, prod, cmd, hold = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    result = post(db_conn, cmd)
    db_conn.commit()
    assert post(db_conn, cmd, NOW + timedelta(days=4)) == result
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM applied_customer_billing_rate").fetchone() == (1,)
    bill = billing.get_bill(db_conn, result["bill_id"])
    assert bill["state"] == "settled" and bill["run_type"] == "offCycle"
    assert bill["amount_due"] == D(12) and bill["payment_due_date"] == NOW.date()
    document = json.loads(base64.b64decode(bill["bill_document"]))
    assert document["taxExcludedAmount"]["value"] == 10
    assert document["taxItem"][0]["taxAmount"]["value"] == 2
    assert document["billingPeriod"] == {"start": "2026-01-15", "end": "2026-02-15"}
    assert funding.load(db_conn, acc["id"]).spendable == D(18)
    assert billing.latest_run_date(db_conn) is None
    assert billing.get_run_by_id(db_conn, result["run_id"])["status"] == "completed"
    assert db_conn.execute(
        "SELECT authorized_at, amount, tax_amount FROM funded_interval"
    ).fetchone() == (NOW, D(10), D(2))
    body = api.get(f"/tmf-api/customerBillManagement/v5/customerBill/{result['bill_id']}").json()
    assert body["state"] == "settled" and "billCycle" not in body
    assert body["remainingAmount"]["value"] == 0
    assert db_conn.execute(
        "SELECT to_state FROM customer_bill_state_change ORDER BY seq"
    ).fetchall() == [("new",), ("validated",), ("sent",), ("settled",)]


def test_zero_purchase_needs_no_allocations(db_conn):
    _, _, cmd, _ = prepared(db_conn, amount="0", tax="0")
    assert worker_db.acquire_ownership(db_conn)
    post(db_conn, cmd)
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)
    assert db_conn.execute("SELECT amount FROM funded_interval").fetchone() == (D(0),)


@pytest.mark.parametrize("days", [1, 45, 400])
def test_delayed_start_keeps_accepted_money_and_buys_only_today(db_conn, days):
    acc, _, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    now = NOW + timedelta(days=days)
    result = post(db_conn, cmd, now)
    db_conn.commit()
    grant = db_conn.execute(
        "SELECT lower(period), upper(period), anchor_date, interval_index, quote, authorized_at"
        " FROM funded_interval"
    ).fetchone()
    assert grant[0] == grant[2] == now.date()
    assert (
        grant[1]
        == prepaid.IntervalQuote.model_validate(dict(cmd["request"], anchor=now.date().isoformat()))
        .period()
        .end
    )
    assert grant[3:] == (0, cmd["request"], now)
    assert commands.get(db_conn, cmd["id"])["request"] == cmd["request"]
    bill = billing.get_bill(db_conn, result["bill_id"])
    assert bill["amount_due"] == D(12) and bill["bill_date"] == now.date()
    document = json.loads(base64.b64decode(bill["bill_document"]))
    assert document["billingPeriod"]["start"] == now.date().isoformat()
    assert funding.load(db_conn, acc["id"]).spendable == D(18)
    assert post(db_conn, cmd, now + timedelta(days=3)) == result


def test_delayed_start_uses_authorization_business_date(db_conn):
    _, _, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    now = NOW.astimezone(UTC).replace(hour=22)
    post(db_conn, cmd, now)
    assert db_conn.execute("SELECT lower(period) FROM funded_interval").fetchone() == (
        now.date() + timedelta(days=1),
    )


def test_late_renewal_requires_fresh_quote_and_retains_hold(db_conn):
    acc, _, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    post(db_conn, cmd)
    now = NOW.replace(month=2)
    renewal = commands.enqueue(
        db_conn,
        uuid4(),
        acc["id"],
        kind="renewal",
        due_date=now.date(),
        request=dict(cmd["request"], index=1),
        now=now,
    )
    hold = funding.reserve_for_command(
        db_conn, acc["id"], D(12), command_id=renewal["id"], held_at=now
    )
    with pytest.raises(ValueError, match="authorization business date"):
        post(db_conn, renewal, now + timedelta(days=1))
    assert commands.get(db_conn, renewal["id"])["state"] == "pending"
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (None,)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)


def test_outer_rollback_restores_accepted_command_and_hold(db_conn):
    _, _, cmd, _ = prepared(db_conn)
    db_conn.commit()
    assert worker_db.acquire_ownership(db_conn)
    first = post(db_conn, cmd)
    db_conn.rollback()
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
    result = post(db_conn, cmd)
    db_conn.commit()
    assert result["interval_id"] != first["interval_id"]


def test_adjacent_renewal_keeps_anchor_and_its_own_command_run(db_conn):
    acc, _, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    first = post(db_conn, cmd)
    db_conn.commit()
    now = NOW.replace(month=2)
    request = dict(cmd["request"], index=1)
    renewal = commands.enqueue(
        db_conn, uuid4(), acc["id"], kind="renewal", due_date=now.date(), request=request, now=now
    )
    funding.reserve_for_command(db_conn, acc["id"], D(12), command_id=renewal["id"], held_at=now)
    second = post(db_conn, renewal, now)
    db_conn.commit()
    assert first["run_id"] != second["run_id"]
    assert db_conn.execute(
        "SELECT anchor_date, interval_index, lower(period), upper(period)"
        " FROM funded_interval ORDER BY interval_index"
    ).fetchall() == [
        (NOW.date(), 0, NOW.date(), date(2026, 2, 15)),
        (NOW.date(), 1, date(2026, 2, 15), date(2026, 3, 15)),
    ]
    assert funding.load(db_conn, acc["id"]).spendable == D(6)


@pytest.mark.parametrize("stage", ["snapshot", "conversion", "completion"])
@pytest.mark.parametrize("delay", [0, 45])
def test_failure_rolls_back_all_posting_and_retains_hold(db_conn, monkeypatch, stage, delay):
    _, _, cmd, hold = prepared(db_conn)
    db_conn.commit()
    assert worker_db.acquire_ownership(db_conn)
    module, name = {
        "snapshot": (prepaid.presentation, "snapshot_bill"),
        "conversion": (funding, "convert_for_command"),
        "completion": (commands, "complete"),
    }[stage]
    original = getattr(module, name)

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected crash")

    monkeypatch.setattr(module, name, fail)
    with pytest.raises(RuntimeError, match="injected crash"):
        post(db_conn, cmd, NOW + timedelta(days=delay))
    for table in (
        "billing_run",
        "applied_customer_billing_rate",
        "customer_bill",
        "funded_interval",
        "bill_allocation",
        "customer_bill_state_change",
        "funding_hold_conversion",
    ):
        assert db_conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone() == (0,)
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (None,)
    monkeypatch.setattr(module, name, original)
    post(db_conn, cmd, NOW + timedelta(days=delay))
    db_conn.commit()


@pytest.mark.parametrize(
    "changes",
    [
        {"unexpected": True},
        {"amount": "10.001"},
        {"amount": "NaN"},
        {"taxes": [dict(category="VAT", rate=20, amount=3)]},
        {"currency": "GBP"},
        {"anchor": "2026-01-14"},
        {"index": 1},
        {"product_id": str(uuid4())},
        {"offering_id": str(uuid4())},
    ],
)
def test_bad_quotes_leave_no_posting(db_conn, changes):
    _, _, cmd, _ = prepared(db_conn, request_changes=changes)
    assert worker_db.acquire_ownership(db_conn)
    with pytest.raises(ValueError):
        post(db_conn, cmd)
    assert db_conn.execute("SELECT count(*) FROM billing_run").fetchone() == (0,)


def test_ownership_hold_account_and_product_guards(db_conn):
    acc, prod, cmd, hold = prepared(db_conn)
    with pytest.raises(ValueError, match="ownership"):
        post(db_conn, cmd)
    assert worker_db.acquire_ownership(db_conn)
    db_conn.execute("UPDATE billing_account SET state = 'Suspended' WHERE id = %s", (acc["id"],))
    with pytest.raises(ValueError, match="active prepaid"):
        post(db_conn, cmd)
    db_conn.execute("UPDATE billing_account SET state = 'Active' WHERE id = %s", (acc["id"],))
    db_conn.execute("UPDATE product SET status = 'suspended' WHERE id = %s", (prod["id"],))
    with pytest.raises(ValueError, match="lifecycle"):
        post(db_conn, cmd)
    db_conn.execute("UPDATE product SET status = 'active' WHERE id = %s", (prod["id"],))
    funding.release(db_conn, acc["id"], hold, command_id=cmd["id"], released_at=NOW)
    with pytest.raises(ValueError, match="outstanding"):
        post(db_conn, cmd)


@pytest.mark.parametrize(
    "anchor,unit,length,index,start,end",
    [
        ("2026-01-31", "monthly", 1, 1, "2026-02-28", "2026-03-31"),
        ("2024-02-29", "yearly", 1, 3, "2027-02-28", "2028-02-29"),
        ("2026-03-07", "daily", 2, 0, "2026-03-07", "2026-03-09"),
        ("2026-03-07", "weekly", 2, 0, "2026-03-07", "2026-03-21"),
    ],
)
def test_prepaid_calendar_keeps_original_anchor(anchor, unit, length, index, start, end):
    quote = prepaid.IntervalQuote(
        product_id=uuid4(),
        offering_id=uuid4(),
        price_id=uuid4(),
        currency="USD",
        amount=0,
        taxes=[],
        anchor=anchor,
        unit=unit,
        length=length,
        index=index,
    )
    assert quote.period().start == date.fromisoformat(start)
    assert quote.period().end == date.fromisoformat(end)
