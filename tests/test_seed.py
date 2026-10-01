"""`make seed` (tests/seed.py) against a throwaway database, on a fixed "today": it builds
what the demo needs, a second run changes nothing, and it refuses what it must refuse."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from tests import seed as seeding
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, CATALOG, PRODUCT

TODAY = date(2026, 10, 1)


def counts(db_conn) -> dict[str, int]:
    tables = (
        "product_offering",
        "billing_account",
        "product",
        "payment",
        "billing_run",
        "applied_customer_billing_rate",
        "customer_bill",
    )
    return {t: db_conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608


def test_seed_builds_the_demo_and_is_idempotent(api, db_conn):
    assert seeding.seed(api, db_conn, api.clock, TODAY) is True
    before = counts(db_conn)
    assert before["product_offering"] == 4
    assert before["billing_account"] == 7
    assert before["billing_run"] == seeding.START_DAYS  # one per day up to today
    assert before["applied_customer_billing_rate"] > 0 and before["customer_bill"] > 0
    assert before["payment"] > 0

    accounts = decimals(api.get(f"{ACCOUNT}?limit=100"))
    states = sorted(a["state"] for a in accounts)
    assert states.count("Suspended") == 1 and states.count("Closed") == 1
    balances = [a["accountBalance"][0]["amount"]["value"] for a in accounts]
    assert any(b < 0 for b in balances) and any(b > 0 for b in balances)
    assert all(b == b.quantize(Decimal("0.01")) for b in balances)

    statuses = {p["status"] for p in api.get(f"{PRODUCT}?limit=100").json()}
    assert {"active", "created", "pendingTerminate", "terminated"} <= statuses
    lifecycle = {o["lifecycleStatus"] for o in api.get(f"{CATALOG}/productOffering").json()}
    assert lifecycle == {"Active", "In Design"}

    # the history panels of an account with charges, a refund and payments are not empty
    ivan = next(a for a in accounts if a["name"] == "Ivan Petrov main")
    kinds = {
        c["kind"]
        for c in api.get(f"/internal/v1/billingAccount/{ivan['id']}/charges?limit=100").json()
    }
    assert kinds == {"charge", "refund"}
    types = {
        t["type"]
        for t in api.get(f"/internal/v1/billingAccount/{ivan['id']}/transactions?limit=100").json()
    }
    assert types == {"charge", "refund", "payment"}

    # a second run is a no-op
    assert seeding.seed(api, db_conn, api.clock, TODAY) is False
    assert counts(db_conn) == before


def test_a_partial_seed_is_reported_not_patched(api):
    api.clock.set(seeding.at(TODAY, 9))
    seeding.build_catalog(seeding.Api(api, api.clock), TODAY + timedelta(days=1))
    with pytest.raises(seeding.SeedError, match="partial seed"):
        seeding.already_seeded(seeding.Api(api, api.clock))


@pytest.mark.parametrize("env", ["prod", "PROD", "production"])
def test_refuses_prod(monkeypatch, env):
    monkeypatch.setattr(settings, "app_env", env)
    with pytest.raises(seeding.SeedError, match="APP_ENV"):
        seeding.refuse_unless_local()


def test_refuses_a_database_that_is_not_the_local_one(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://bss:x@postgres:5432/bss")
    with pytest.raises(seeding.SeedError, match="not the local"):
        seeding.refuse_unless_local()
