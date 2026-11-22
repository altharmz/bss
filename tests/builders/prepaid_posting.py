"""Shared builders of tests/test_prepaid_posting.py, used by other test modules (#423)."""

from decimal import Decimal as D
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.billing import prepaid
from app.repository import funding
from app.repository import prepaid_commands as commands
from tests.builders.settlement_db import make_payment
from tests.factories import NOW, account, offering, product

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
