"""Deterministic demo data for the LOCAL stack: `make seed` (python -m tests.seed).

Everything is created through the public TMF API - the same routes, validation and order
processing as production - never through SQL. The API refuses prices that start in the
past and orders with a past start date, so the API runs in-process (TestClient) with a
FixedClock that starts START_DAYS before today and moves forward day by day; each day the
billing run for that date runs first (00:30, through the stage 4 worker's bill_date on a
worker connection, so every run has its per-account outcomes), then the day's orders and
payments (10:00). Dates
are relative to today, so future-dated and pendingTerminate products really are in the
future. Same day -> same data.

Idempotent: if the seed's catalog is already there it does nothing; a partial seed (a
crash half-way) is reported, never duplicated or patched up.

Refuses to run with APP_ENV=prod or against any database but the local compose `db`.
Also used as the e2e fixture (stage 5 checkpoint 7): seed(client, clock, today).
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg

from app.clock import FixedClock, SystemClock
from app.config import settings
from app.repository import billing
from app.worker import db as worker_db
from app.worker.prepaid import observe_entitlements, process_commands, produce_renewals
from app.worker.process import bill_date
from tests import apidata
from tests.apidata import ACCOUNT, CATALOG, CUSTOMER

START_DAYS = 120
TZ = ZoneInfo(settings.billing_tz)
MARKER_CODE = "MOB-S"  # the first offering the seed creates
MOBILE_SPEC = "Mobile Voice & Data (numbered)"  # requires a subscriber number
NUMBERS = "/tmf-api/resourceInventory/v5/resource"
FIRST_NUMBER, NUMBER_COUNT = 12025550100, 20


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
    price: int  # whole dollars: sent as a JSON integer, never through a float
    status: str


CATALOG_PLAN = [
    OfferingPlan("MOB-S", "Mobile S", MOBILE_SPEC, 29, "Active"),
    OfferingPlan("MOB-L", "Mobile L", MOBILE_SPEC, 129, "Active"),
    OfferingPlan("NET-100", "Fiber 100", "Home Internet", 65, "Active"),
    OfferingPlan("TV-BASE", "TV Basic", "TV", 40, "In Design"),
]


@dataclass(frozen=True)
class AccountPlan:
    key: str
    customer: str
    organization: bool
    cycle_day: int
    credit_limit: str | None
    prepaid: bool = False


ACCOUNTS_PLAN = [
    AccountPlan("ivan", "James Wilson", False, 1, "100"),
    AccountPlan("anna", "Emily Carter", False, 5, None),
    AccountPlan("romashka", "Acme Corporation", True, 15, "500"),
    AccountPlan("sergey", "Michael Brooks", False, 10, None),  # Suspended later
    AccountPlan("olga", "Sarah Johnson", False, 28, "50"),  # never pays: negative
    AccountPlan("dmitry", "David Thompson", False, 1, None),  # terminated, then Closed
    AccountPlan("elena", "Jessica Martinez", False, 20, None),  # joins today
    # prepaid: tops up, buys, renews from funds, runs out, tops up again
    AccountPlan("pavel", "Robert Taylor", False, 1, None, prepaid=True),
    # prepaid, ran out: her renewal waits for a top-up
    AccountPlan("vera", "Linda Harris", False, 1, None, prepaid=True),
]


def account_name(plan: AccountPlan) -> str:
    return f"{plan.customer} {'corporate' if plan.organization else 'main'}"


# categories, each with the offerings in it; catalogs, each with the categories it lists
CATEGORIES_PLAN = {
    "Mobile": ["MOB-S", "MOB-L"],
    "Home Internet": ["NET-100"],
    "TV": ["TV-BASE"],
}
CATALOGS_PLAN = {
    "Consumer": ["Mobile", "Home Internet", "TV"],
    "Business": ["Mobile", "Home Internet"],
}


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
    numbers: list[dict[str, Any]] = field(default_factory=list)  # the free ones, in order


def build_catalog(api: Api, first_day: date) -> dict[str, dict[str, Any]]:
    start = at(first_day, 0).isoformat()  # the next midnight: the earliest a price may start
    specs: dict[str, dict[str, Any]] = {}
    offerings = {}
    for plan in CATALOG_PLAN:
        if plan.spec not in specs:
            numbered = (
                {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]}
                if plan.spec == MOBILE_SPEC
                else {}
            )
            specs[plan.spec] = api.post(
                f"{CATALOG}/productSpecification",
                {
                    "@type": "ProductSpecification",
                    "name": plan.spec,
                    "lifecycleStatus": "Active",
                    **numbered,
                },
            )
        price = api.post(
            f"{CATALOG}/productOfferingPrice",
            {
                "@type": "ProductOfferingPrice",
                "name": f"{plan.name} monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "USD", "value": plan.price},
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


def build_numbers(api: Api) -> list[dict[str, Any]]:
    """The subscriber numbers 12025550100-12025550119, free until a mobile add takes one."""
    return [
        api.post(
            NUMBERS,
            {
                "@type": "LogicalResource",
                "name": f"MSISDN {value}",
                "value": value,
                "administrativeState": "unlocked",
                "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
            },
        )
        for value in (str(FIRST_NUMBER + i) for i in range(NUMBER_COUNT))
    ]


def build_classification(api: Api, offerings: dict[str, dict[str, Any]]) -> None:
    """Every offering in a category; the catalogs list root categories."""
    categories = {}
    for name, codes in CATEGORIES_PLAN.items():
        categories[name] = api.post(
            f"{CATALOG}/category",
            {
                "@type": "Category",
                "name": name,
                "lifecycleStatus": "Active",
                "productOffering": [
                    {"id": offerings[code]["id"], "@type": "ProductOfferingRef"} for code in codes
                ],
            },
        )
    for name, roots in CATALOGS_PLAN.items():
        api.post(
            f"{CATALOG}/productCatalog",
            {
                "@type": "ProductCatalog",
                "name": f"{name} catalog",
                "lifecycleStatus": "Active",
                "category": [{"id": categories[r]["id"], "@type": "CategoryRef"} for r in roots],
            },
        )


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
        extra: dict[str, Any] = (
            {}
            if plan.credit_limit is None
            else {"creditLimit": {"unit": "USD", "value": int(plan.credit_limit)}}
        )
        if plan.prepaid:
            extra["ratingType"] = "prepaid"
        accounts[plan.key] = api.post(
            ACCOUNT,
            apidata.account_body(
                customer, name=account_name(plan), billCycleDay=plan.cycle_day, **extra
            ),
        )
    return accounts


def order(
    api: Api,
    world: World,
    who: str,
    item: dict[str, Any],
    start: date | None,
    state: str = "completed",
) -> dict:
    body = apidata.order_body(world.accounts[who], item)
    if start is not None:
        body["requestedStartDate"] = iso_noon(start)
    placed = api.post(apidata.ORDER, body)
    if placed["state"] != state:
        raise SeedError(f"order for {who} was {placed['state']}: {placed}")
    return placed


def add_item(world: World, code: str) -> dict[str, Any]:
    """An add item; a mobile add carries the next free number (the specification requires one)."""
    offering = world.offerings[code]
    if not code.startswith("MOB-"):
        return apidata.add_item(offering)
    number = world.numbers.pop(0)
    product = {
        "@type": "ProductRefOrValue",
        "realizingResource": [
            {"id": number["id"], "@type": "ResourceRef", "@referredType": "LogicalResource"}
        ],
    }
    return apidata.add_item(offering, product=product)


def add(api: Api, world: World, key: str, who: str, code: str, start: date) -> None:
    placed = order(api, world, who, add_item(world, code), start)
    world.products[key] = api.get(placed["productOrderItem"][0]["product"]["href"])


def add_prepaid(api: Api, world: World, who: str, code: str) -> None:
    """An immediate prepaid purchase: accepted `inProgress`, completed by the worker's next
    command pass (`settle_prepaid`)."""
    order(api, world, who, add_item(world, code), None, state="inProgress")


def delete(api: Api, world: World, key: str, who: str, end: date | None = None) -> None:
    order(api, world, who, apidata.delete_item(world.products[key]["id"]), end)


def pay(api: Api, world: World, who: str, value: int, method: str) -> None:
    body = apidata.payment_body(world.accounts[who], value)
    body["paymentMethod"] = {"name": method}  # the stored word; echoed as name and @type
    api.post(apidata.PAYMENT, body)


BILLS = "/tmf-api/customerBillManagement/v5/customerBill"


def open_bills(api: Api, world: World, who: str) -> list[dict]:
    """The account's bills that still owe something and take payments, oldest first."""
    bills = api.get(f"{BILLS}?billingAccount.id={world.accounts[who]['id']}&limit=100")
    # the list is newest first (#314): order by bill date here
    found = sorted(
        (b for b in bills if b["state"] in ("sent", "partiallyPaid")),
        key=lambda b: (b["billDate"], b["id"]),
    )
    if not found:
        raise SeedError(f"{who} has no open bill to seed the bill lifecycle with")
    return found


