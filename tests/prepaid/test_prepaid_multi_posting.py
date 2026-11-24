"""Multi-product purchases retain per-product bills and atomic command effects."""

import base64
import json
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest
from psycopg import sql

from app.billing import prepaid
from app.repository import billing, funding
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests.builders.billing.settlement_db import make_payment
from tests.builders.prepaid.prepaid_posting import TZ
from tests.factories import NOW, account, offering, product


def prepared(conn, *, zero=False, changes=None):
    acc = account(conn)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    off = offering(conn)
    make_payment(conn, acc, "40")
    quotes = []
    for unit, amount, tax in [("weekly", "10", "2"), ("monthly", "5", "1")]:
        prod = product(conn, acc["id"], off["id"], NOW.date())
        quotes.append(
            dict(
                product_id=str(prod["id"]),
                offering_id=str(off["id"]),
                price_id=str(uuid4()),
                currency="USD",
                amount=D(0 if zero else amount),
                taxes=[dict(category="VAT", rate=D(20), amount=D(0 if zero else tax))],
                anchor=NOW.date().isoformat(),
                index=0,
                unit=unit,
                length=1,
            )
        )
    quotes.sort(key=lambda q: UUID(q["product_id"]))
    if changes:
        changes(quotes)
    cmd = commands.enqueue(
        conn,
        uuid4(),
        acc["id"],
        kind="order",
        due_date=NOW.date(),
        request={"intervals": list(reversed(quotes))},
        now=NOW,
    )
    hold = funding.reserve_for_command(
        conn, acc["id"], D(0 if zero else 18), command_id=cmd["id"], held_at=NOW
    )
    return acc, cmd, hold


@pytest.mark.parametrize("delay,zero", [(0, False), (45, False), (0, True)])
def test_multi_purchase_bills_coverage_sources_and_replay(db_conn, delay, zero):
    acc, cmd, _ = prepared(db_conn, zero=zero)
    assert worker_db.acquire_ownership(db_conn)
    now = NOW + timedelta(days=delay)
    result = prepaid.post_interval(db_conn, cmd["id"], now=now, tz=TZ)
    db_conn.commit()
    assert len(result["purchases"]) == 2
    assert prepaid.post_interval(db_conn, cmd["id"], now=now + timedelta(days=7), tz=TZ) == result
    grants = db_conn.execute(
        "SELECT product_id, quote, lower(period), upper(period), anchor_date, interval_index"
        " FROM funded_interval ORDER BY product_id"
    ).fetchall()
    quotes = sorted(cmd["request"]["intervals"], key=lambda q: UUID(q["product_id"]))
    for grant, quote, purchase in zip(grants, quotes, result["purchases"], strict=True):
        assert grant[1] == quote
        assert grant[2] == grant[4] == now.date() and grant[5] == 0
        bill = billing.get_bill(db_conn, purchase["bill_id"])
        assert bill["state"] == "settled" and bill["run_type"] == "offCycle"
        assert bill["amount_due"] == D(str(quote["amount"])) * D("1.2")
        document = json.loads(base64.b64decode(bill["bill_document"]))
        assert document["billingPeriod"] == {"start": str(grant[2]), "end": str(grant[3])}
    assert len({g[3] for g in grants}) == 2
    assert db_conn.execute("SELECT count(*) FROM funding_hold_conversion").fetchone() == (1,)
    assert db_conn.execute("SELECT count(*) FROM funding_conversion_bill").fetchone() == (2,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0 if zero else 2,)
    assert db_conn.execute("SELECT count(*) FROM billing_run").fetchone() == (1,)
    assert funding.load(db_conn, acc["id"]).spendable == D(40 if zero else 22)


@pytest.mark.parametrize("stage", ["second_bill", "conversion", "completion"])
def test_late_failure_rolls_back_entire_order_tree(db_conn, monkeypatch, stage):
    _, cmd, hold = prepared(db_conn)
    db_conn.commit()
    assert worker_db.acquire_ownership(db_conn)
    module, name = {
        "second_bill": (prepaid.presentation, "snapshot_bill"),
        "conversion": (funding, "convert_bills_for_command"),
        "completion": (commands, "complete"),
    }[stage]
    original = getattr(module, name)
    calls = 0

    def crash(*args, **kwargs):
        nonlocal calls
        value = original(*args, **kwargs)
        calls += 1
        if stage != "second_bill" or calls == 2:
            raise RuntimeError("injected tree crash")
        return value

    monkeypatch.setattr(module, name, crash)
    with pytest.raises(RuntimeError, match="tree crash"):
        prepaid.post_interval(db_conn, cmd["id"], now=NOW, tz=TZ)
    for table in (
        "funded_interval",
        "customer_bill",
        "applied_customer_billing_rate",
        "billing_run",
        "funding_hold_conversion",
        "funding_conversion_bill",
        "bill_allocation",
        "event",
    ):
        assert db_conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone() == (0,)
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    assert db_conn.execute(
        "SELECT released_at FROM funding_hold WHERE id = %s", (hold,)
    ).fetchone() == (None,)
    monkeypatch.setattr(module, name, original)
    prepaid.post_interval(db_conn, cmd["id"], now=NOW, tz=TZ)
    db_conn.commit()


@pytest.mark.parametrize("case", ["duplicate", "currency", "unknown", "empty", "short_hold"])
def test_invalid_tree_never_partially_posts(db_conn, case):
    def change(quotes):
        if case == "duplicate":
            quotes[1] = quotes[0]
        elif case == "currency":
            quotes[1]["currency"] = "GBP"
        elif case == "unknown":
            quotes[1]["product_id"] = str(uuid4())
        elif case == "empty":
            quotes.clear()
        else:
            quotes[1]["amount"] = D(20)
            quotes[1]["taxes"][0]["amount"] = D(4)

    _, cmd, _ = prepared(db_conn, changes=change)
    assert worker_db.acquire_ownership(db_conn)
    with pytest.raises(ValueError):
        prepaid.post_interval(db_conn, cmd["id"], now=NOW, tz=TZ)
    assert db_conn.execute("SELECT count(*) FROM customer_bill").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert db_conn.execute("SELECT released_at FROM funding_hold").fetchone() == (None,)
