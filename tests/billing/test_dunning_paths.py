"""The dunning pass's stop, race and failure paths (app/worker/dunning.py, #511), against the
real test database: a checkpoint and the heartbeat, an account suspended after the pass listed
it, a stale read of the steps taken, a product in flight at restoration, and an internal order
that is rejected or raises."""
# ruff: noqa: F811

import logging
from dataclasses import replace
from datetime import timedelta

from app.ordering import process as ordering
from app.worker import dunning
from app.worker.dunning import process_dunning
from tests.builders.billing.billing_completion import worker  # noqa: F401 - fixtures
from tests.builders.billing.billing_db import at
from tests.builders.billing.dunning import OVERDUE, billed, day, pay, product_row, steps
from tests.builders.orders.api_suspension import change
from tests.builders.worker.logs import worker_lines
from tests.timeline import TZ


def test_a_checkpoint_stops_before_the_next_account_and_each_account_gets_a_heartbeat(
    api, worker, db_conn
):
    (first, _), (second, _) = billed(api, worker, db_conn, 310, 40)
    api.clock.set(at(OVERDUE + timedelta(days=1)))
    assert process_dunning(worker, clock=api.clock, tz=TZ, checkpoint=lambda: True) == []
    assert steps(db_conn, first) == [] and steps(db_conn, second) == []
    beats = []
    taken = process_dunning(
        worker, clock=api.clock, tz=TZ, checkpoint=lambda: False, heartbeat=lambda: beats.append(1)
    )
    assert sorted(str(a) for a, _ in taken) == sorted([first["id"], second["id"]])
    assert len(beats) == 2


def test_an_account_suspended_after_the_pass_listed_it_takes_no_step(
    api, worker, db_conn, monkeypatch
):
    account, product = billed(api, worker, db_conn)
    when = OVERDUE + timedelta(days=14)
    listed = dunning.repo.candidates(worker, when, full=True)
    worker.commit()
    assert [str(a) for a in listed] == [account["id"]]
    # the operator suspends the account between the listing and the account lock
    assert api.patch(account["href"], {"state": "Suspended"}).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(dunning.repo, "candidates", lambda conn, today, full: listed)
        assert day(api, worker, when) == []
    assert steps(db_conn, account) == [] and product_row(db_conn, product) == ("active", False)
    view = api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()
    assert view["stepsTaken"] == []


def test_a_step_recorded_already_is_not_taken_twice_on_a_stale_read(
    api, worker, db_conn, monkeypatch
):
    account, _ = billed(api, worker, db_conn)
    when = OVERDUE + timedelta(days=1)
    assert day(api, worker, when) == [(account["id"], "reminder1")]
    # a read that missed the step: the insert's guard still keeps it to one
    with monkeypatch.context() as patch:
        patch.setattr(dunning.repo, "steps_of", lambda conn, account_id, o: [])
        assert day(api, worker, when) == []
    assert [s[0] for s in steps(db_conn, account)] == ["reminder1"]
    view = api.get(f"/internal/v1/billingAccount/{account['id']}/dunning").json()
    assert [s["step"] for s in view["stepsTaken"]] == ["reminder1"]


def test_a_product_with_a_status_change_in_flight_waits_at_restoration(api, worker, db_conn):
    account, product = billed(api, worker, db_conn)
    suspended_on = OVERDUE + timedelta(days=14)
    assert day(api, worker, suspended_on) == [(account["id"], "suspend")]
    # the operator schedules the line's resumption a week ahead: an order in flight
    later = suspended_on + timedelta(days=7)
    placed = change(api, account, product, "active", later, on=at(suspended_on + timedelta(days=1)))
    assert placed["state"] in ("acknowledged", "inProgress", "completed"), placed
    pay(api, account, 310)
    assert day(api, worker, suspended_on + timedelta(days=1), full=False) == []
    assert product_row(db_conn, product) == ("suspended", True)
    assert [s[0] for s in steps(db_conn, account)] == ["suspend"]


def test_a_rejected_suspension_order_suspends_nothing_and_says_why(
    api, worker, db_conn, monkeypatch, caplog
):
    account, product = billed(api, worker, db_conn)
    real = ordering.place_order

    def dated_yesterday(conn, request, *, now, today):
        return real(
            conn, replace(request, requested_start=today - timedelta(days=1)), now=now, today=today
        )

    with monkeypatch.context() as patch:
        patch.setattr(dunning, "place_order", dated_yesterday)
        with caplog.at_level(logging.WARNING, logger="app.worker"):
            assert day(api, worker, OVERDUE + timedelta(days=14)) == [(account["id"], "suspend")]
    assert product_row(db_conn, product) == ("active", False)
    assert [(s[0], s[3]) for s in steps(db_conn, account)] == [("suspend", 0)]
    [warning] = [line for line in worker_lines(caplog, logging.WARNING) if "rejected" in line]
    assert warning.startswith(f"dunning suspended of product {product['id']} rejected: ")
    assert "before today" in warning
    # the rejected order is rolled back with the attempt: no modify item was kept
    kept = db_conn.execute(
        "SELECT count(*) FROM product_order_item WHERE product_id = %s AND action = 'modify'",
        (product["id"],),
    ).fetchone()
    db_conn.commit()
    assert kept == (0,)


def test_a_failing_restoration_order_is_tried_again_on_the_next_pass(
    api, worker, db_conn, monkeypatch, caplog
):
    account, product = billed(api, worker, db_conn)
    suspended_on = OVERDUE + timedelta(days=14)
    assert day(api, worker, suspended_on) == [(account["id"], "suspend")]
    api.clock.set(at(suspended_on + timedelta(days=1), 8))
    pay(api, account, 310)

    def down(conn, request, *, now, today):
        raise RuntimeError("ordering is down")

    with monkeypatch.context() as patch:
        patch.setattr(dunning, "place_order", down)
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            assert day(api, worker, suspended_on + timedelta(days=1), full=False) == []
    assert worker_lines(caplog) == [f"dunning active of product {product['id']} failed"]
    assert product_row(db_conn, product) == ("suspended", True)
    # the next pass places the order
    assert day(api, worker, suspended_on + timedelta(days=1), full=False) == [
        (account["id"], "restore")
    ]
    assert product_row(db_conn, product) == ("active", False)
