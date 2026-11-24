"""Builders other test modules share, from tests/billing/test_billing_db.py (#423)."""

from datetime import date, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import bill_account
from app.billing.model import BillingResult
from app.repository import billing
from tests import apidata
from tests.timeline import iso, midnight


def at(day: date, hour: int = 10) -> datetime:
    return midnight(day) + timedelta(hours=hour)


def run_row(conn: psycopg.Connection, day: date) -> dict:
    """The billing run of `day`, started at 00:30 that day if it does not exist yet. Runs
    are left `running`: finishing them is stage 4's job."""
    run = billing.get_run(conn, day)
    if run is None:
        run = billing.insert_run(
            conn, day, now=midnight(day) + timedelta(minutes=30), trigger="scheduled"
        )
    return run


def bill(conn: psycopg.Connection, account: dict, day: date) -> BillingResult:
    result = bill_account(conn, UUID(account["id"]), run_row(conn, day))
    conn.commit()
    return result


def add(api, account: dict, offering: dict, start: date, *, on: datetime) -> dict:
    api.clock.set(on)
    placed = apidata.order(
        api, account, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def delete(api, account: dict, product: dict, effective: date | None, *, on: datetime) -> None:
    api.clock.set(on)
    over = {} if effective is None else {"requestedStartDate": iso(midnight(effective))}
    placed = apidata.order(api, account, apidata.delete_item(product["id"]), **over)
    assert placed["state"] == "completed", placed


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300)
