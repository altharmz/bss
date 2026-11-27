"""The prepaid lifecycle pass's stop, race and failure paths (app/worker/lifecycle.py, #511),
against the real test database: a checkpoint and the heartbeat, an account suspended after the
pass listed it, and an internal order that raises."""
# ruff: noqa: F811

import logging
from datetime import timedelta
from uuid import UUID

import pytest

from app.billing.lifecycle import Settings
from app.clock import FixedClock
from app.repository import lifecycle as lifecycle_repo
from app.worker import lifecycle
from app.worker.lifecycle import process_lifecycle
from app.worker.prepaid import process_commands
from tests.builders.prepaid.prepaid_lifecycle import at, go, numbered  # noqa: F401 - fixtures
from tests.builders.prepaid.prepaid_renewal_producer import pay
from tests.builders.resources.network_provisioning import stub  # noqa: F401 - fixtures
from tests.builders.worker.logs import worker_lines
from tests.timeline import TZ

pytestmark = pytest.mark.capability("worker.prepaidLifecycle")


def lifecycle_pass(api, db_conn, day, **kwargs):
    api.clock.set(at(day))
    acted = process_lifecycle(db_conn, clock=FixedClock(at(day)), tz=TZ, **kwargs)
    db_conn.commit()
    return acted


def row(db_conn, product_id):
    db_conn.commit()
    return db_conn.execute(
        "SELECT status, manually_suspended, lifecycle_suspended FROM product WHERE id = %s",
        (product_id,),
    ).fetchone()


def events(db_conn):
    rows = db_conn.execute(
        "SELECT action, business_date, outcome, reason FROM prepaid_lifecycle_event"
        " ORDER BY created_at"
    ).fetchall()
    db_conn.commit()
    return rows


def test_a_checkpoint_stops_before_the_next_account_and_each_account_gets_a_heartbeat(
    api, db_conn, numbered
):
    day = numbered.due + timedelta(days=3)
    assert lifecycle_pass(api, db_conn, day, checkpoint=lambda: True) == []
    assert events(db_conn) == [] and row(db_conn, numbered.product)[2] is False
    beats = []
    acted = lifecycle_pass(
        api, db_conn, day, checkpoint=lambda: False, heartbeat=lambda: beats.append(1)
    )
    assert acted == [numbered.product] and beats == [1]
    assert events(db_conn) == [("suspend", day, "placed", None)]


def test_a_funded_line_is_not_resumed_on_an_account_suspended_after_the_listing(
    api, db_conn, numbered, monkeypatch
):
    acc = numbered.acc
    go(api, db_conn, numbered.due + timedelta(days=3))
    assert row(db_conn, numbered.product)[:3] == ("suspended", True, True)
    day = numbered.due + timedelta(days=4)
    pay(db_conn, UUID(acc["id"]), "12", at(day))
    process_commands(db_conn, clock=FixedClock(at(day)), tz=TZ)  # the renewal: funded again
    db_conn.commit()
    settings = lifecycle_repo.settings(db_conn)
    assert isinstance(settings, Settings)
    listed = lifecycle._due_accounts(db_conn, settings, day)
    db_conn.commit()
    assert [str(r["id"]) for r in listed] == [acc["id"]]
    # the operator suspends the account between the listing and the account lock
    assert api.patch(acc["href"], {"state": "Suspended"}).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "_due_accounts", lambda conn, settings, today: listed)
        assert lifecycle_pass(api, db_conn, day) == []
    assert row(db_conn, numbered.product)[:3] == ("suspended", True, True)
    assert [e[0] for e in events(db_conn)] == ["suspend"]
    # active again: the next pass resumes the line
    assert api.patch(acc["href"], {"state": "Active"}).status_code == 200
    assert lifecycle_pass(api, db_conn, day) == [numbered.product]
    assert [e[:3] for e in events(db_conn)][-1] == ("resume", day, "placed")


def test_a_failing_order_is_recorded_and_tried_again_the_next_business_date(
    api, db_conn, numbered, monkeypatch, caplog
):
    day = numbered.due + timedelta(days=3)

    def down(conn, request, *, now, today):
        raise RuntimeError("ordering is down")

    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "place_order", down)
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            assert lifecycle_pass(api, db_conn, day) == []
            assert lifecycle_pass(api, db_conn, day) == []  # one attempt per business date
    assert worker_lines(caplog) == [f"lifecycle suspend of product {numbered.product} failed"]
    assert events(db_conn) == [("suspend", day, "rejected", "ordering is down")]
    assert row(db_conn, numbered.product)[2] is False
    next_day = day + timedelta(days=1)
    assert lifecycle_pass(api, db_conn, next_day) == [numbered.product]
    assert events(db_conn)[-1] == ("suspend", next_day, "placed", None)