def hold_oldest(api: Api, world: World, who: str) -> None:
    """A disputed bill: the oldest open one goes on hold (TMF678 PATCH)."""
    api.patch(f"{BILLS}/{open_bills(api, world, who)[0]['id']}", {"state": "onHold"})


def pay_newest(api: Api, world: World, who: str, value: int, method: str) -> None:
    """A payment lettered to the newest open bill (TMF676 paymentItem), not the oldest."""
    newest = open_bills(api, world, who)[-1]
    body = apidata.payment_body(world.accounts[who], value)
    body["paymentMethod"] = {"name": method}
    body["paymentItem"] = [
        {
            "item": {"id": newest["id"], "@referredType": "CustomerBill"},
            "amount": {"unit": "USD", "value": value},
        }
    ]
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
    on(d(5), lambda a, w: pay(a, w, "ivan", 150, "Bank card"))
    on(d(5), lambda a, w: pay(a, w, "anna", 60, "Faster Payments"))
    on(d(5), lambda a, w: pay(a, w, "romashka", 200, "Bank transfer"))
    on(d(10), lambda a, w: add(a, w, "olga-mob", "olga", "MOB-L", d(10)))
    on(d(10), lambda a, w: add(a, w, "dmitry-net", "dmitry", "NET-100", d(10)))
    on(d(20), lambda a, w: add(a, w, "ivan-net", "ivan", "NET-100", d(20)))
    on(d(35), lambda a, w: pay(a, w, "ivan", 200, "Bank card"))
    on(d(35), lambda a, w: pay(a, w, "anna", 30, "Faster Payments"))
    on(d(35), lambda a, w: pay(a, w, "romashka", 130, "Bank transfer"))
    on(d(35), lambda a, w: pay(a, w, "sergey", 29, "Bank card"))
    on(d(45), lambda a, w: delete(a, w, "dmitry-net", "dmitry"))  # terminated that day
    on(d(60), lambda a, w: a.patch(w.accounts["dmitry"]["href"], {"state": "Closed"}))
    on(d(65), lambda a, w: pay(a, w, "ivan", 190, "Bank card"))
    on(d(65), lambda a, w: pay(a, w, "anna", 29, "Faster Payments"))
    on(d(65), lambda a, w: pay(a, w, "romashka", 130, "Bank transfer"))
    on(d(70), lambda a, w: add(a, w, "anna-net", "anna", "NET-100", d(70)))
    on(d(80), lambda a, w: a.patch(w.accounts["sergey"]["href"], {"state": "Suspended"}))
    on(d(95), lambda a, w: pay(a, w, "ivan", 194, "Bank card"))
    on(d(95), lambda a, w: pay(a, w, "anna", 94, "Faster Payments"))
    on(d(95), lambda a, w: pay(a, w, "romashka", 130, "Bank transfer"))
    on(d(100), lambda a, w: delete(a, w, "ivan-mob", "ivan"))  # mid-period: a refund
    on(d(110), lambda a, w: pay(a, w, "romashka", 130, "Bank transfer"))
    on(d(4), lambda a, w: pay(a, w, "pavel", 60, "Bank card"))  # a top-up
    on(d(5), lambda a, w: add_prepaid(a, w, "pavel", "MOB-S"))
    on(d(75), lambda a, w: pay(a, w, "pavel", 30, "Bank card"))  # after running out: resumes
    on(d(110), lambda a, w: pay(a, w, "pavel", 30, "Bank card"))  # resumes the lapsed renewal
    on(d(70), lambda a, w: pay(a, w, "vera", 30, "Bank card"))
    on(d(71), lambda a, w: add_prepaid(a, w, "vera", "MOB-S"))  # her renewal at d(101) finds 10
    # today: a product starting in 5 days, one starting today (charged by tomorrow's
    # run), and a termination ordered for 12 days from now (pendingTerminate)
    on(today, lambda a, w: add(a, w, "elena-mob", "elena", "MOB-S", today + timedelta(days=5)))
    on(today, lambda a, w: add(a, w, "elena-net", "elena", "NET-100", today))
    on(today, lambda a, w: delete(a, w, "anna-mob", "anna", today + timedelta(days=12)))
    # the bill lifecycle: Olga (who never pays) disputes her oldest bill; Sergey pays 100
    # towards his newest bill rather than the oldest
    on(today, lambda a, w: hold_oldest(a, w, "olga"))
    on(today, lambda a, w: pay_newest(a, w, "sergey", 10, "Bank card"))

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


