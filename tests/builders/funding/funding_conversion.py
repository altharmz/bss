"""Builders other test modules share, from tests/funding/test_funding_conversion.py (#423)."""

from datetime import date
from decimal import Decimal as D
from uuid import uuid4

from app.repository import funding
from tests.builders.billing.settlement_db import make_payment
from tests.builders.funding.funding_db import billed_charge
from tests.factories import NOW, world


def prepared(conn, amount="15"):
    w = world(conn)
    make_payment(conn, w.account, "20")
    key = uuid4()
    hold = funding.reserve_for_command(
        conn, w.account["id"], D(amount), command_id=key, held_at=NOW
    )
    bill = billed_charge(conn, w, date(2026, 1, 1), amount)
    return w, key, hold, bill


def convert(conn, w, key, bill, at=NOW):
    return funding.convert_for_command(
        conn, w.account["id"], bill["id"], command_id=key, converted_at=at
    )
