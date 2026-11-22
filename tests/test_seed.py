"""`make seed` (tests/seed.py) against a throwaway database, on a fixed "today": it builds
what the demo needs, a second run changes nothing, and it refuses what it must refuse."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from tests import seed as seeding
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, CATALOG, PRODUCT, RATE
from tests.timeline import z

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


def all_of(api, url: str) -> list[dict]:
    """Every item of a TMF list, page by page."""
    items: list[dict] = []
    while True:
        page = api.get(f"{url}?offset={len(items)}&limit=100")
        items += page.json()
        if 'rel="next"' not in page.headers.get("Link", ""):
            return items


@pytest.mark.slow
def test_seed_builds_the_demo_and_is_idempotent(api, db_conn):
    assert seeding.seed(api, api.clock, TODAY) is True
    before = counts(db_conn)
    assert before["product_offering"] == 4
    assert before["billing_account"] == 9
    # one daily run per day up to today, plus the prepaid commands' own runs
    assert db_conn.execute("SELECT count(*) FROM billing_run WHERE kind = 'daily'").fetchone() == (
        seeding.START_DAYS,
    )
    assert before["billing_run"] > seeding.START_DAYS
    # every run went through the worker: completed, one ok outcome per member account
    # (all 9 accounts are opened on the seed's first day, before the first run)
    statuses = db_conn.execute("SELECT DISTINCT status FROM billing_run").fetchall()
    assert statuses == [("completed",)]
    outcomes = db_conn.execute(
        "SELECT status, count(*) FROM billing_run_account GROUP BY status"
    ).fetchall()
    assert outcomes == [("ok", seeding.START_DAYS * 9)]
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
    ivan = next(a for a in accounts if a["name"] == "James Wilson main")
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

    # the bill lifecycle: bills in every payment state, one on hold, a credit note, and
    # both automatic and explicit allocations
    bill_states = {
        r[0] for r in db_conn.execute("SELECT DISTINCT state FROM customer_bill").fetchall()
    }
    assert {"sent", "partiallyPaid", "settled", "onHold"} <= bill_states, bill_states
    assert db_conn.execute(
        "SELECT count(*) FROM customer_bill WHERE state IN ('new', 'validated')"
    ).fetchone() == (0,)
    kinds = {r[0] for r in db_conn.execute("SELECT DISTINCT kind FROM bill_allocation").fetchall()}
    assert kinds == {"automatic", "explicit"}

    # the history is dated by the seed's FixedClock, not by when the seed ran: every ABR
    # was written by the run of its date at 00:30, every product by an order at 10:00 on
    # one of the simulated days (tests/seed.py, `seed`)
    start = TODAY - timedelta(days=seeding.START_DAYS)
    runs = db_conn.execute(
        "SELECT a.applied_at, r.run_date, r.kind FROM applied_customer_billing_rate a"
        " JOIN billing_run r ON r.id = a.billing_run_id"
    ).fetchall()
    # a daily run is written at 00:30; a prepaid command (a purchase, a renewal) by the
    # worker pass at 11:00 of the day it posted
    assert runs and all(
        applied == (seeding.at(day, 0, 30) if kind == "daily" else seeding.at(day, 11))
        for applied, day, kind in runs
    )
    rates = all_of(api, RATE)
    assert len(rates) == len(runs)
    assert {r["date"] for r in rates} == {z(applied) for applied, _, _ in runs}
    products = all_of(api, PRODUCT)
    created = {datetime.fromisoformat(p["creationDate"]) for p in products}
    days = {c.astimezone(seeding.TZ).date() for c in created}
    assert all(c.astimezone(seeding.TZ).hour == 10 for c in created)
    assert min(days) > start and max(days) <= TODAY

    # the prepaid account: topped up, bought a product, renewed from funds
    pavel = next(a for a in accounts if a["name"] == "Robert Taylor main")
    assert pavel["ratingType"] == "prepaid"
    assert db_conn.execute(
        "SELECT count(*) FROM product WHERE billing_account_id = %s AND status = 'active'",
        (pavel["id"],),
    ).fetchone() == (1,)
    intervals = db_conn.execute(
        "SELECT count(*) FROM funded_interval WHERE billing_account_id = %s", (pavel["id"],)
    ).fetchone()[0]
    assert intervals >= 3, intervals  # the purchase and renewals, one after running out
    # Linda ran out: her product is suspended and the renewal waits for a top-up
    vera = next(a for a in accounts if a["name"] == "Linda Harris main")
    assert db_conn.execute(
        "SELECT status FROM product WHERE billing_account_id = %s", (vera["id"],)
    ).fetchall() == [("suspended",)]
    assert db_conn.execute(
        "SELECT count(*) FROM prepaid_command WHERE state = 'pending' AND billing_account_id = %s",
        (vera["id"],),
    ).fetchone() == (1,)

    # US names; an organization's account is "<name> corporate"
    assert "Acme Corporation corporate" in {a["name"] for a in accounts}

    # a numbered mobile specification, 20 numbers, and every mobile product carries one
    specs = {s["name"]: s for s in api.get(f"{CATALOG}/productSpecification").json()}
    numbered = specs[seeding.MOBILE_SPEC]
    assert [r["id"] for r in numbered["resourceSpecification"]] == ["msisdn"]
    numbers = all_of(api, "/tmf-api/resourceInventory/v5/resource")
    assert sorted(n["value"] for n in numbers) == [str(12025550100 + i) for i in range(20)]
    mobile = {
        o["id"]
        for o in api.get(f"{CATALOG}/productOffering").json()
        if o["name"].startswith("Mobile")
    }
    mobile_products = [  # a terminated product has released its number
        p for p in products if p["productOffering"]["id"] in mobile and p["status"] != "terminated"
    ]
    assert mobile_products and all(len(p["realizingResource"]) == 1 for p in mobile_products)

    # categories Mobile / Home Internet / TV hold every offering; catalogs Consumer / Business
    categories = {c["name"]: c for c in api.get(f"{CATALOG}/category").json()}
    assert sorted(categories) == ["Home Internet", "Mobile", "TV"]
    listed = {o["id"] for c in categories.values() for o in c["productOffering"]}
    assert listed == {o["id"] for o in api.get(f"{CATALOG}/productOffering").json()}
    catalogs = {c["name"]: c for c in api.get(f"{CATALOG}/productCatalog").json()}
    assert sorted(catalogs) == ["Business catalog", "Consumer catalog"]

    # a second run is a no-op
    assert seeding.seed(api, api.clock, TODAY) is False
    assert counts(db_conn) == before


def test_seed_runs_with_the_production_gates_on(api, db_conn, monkeypatch):
    """`make seed` runs on a default local stack: the service-address gate and the account
    PIN gate are on there, while the suite's autouse fixtures turn them off (#321)."""
    monkeypatch.setattr(settings, "tax_require_service_address", True)
    monkeypatch.setattr(settings, "account_pin_required", True)
    assert seeding.seed(api, api.clock, TODAY) is True
    prepaid = db_conn.execute(
        "SELECT a.name, p.status FROM product p"
        " JOIN billing_account a ON a.id = p.billing_account_id"
        " WHERE a.rating_type = 'prepaid' ORDER BY a.name"
    ).fetchall()
    assert prepaid == [("Linda Harris main", "suspended"), ("Robert Taylor main", "active")]


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