def run_billing(conn: psycopg.Connection, day: date, clock: FixedClock) -> None:
    """The billing run of `day` through the worker (`conn` is a worker connection). The
    seed's data bills cleanly, so anything but a completed run is a seed bug."""
    existing = billing.get_run(conn, day)
    if existing is not None:
        raise SeedError(
            f"a billing run for {day} already exists ({existing['status']}); not seeding over it"
        )
    run = bill_date(conn, day, clock=clock, tz=TZ, trigger="scheduled")
    if run["status"] != "completed":
        raise SeedError(f"the billing run of {day} ended {run['status']}: {run['error']}")


def settle_prepaid(conn: psycopg.Connection, clock: FixedClock) -> None:
    """The worker's prepaid passes of the simulated moment: renewals, then the commands
    (purchases, renewals, resumes) they and the day's orders queued."""
    produce_renewals(conn, clock=clock, tz=TZ)
    process_commands(conn, clock=clock, tz=TZ)
    observe_entitlements(conn, clock=clock, tz=TZ)


def seed(client: Any, clock: FixedClock, today: date) -> bool:
    """Seed through `client` (a TmfClient over the app whose clock is `clock`). Returns
    False if the data was already there."""
    api = Api(client, clock)
    start = today - timedelta(days=START_DAYS)
    clock.set(at(start, 9))
    if already_seeded(api):
        return False
    world = World(offerings=build_catalog(api, start + timedelta(days=1)), accounts={}, products={})
    world.accounts = build_accounts(api)
    world.numbers = build_numbers(api)
    build_classification(api, world.offerings)
    plan = events(today, start)
    day = start + timedelta(days=1)
    with worker_db.connect() as worker:  # owns billing while the seed runs
        while day <= today:
            clock.set(at(day, 0, 30))
            run_billing(worker, day, clock)
            if day in plan:
                clock.set(at(day, 10))
                plan[day](api, world)
            clock.set(at(day, 11))
            settle_prepaid(worker, clock)
            day += timedelta(days=1)
    return True


def refuse_unless_local() -> None:
    if settings.app_env.lower() in ("prod", "production"):
        raise SeedError(f"refusing to seed: APP_ENV is {settings.app_env}")
    host = urlparse(settings.database_url).hostname
    if host != "db":
        raise SeedError(f"refusing to seed: DATABASE_URL points at {host!r}, not the local `db`")


def main() -> int:
    import secrets

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
        # In-process, local-only service credential: no app user or persistent secret.
        previous_secret = settings.bss_service_secret
        settings.bss_service_secret = secrets.token_urlsafe(32)
        try:
            client = TmfClient(
                TestClient(
                    app,
                    raise_server_exceptions=True,
                    headers={
                        "X-Bss-Service": settings.bss_service_secret,
                        "X-Remote-User": "mcp-token:seed",
                    },
                )
            )
            created = seed(client, clock, today)
        finally:
            settings.bss_service_secret = previous_secret
            app.dependency_overrides.pop(get_clock, None)
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
