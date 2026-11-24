"""Builders other test modules share, from tests/worker/test_worker_process.py (#423)."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import UUID

import psycopg

from app.clock import FixedClock
from app.repository import billing
from app.worker.process import bill_date
from tests import apidata, timeline
from tests.timeline import TZ, iso, midnight

NOV20, DEC1, DEC2, DEC3 = (
    date(2026, 11, 20),
    date(2026, 12, 1),
    date(2026, 12, 2),
    date(2026, 12, 3),
)


def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return midnight(day) + timedelta(hours=hour, minutes=minute)


def add(api, acc, offering, start, *, on):
    api.clock.set(on)
    placed = apidata.order(
        api, acc, apidata.add_item(offering), requestedStartDate=iso(midnight(start))
    )
    assert placed["state"] == "completed", placed
    return apidata.product_of(api, placed)


def terminate(api, acc, product, effective, *, on):
    api.clock.set(on)
    placed = apidata.order(
        api, acc, apidata.delete_item(product["id"]), requestedStartDate=iso(midnight(effective))
    )
    assert placed["state"] == "completed", placed


def set_currency(conn, offering_id, currency):
    conn.execute(
        "UPDATE product_offering_price SET currency = %s WHERE product_offering_id = %s",
        (currency, offering_id),
    )


def run(conn, clock: FixedClock, day: date):
    clock.set(at(day, 0, 30))
    return bill_date(conn, day, clock=clock, tz=TZ, trigger="scheduled")


FirstRun = Callable[[psycopg.Connection, FixedClock, dict], psycopg.Connection]


def plain_first_run(conn, clock, ids):
    run(conn, clock, DEC1)
    return conn


def scenario(api, conn, *, broken: bool, first_run: FirstRun = plain_first_run) -> dict:
    """Accounts a and b on a healthy offering, x on a fragile one, all from Dec 1; x's
    product is terminated as of Dec 3 (ordered Dec 2). Runs Dec 1, 2, 3. `first_run`
    performs Dec 1 and returns the connection that carries on (a takeover replaces it)."""
    api.clock.set(timeline.NOW)  # prices can only start after "now": create them first
    healthy = apidata.sellable_offering(api, value=300)
    fragile = apidata.sellable_offering(api, value=500)
    api.clock.set(at(NOV20, 9))
    a, b, x = apidata.account(api), apidata.account(api), apidata.account(api)
    add(api, a, healthy, DEC1, on=at(NOV20))
    add(api, b, healthy, DEC1, on=at(NOV20))
    product = add(api, x, fragile, DEC1, on=at(NOV20))
    ids = {name: UUID(acc["id"]) for name, acc in (("a", a), ("b", b), ("x", x))}
    if broken:
        set_currency(conn, fragile["id"], "EUR")
    conn = first_run(conn, api.clock, ids)
    run(conn, api.clock, DEC2)
    terminate(api, x, product, DEC3, on=at(DEC2))
    run(conn, api.clock, DEC3)
    runs = {day: billing.get_run(conn, day) for day in (DEC1, DEC2, DEC3)}
    return {"ids": ids, "runs": runs, "fragile": fragile, "conn": conn}


def outcome(conn, day, account_id):
    return billing.outcomes(conn, billing.get_run(conn, day)["id"]).get(account_id)
