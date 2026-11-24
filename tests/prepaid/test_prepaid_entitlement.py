"""Coverage boundaries, independent causes and read/worker separation."""

from datetime import timedelta

import psycopg
import pytest
from alembic.config import Config

from alembic import command
from app.billing.entitlement import effective_state, view
from app.clock import FixedClock
from app.repository import parties, products
from app.worker import db as worker_db
from app.worker.prepaid import observe_entitlements
from tests.builders.prepaid.prepaid_posting import TZ, post, prepared
from tests.builders.prepaid.prepaid_renewal import renewal
from tests.builders.tmf.events import stored, subscribe
from tests.factories import NOW


@pytest.mark.migration_history
def test_manual_cause_migration_preserves_suspended_history(temp_db_url):
    from tests.factories import account, offering, product

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0042")
    with psycopg.connect(temp_db_url) as conn:
        acc = account(conn)
        conn.execute("UPDATE billing_account SET rating_type = 'prepaid'")
        prod = product(conn, acc["id"], offering(conn)["id"], NOW.date())
        conn.execute("UPDATE product SET status = 'suspended'")
    command.upgrade(cfg, "head")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT manually_suspended FROM product").fetchall() == [(True,)]
    with pytest.raises(Exception, match="manual suspension causes must be retained"):
        command.downgrade(cfg, "0042")
    with psycopg.connect(temp_db_url) as conn:
        conn.execute("UPDATE product SET manually_suspended = false WHERE id = %s", (prod["id"],))
    command.downgrade(cfg, "0042")
    command.upgrade(cfg, "head")


@pytest.mark.parametrize(
    "manual,account,status,cause",
    [
        (False, "Active", "active", None),
        (True, "Active", "suspended", "manual"),
        (False, "Suspended", "suspended", "account"),
        (True, "Suspended", "suspended", "manual"),
    ],
)
def test_independent_causes(db_conn, manual, account, status, cause):
    acc, prod, cmd, _ = prepared(db_conn)
    assert worker_db.acquire_ownership(db_conn)
    post(db_conn, cmd)
    prod["manually_suspended"] = manual
    acc["state"] = account
    result = view(db_conn, prod, acc, NOW.date())
    assert (result["status"], result["suspensionCause"]) == (status, cause)
    prod["termination_date"] = NOW.date()
    assert view(db_conn, prod, acc, NOW.date())["status"] == "terminated"


def test_expiry_read_is_pure_worker_persists_once(db_conn, api):
    acc, prod, cmd, _ = prepared(db_conn)
    acc = parties.get_billing_account(db_conn, acc["id"])
    assert view(db_conn, prod, acc, NOW.date())["status"] == "pendingActive"
    assert worker_db.acquire_ownership(db_conn)
    post(db_conn, cmd)
    db_conn.commit()
    paid = view(db_conn, prod, acc, NOW.date())
    assert paid["authorizedAt"] == NOW
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    expiry = NOW + timedelta(days=31)
    expired = view(db_conn, prod, acc, expiry.date())
    assert expired["suspensionCause"] == "funding"
    assert products.get_product(db_conn, prod["id"])["status"] == "active"
    api.clock.set(expiry)
    response = api.get(f"/internal/v1/billingAccount/{acc['id']}/products")
    assert response.status_code == 200, response.text
    assert response.json()[0]["suspensionCause"] == "funding"
    assert stored(db_conn, "TMF637") == []
    db_conn.commit()
    for _ in range(2):
        observe_entitlements(db_conn, clock=FixedClock(expiry), tz=TZ)
    assert products.get_product(db_conn, prod["id"])["status"] == "suspended"
    events = stored(db_conn, "TMF637")
    assert len(events) == 1
    assert events[0][1]["event"]["product"]["status"] == "suspended"
    grant = {"period": type("Period", (), {"lower": NOW.date(), "upper": paid["paidThrough"]})()}
    assert effective_state(prod, acc, grant, paid["paidThrough"])[1] == "funding"


def test_observed_funding_suspension_does_not_block_funded_renewal(db_conn):
    _, cmd, _, now = renewal(db_conn)
    db_conn.commit()
    observe_entitlements(db_conn, clock=FixedClock(now), tz=TZ)
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("suspended",)
    db_conn.execute("UPDATE product SET manually_suspended = true")
    with pytest.raises(ValueError, match="lifecycle"):
        post(db_conn, cmd, now)
    db_conn.execute("UPDATE product SET manually_suspended = false")
    post(db_conn, cmd, now)
    observe_entitlements(db_conn, clock=FixedClock(now), tz=TZ)
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("active",)
