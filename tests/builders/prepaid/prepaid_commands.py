"""Builders other test modules share, from tests/prepaid/test_prepaid_commands.py (#423)."""

from decimal import Decimal as D
from uuid import uuid4

from app.repository import prepaid_commands as commands
from tests.factories import NOW


def enqueue(conn, acc, key=None, **changes):
    values = dict(kind="order", due_date=NOW.date(), request={"amount": D("12.34")}, now=NOW)
    values.update(changes)
    return commands.enqueue(conn, key or uuid4(), acc["id"], **values)
