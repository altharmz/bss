"""Actual-date renewal selection and atomic recovery, without a dispatcher."""

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
from psycopg.types.range import Range

from app.billing.funding import InsufficientFunds
from app.billing.model import MissingPrice
from app.repository import billing, catalog, funding
from app.repository import prepaid_commands as commands
from tests.builders.prepaid_posting import post
from tests.builders.prepaid_renewal import renewal
from tests.builders.settlement_db import make_payment
from tests.factories import NOW, T0


@pytest.mark.parametrize("delay", [0, 1, 400])
def test_renewal_selects_current_money_and_only_one_interval(db_conn, delay):
    acc, cmd, price, now = renewal(db_conn, delay=delay)
    result = post(db_conn, cmd, now)
    db_conn.commit()
    grant = db_conn.execute(
        "SELECT lower(period), anchor_date, interval_index, amount, tax_amount, quote"
        " FROM funded_interval WHERE command_id = %s",
        (cmd["id"],),
    ).fetchone()
    assert grant[:5] == (
        now.date(),
        now.date() if delay else NOW.date(),
        0 if delay else 1,
        D(5),
        D(1),
    )
    assert grant[5]["price_id"] == str(price["id"])
    assert commands.get(db_conn, cmd["id"])["request"] == cmd["request"]
    assert billing.get_bill(db_conn, result["bill_id"])["amount_due"] == D(6)
    assert funding.load(db_conn, acc["id"]).spendable == D(12)
    assert post(db_conn, cmd, now + timedelta(days=2)) == result
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)


@pytest.mark.parametrize("amount", ["0", "15"])
def test_renewal_zero_and_exact_tax_inclusive_funds(db_conn, amount):
    acc, cmd, _, now = renewal(db_conn, amount=amount, delay=1)
    result = post(db_conn, cmd, now)
    db_conn.commit()
    total = D(amount) * D("1.20")
    assert billing.get_bill(db_conn, result["bill_id"])["amount_due"] == total
    assert funding.load(db_conn, acc["id"]).spendable == D(18) - total


@pytest.mark.parametrize("unit,length", [("daily", 2), ("weekly", 2), ("yearly", 1)])
def test_changed_cadence_starts_full_interval_today(db_conn, unit, length):
    _, cmd, _, now = renewal(db_conn, unit=unit, length=length)
    post(db_conn, cmd, now)
    assert db_conn.execute(
        "SELECT anchor_date, interval_index FROM funded_interval WHERE command_id = %s",
        (cmd["id"],),
    ).fetchone() == (now.date(), 0)


def test_shortfall_retries_from_new_date_and_new_price(db_conn):
    acc, cmd, price, now = renewal(db_conn, amount="20")
    with pytest.raises(InsufficientFunds):
        post(db_conn, cmd, now)
    assert commands.get(db_conn, cmd["id"])["state"] == "pending"
    assert db_conn.execute("SELECT count(*) FROM funding_command_hold").fetchone() == (1,)
    make_payment(db_conn, acc, "20")
    db_conn.execute("UPDATE product_offering_price SET amount = 21 WHERE id = %s", (price["id"],))
    result = post(db_conn, cmd, now + timedelta(days=30))
    assert billing.get_bill(db_conn, result["bill_id"])["amount_due"] == D("25.20")
    assert db_conn.execute(
        "SELECT lower(period) FROM funded_interval WHERE command_id = %s", (cmd["id"],)
    ).fetchone() == ((now + timedelta(days=30)).date(),)


def test_missing_price_is_technical_and_does_not_reserve(db_conn):
    _, cmd, price, now = renewal(db_conn)
    db_conn.execute("DELETE FROM product_offering_price WHERE id = %s", (price["id"],))
    with pytest.raises(MissingPrice):
        post(db_conn, cmd, now)
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (1,)


def test_renewal_completion_failure_rolls_back_quote_hold_and_money(db_conn, monkeypatch):
    _, cmd, _, now = renewal(db_conn)
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash")

    monkeypatch.setattr(commands, "complete", crash)
    with pytest.raises(RuntimeError, match="crash"):
        post(db_conn, cmd, now)
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (1,)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    monkeypatch.setattr(commands, "complete", original)
    post(db_conn, cmd, now + timedelta(days=1))
    db_conn.commit()


