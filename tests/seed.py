"""Deterministic demo data for the LOCAL stack: `make seed` (python -m tests.seed).

Everything is created through the public TMF API - the same routes, validation and order
processing as production - never through SQL. The API refuses prices that start in the
past and orders with a past start date, so the API runs in-process (TestClient) with a
FixedClock that starts START_DAYS before today and moves forward day by day; each day the
billing run for that date runs first (00:30, the stage 3 adapter's bill_account for every
account, as the stage 4 worker will), then the day's orders and payments (10:00). Dates
are relative to today, so future-dated and pendingTerminate products really are in the
future. Same day -> same data.

Idempotent: if the seed's catalog is already there it does nothing; a partial seed (a
crash half-way) is reported, never duplicated or patched up.

Refuses to run with APP_ENV=prod or against any database but the local compose `db`.
Also used as the e2e fixture (stage 5 checkpoint 7): seed(client, conn, today).
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.billing.adapter import bill_account
from app.clock import FixedClock, SystemClock
from app.config import settings
from app.repository import billing
from tests import apidata
from tests.apidata import ACCOUNT, CATALOG, CUSTOMER

START_DAYS = 120
TZ = ZoneInfo(settings.billing_tz)
MARKER_CODE = "MOB-S"  # the first offering the seed creates


class SeedError(Exception):
    pass


def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def iso_noon(day: date) -> str:
    # a requestedStartDate: noon, so no offset question can move it to another date
    return at(day, 12).isoformat()


# --- the plan: catalog, accounts, events by day offset from the start ---


@dataclass(frozen=True)
class OfferingPlan:
    code: str
    name: str
    spec: str
    price: int  # whole roubles: sent as a JSON integer, never through a float
    status: str


CATALOG_PLAN = [
    OfferingPlan("MOB-S", "Mobile S", "Mobile Voice & Data", 290, "Active"),
    OfferingPlan("MOB-L", "Mobile L", "Mobile Voice & Data", 1290, "Active"),
    OfferingPlan("NET-100", "Fiber 100", "Home Internet", 650, "Active"),
    OfferingPlan("TV-BASE", "TV Basic", "TV", 399, "In Design"),
]


@dataclass(frozen=True)
class AccountPlan:
    key: str
    customer: str
    organization: bool
    cycle_day: int
    credit_limit: str | None


ACCOUNTS_PLAN = [
    AccountPlan("ivan", "Ivan Petrov", False, 1, "1000"),
    AccountPlan("anna", "Anna Smirnova", False, 5, None),
    AccountPlan("romashka", "Romashka LLC", True, 15, "5000"),
    AccountPlan("sergey", "Sergey Volkov", False, 10, None),  # Suspended later
    AccountPlan("olga", "Olga Kuznetsova", False, 28, "500"),  # never pays: negative
    AccountPlan("dmitry", "Dmitry Sokolov", False, 1, None),  # terminated, then Closed
    AccountPlan("elena", "Elena Morozova", False, 20, None),  # joins today
]


def account_name(plan: AccountPlan) -> str:
    return f"{plan.customer} main"


# --- talking to the API ---


class Api:
    """The in-process TMF API with the seed's clock; every call must succeed."""

    def __init__(self, client: Any, clock: FixedClock) -> None:
        self.client = client
        self.clock = clock

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(url, body)
        if response.status_code != 201:
            raise SeedError(f"POST {url} -> {response.status_code}: {response.text}")
        return response.json()

    def patch(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.client.patch(url, body)
        if response.status_code != 200:
            raise SeedError(f"PATCH {url} -> {response.status_code}: {response.text}")
        return response.json()

    def get(self, url: str) -> Any:
        response = self.client.get(url)
        if response.status_code != 200:
            raise SeedError(f"GET {url} -> {response.status_code}: {response.text}")
        return response.json()


@dataclass
class World:
    offerings: dict[str, dict[str, Any]]
    accounts: dict[str, dict[str, Any]]
    products: dict[str, dict[str, Any]]


def build_catalog(api: Api, first_day: date) -> dict[str, dict[str, Any]]:
    start = at(first_day, 0).isoformat()  # the next midnight: the earliest a price may start
    specs: dict[str, dict[str, Any]] = {}
    offerings = {}
    for plan in CATALOG_PLAN:
        if plan.spec not in specs:
            specs[plan.spec] = api.post(
                f"{CATALOG}/productSpecification",
                {"@type": "ProductSpecification", "name": plan.spec, "lifecycleStatus": "Active"},
            )
        price = api.post(
            f"{CATALOG}/productOfferingPrice",
            {
                "@type": "ProductOfferingPrice",
                "name": f"{plan.name} monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "RUB", "value": plan.price},
                "validFor": {"startDateTime": start},
            },
        )
        offerings[plan.code] = api.post(
            f"{CATALOG}/productOffering",
            {
                "@type": "ProductOffering",
                "name": plan.name,
                "lifecycleStatus": plan.status,
                "validFor": {"startDateTime": start},
                "productSpecification": {
                    "id": specs[plan.spec]["id"],
                    "@type": "ProductSpecificationRef",
                },
                "productOfferingPrice": [{"id": price["id"], "@type": "ProductOfferingPriceRef"}],
                "externalIdentifier": apidata.code_identifier(plan.code),
            },
        )
    return offerings


def build_accounts(api: Api) -> dict[str, dict[str, Any]]:
    accounts = {}
    for plan in ACCOUNTS_PLAN:
        if plan.organization:
            party = api.post(
                "/tmf-api/partyManagement/v5/organization",
                {"@type": "Organization", "name": plan.customer},
            )
        else:
            given, family = plan.customer.split(" ")
            party = api.post(
                "/tmf-api/partyManagement/v5/individual",
                {"@type": "Individual", "givenName": given, "familyName": family},
            )
        customer = api.post(
            CUSTOMER, apidata.customer_body(party, name=plan.customer, status="Active")
        )
        extra = (
            {}
            if plan.credit_limit is None
            else {"creditLimit": {"unit": "RUB", "value": int(plan.credit_limit)}}
        )
        accounts[plan.key] = api.post(
            ACCOUNT,
            apidata.account_body(
                customer, name=account_name(plan), billCycleDay=plan.cycle_day, **extra
            ),
        )
    return accounts


def order(api: Api, world: World, who: str, item: dict[str, Any], start: date | None) -> dict:
    body = apidata.order_body(world.accounts[who], item)
    if start is not None:
        body["requestedStartDate"] = iso_noon(start)
    placed = api.post(apidata.ORDER, body)
    if placed["state"] != "completed":
        raise SeedError(f"order for {who} was {placed['state']}: {placed}")
    return placed


def add(api: Api, world: World, key: str, who: str, code: str, start: date) -> None:
    placed = order(api, world, who, apidata.add_item(world.offerings[code]), start)
    world.products[key] = api.get(placed["productOrderItem"][0]["product"]["href"])


def delete(api: Api, world: World, key: str, who: str, end: date | None = None) -> None:
    order(api, world, who, apidata.delete_item(world.products[key]["id"]), end)


def pay(api: Api, world: World, who: str, value: int, method: str) -> None:
    body = apidata.payment_body(world.accounts[who], value)
    body["paymentMethod"] = {"name": method}  # the stored word; echoed as name and @type
    api.post(apidata.PAYMENT, body)


def events(today: date, start: date) -> dict[date, Callable[[Api, World], None]]:
    """What happens on each day, at 10:00. `start` is day 0 (catalog and accounts)."""

    def d(n: int) -> date:
        return start + timedelta(days=n)

    plan: dict[date, list[Callable[[Api, World], None]]] = {}

    def on(day: date, step: Callable[[Api, World], None]) -> None:
        plan.setdefault(day, []).append(step)

    on(d(2), lambda a, w: add(a, w, "ivan-mob", "ivan", "MOB-L", d(2)))
    on(d(2), lambda a, w: add(a, w, "anna-mob", "anna", "MOB-S", d(2)))
    on(d(2), lambda a, w: add(a, w, "rom-net-1", "romashka", "NET-100", d(2)))
    on(d(2), lambda a, w: add(a, w, "rom-net-2", "romashka", "NET-100", d(2)))
    on(d(3), lambda a, w: add(a, w, "sergey-mob", "sergey", "MOB-S", d(3)))
    on(d(5), lambda a, w: pay(a, w, "ivan", 1500, "Bank card"))
    on(d(5), lambda a, w: pay(a, w, "anna", 600, "Faster Payments"))
    on(d(5), lambda a, w: pay(a, w, "romashka", 2000, "Bank transfer"))
    on(d(10), lambda a, w: add(a, w, "olga-mob", "olga", "MOB-L", d(10)))
    on(d(10), lambda a, w: add(a, w, "dmitry-net", "dmitry", "NET-100", d(10)))
    on(d(20), lambda a, w: add(a, w, "ivan-net", "ivan", "NET-100", d(20)))
    on(d(35), lambda a, w: pay(a, w, "ivan", 2000, "Bank card"))
    on(d(35), lambda a, w: pay(a, w, "anna", 300, "Faster Payments"))
    on(d(35), lambda a, w: pay(a, w, "romashka", 1300, "Bank transfer"))
    on(d(35), lambda a, w: pay(a, w, "sergey", 290, "Bank card"))
    on(d(45), lambda a, w: delete(a, w, "dmitry-net", "dmitry"))  # terminated that day
    on(d(60), lambda a, w: a.patch(w.accounts["dmitry"]["href"], {"state": "Closed"}))
    on(d(65), lambda a, w: pay(a, w, "ivan", 1900, "Bank card"))
    on(d(65), lambda a, w: pay(a, w, "anna", 290, "Faster Payments"))
    on(d(65), lambda a, w: pay(a, w, "romashka", 1300, "Bank transfer"))
    on(d(70), lambda a, w: add(a, w, "anna-net", "anna", "NET-100", d(70)))
    on(d(80), lambda a, w: a.patch(w.accounts["sergey"]["href"], {"state": "Suspended"}))
    on(d(95), lambda a, w: pay(a, w, "ivan", 1940, "Bank card"))
    on(d(95), lambda a, w: pay(a, w, "anna", 940, "Faster Payments"))
    on(d(95), lambda a, w: pay(a, w, "romashka", 1300, "Bank transfer"))
    on(d(100), lambda a, w: delete(a, w, "ivan-mob", "ivan"))  # mid-period: a refund
    on(d(110), lambda a, w: pay(a, w, "romashka", 1300, "Bank transfer"))
    # today: a product starting in 5 days, one starting today (charged by tomorrow's
    # run), and a termination ordered for 12 days from now (pendingTerminate)
    on(today, lambda a, w: add(a, w, "elena-mob", "elena", "MOB-S", today + timedelta(days=5)))
    on(today, lambda a, w: add(a, w, "elena-net", "elena", "NET-100", today))
    on(today, lambda a, w: delete(a, w, "anna-mob", "anna", today + timedelta(days=12)))

    def run_all(steps: list[Callable[[Api, World], None]]) -> Callable[[Api, World], None]:
        def run(api: Api, world: World) -> None:
            for step in steps:
                step(api, world)

        return run

    return {day: run_all(steps) for day, steps in plan.items()}


# --- the seed ---


def already_seeded(api: Api) -> bool:
    found = api.get(f"{CATALOG}/productOffering?externalIdentifier.id={MARKER_CODE}")
    if not found:
        return False
    names = {account_name(plan) for plan in ACCOUNTS_PLAN}
    present = sum(1 for name in names if api.get(f"{ACCOUNT}?name={name.replace(' ', '%20')}"))
    if present != len(names):
        raise SeedError(
            f"a partial seed is in the database ({present} of {len(names)} accounts). Reset"
            " the local database (make clean && make up) and run make seed again."
        )
    return True


def run_billing(conn: psycopg.Connection, day: date, account_ids: list[UUID]) -> None:
    try:
        run = billing.start_run(conn, day, now=at(day, 0, 30))
    except billing.BillingRunConflict as exc:
        raise SeedError(
            f"a billing run for {day} already exists ({exc}); not seeding over it"
        ) from None
    for account_id in account_ids:
        bill_account(conn, account_id, run)
    billing.finish_run(conn, run["id"], now=at(day, 0, 45))
    conn.commit()


def seed(client: Any, conn: psycopg.Connection, clock: FixedClock, today: date) -> bool:
    """Seed through `client` (a TmfClient over the app whose clock is `clock`). Returns
    False if the data was already there."""
    api = Api(client, clock)
    start = today - timedelta(days=START_DAYS)
    clock.set(at(start, 9))
    if already_seeded(api):
        return False
    world = World(offerings=build_catalog(api, start + timedelta(days=1)), accounts={}, products={})
    world.accounts = build_accounts(api)
    ids = [UUID(account["id"]) for account in world.accounts.values()]
    plan = events(today, start)
    day = start + timedelta(days=1)
    while day <= today:
        clock.set(at(day, 0, 30))
        run_billing(conn, day, ids)
        if day in plan:
            clock.set(at(day, 10))
            plan[day](api, world)
        day += timedelta(days=1)
    return True


def refuse_unless_local() -> None:
    if settings.app_env.lower() in ("prod", "production"):
        raise SeedError(f"refusing to seed: APP_ENV is {settings.app_env}")
    host = urlparse(settings.database_url).hostname
    if host != "db":
        raise SeedError(f"refusing to seed: DATABASE_URL points at {host!r}, not the local `db`")


def main() -> int:
    from fastapi.testclient import TestClient

    from app.api.deps import get_clock
    from app.main import app
    from tests.apiclient import TmfClient

    # app.main configured logging at LOG_LEVEL (debug locally) on import. The seed is a
    # command: INFO, and no per-request lines from the in-process client (one per call,
    # over 120 days) or asyncio's event-loop chatter.
    logging.getLogger().setLevel(logging.INFO)
    for name in ("asyncio", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)

    try:
        refuse_unless_local()
        today = SystemClock().today()
        clock = FixedClock(at(today, 9))
        app.dependency_overrides[get_clock] = lambda: clock
        client = TmfClient(TestClient(app, raise_server_exceptions=True))
        with psycopg.connect(settings.database_url) as conn:
            created = seed(client, conn, clock, today)
    except SeedError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    if created:
        print(
            f"seed: created {len(CATALOG_PLAN)} offerings, {len(ACCOUNTS_PLAN)} accounts and"
            f" {START_DAYS} days of billing runs up to {today}"
        )
    else:
        print("seed: the demo data is already there - nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