def test_second_command_cannot_renew_an_obsolete_interval(db_conn):
    acc, cmd, _, now = renewal(db_conn)
    post(db_conn, cmd, now)
    duplicate = commands.enqueue(
        db_conn,
        uuid4(),
        acc["id"],
        kind="renewal",
        due_date=cmd["due_date"],
        request=cmd["request"],
        now=now,
    )
    with pytest.raises(ValueError, match="latest interval"):
        post(db_conn, duplicate, now)


def test_recovery_selects_price_and_exemption_at_today_not_due_date(db_conn):
    _, cmd, price, now = renewal(db_conn, delay=40)
    boundary = now - timedelta(days=1)
    db_conn.execute(
        "UPDATE product_offering_price SET valid_for = tstzrange(%s, %s, '[)') WHERE id = %s",
        (T0, boundary, price["id"]),
    )
    current = catalog.create_product_offering_price(
        db_conn,
        product_offering_id=price["product_offering_id"],
        price_type="recurring",
        amount=D(7),
        currency="USD",
        valid_for=Range(boundary, None, "[)"),
        recurring_period="monthly",
        details={"tax": [{"taxCategory": "VAT", "taxRate": 10}]},
    )
    result = post(db_conn, cmd, now)
    assert billing.get_bill(db_conn, result["bill_id"])["amount_due"] == D("7.70")
    assert db_conn.execute(
        "SELECT quote->>'price_id' FROM funded_interval WHERE command_id = %s", (cmd["id"],)
    ).fetchone() == (str(current["id"]),)


@pytest.mark.parametrize("exempt", [True, False])
def test_renewal_tax_exemption_uses_actual_start(db_conn, exempt):
    from app.repository._sql import jsonb

    acc, cmd, _, now = renewal(db_conn, delay=40)
    db_conn.execute(
        "UPDATE billing_account SET details = %s WHERE id = %s",
        (
            jsonb(
                {
                    "taxExemption": [
                        {
                            "validFor": {
                                "startDateTime": (now - timedelta(days=1)).isoformat(),
                                "endDateTime": (
                                    now + timedelta(days=1) if exempt else now - timedelta(hours=13)
                                ).isoformat(),
                            },
                            "taxDefinition": [{"taxType": "VAT"}],
                        }
                    ]
                }
            ),
            acc["id"],
        ),
    )
    result = post(db_conn, cmd, now)
    assert billing.get_bill(db_conn, result["bill_id"])["amount_due"] == (D(5) if exempt else D(6))


def test_month_end_renewal_restores_original_day(db_conn):
    acc, cmd, _, now = renewal(db_conn)
    # An initial start accepted on Jan 15 can be authorized on Jan 31.
    # Build a second product to exercise the normal Jan31 -> Feb28 -> Mar31 sequence.
    from tests.factories import product

    prod = product(
        db_conn,
        acc["id"],
        db_conn.execute("SELECT product_offering_id FROM product LIMIT 1").fetchone()[0],
        NOW.date(),
    )
    initial = commands.enqueue(
        db_conn,
        uuid4(),
        acc["id"],
        kind="order",
        due_date=NOW.date(),
        request=dict(
            db_conn.execute("SELECT request FROM prepaid_command WHERE kind = 'order'").fetchone()[
                0
            ],
            product_id=str(prod["id"]),
            amount="0",
            taxes=[],
        ),
        now=NOW,
    )
    funding.reserve_for_command(db_conn, acc["id"], D(0), command_id=initial["id"], held_at=NOW)
    first = post(db_conn, initial, NOW.replace(day=31))
    due = now.replace(day=28)
    cmd = commands.enqueue(
        db_conn,
        uuid4(),
        acc["id"],
        kind="renewal",
        due_date=due.date(),
        request={"previous_interval_id": first["interval_id"]},
        now=due,
    )
    post(db_conn, cmd, due)
    assert db_conn.execute(
        "SELECT lower(period), upper(period), anchor_date FROM funded_interval"
        " WHERE command_id = %s",
        (cmd["id"],),
    ).fetchone() == (date(2026, 2, 28), date(2026, 3, 31), date(2026, 1, 31))
