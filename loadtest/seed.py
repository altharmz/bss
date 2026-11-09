"""The load-test dataset: N subscribers with DAYS of history (#289, docs/loadtest.md).

`python -m loadtest.seed --n 100000` (make loadtest-seed). Like `make seed`
(tests/seed.py) it drives the real code in process: the FastAPI app through Starlette's
TestClient with `get_clock` overridden by a FixedClock that moves forward, and the
worker's own functions (the daily run through `bill_date`, then one `tick` of the
evening, and the usage feed step) on one worker connection with the same clock. So only
orders create products and only the billing run creates ABRs and bills.

Timeline: day 0 = today - DAYS. Every day: the billing run of that date at 00:30, the
day's new subscribers between 08:00 and 20:00 (the first min(30, DAYS) days create all
N), the day's top-ups, bill payments, plan changes and terminations at 20:00, one worker
tick at 23:00, and - in the last USAGE_DAYS days - 20 usage records per active line
through the stub network's feed at 23:30. It ends with yesterday's run, so a live worker
carries on with today.

Deterministic: subscriber n's plan comes from (--seed, n) alone. Resumable: each finished
day is a row in `loadtest_progress`; a rerun continues at the first missing day and redoes
it. Redoing is harmless: parties, customers, accounts, numbers and payment methods are
found by name before they are created, orders, payments and top-ups carry an
Idempotency-Key, and usage records a fixed network id. The generator's two tables are
created here, never by an alembic migration.

Refuses unless APP_ENV is `test`, and refuses a database that already holds data it did
not create.
"""

import argparse
import json
import logging
import multiprocessing
import os
import random
import re
import secrets
import sys
import time as monotonic_time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

from app.clock import FixedClock, SystemClock
from app.config import settings

TZ = ZoneInfo(settings.billing_tz)
CREATE_DAYS = 30  # subscribers are created over the first 30 days (fewer if DAYS is shorter)
RECORDS_PER_LINE = 20  # usage records per active line per day
USAGE_BATCH = 500  # what the worker's feed step takes per call (app/worker/usage.py)
OPERATOR = "mcp-token:loadtest"  # the journal scope of the generator's Idempotency-Keys
OUT = Path("loadtest/out/seed-report.json")

PARTY = "/tmf-api/partyManagement/v5/individual"
CUSTOMER = "/tmf-api/customer/v5/customer"
ACCOUNT = "/tmf-api/accountManagement/v5/billingAccount"
NUMBERS = "/tmf-api/resourceInventory/v5/resource"
ORDER = "/tmf-api/productOrderingManagement/v5/productOrder"
PAYMENT = "/tmf-api/payment/v4/payment"
METHODS = "/tmf-api/paymentMethod/v4/paymentMethod"
BALANCE = "/tmf-api/prepayBalanceManagement/v4"
CATALOG = "/tmf-api/productCatalogManagement/v5"

# What a prepaid line is topped up with: its price plus room for the stub's taxes.
TOPUP = {"MOB-S": 40, "MOB-L": 160}
ADDRESSES = (
    {
        "street1": "1 Market St",
        "city": "San Francisco",
        "stateOrProvince": "CA",
        "postCode": "94105",
    },
    {"street1": "5 Main St", "city": "Albany", "stateOrProvince": "NY", "postCode": "12207"},
    {"street1": "200 Congress Ave", "city": "Austin", "stateOrProvince": "TX", "postCode": "78701"},
    {"street1": "10 Pike St", "city": "Seattle", "stateOrProvince": "WA", "postCode": "98101"},
)
USAGE_KINDS = ("data",) * 12 + ("voice",) * 5 + ("sms",) * 3  # 60 / 25 / 15 %

log = logging.getLogger("loadtest.seed")


class SeedError(Exception):
    pass


# --- the plan ---


@dataclass(frozen=True)
class Params:
    n: int
    days: int
    usage_days: int
    seed: int
    day0: date

    @property
    def create_days(self) -> int:
        return min(CREATE_DAYS, self.days)

    def date(self, index: int) -> date:
        return self.day0 + timedelta(days=index)

    def stored(self) -> dict[str, Any]:
        return {**asdict(self), "day0": self.day0.isoformat()}


@dataclass(frozen=True)
class Line:
    index: int  # 0 or 1
    offering: str  # MOB-S or MOB-L
    msisdn: str
    change_day: int | None  # day index of a plan change (the other offering)
    end_day: int | None  # day index of a termination


@dataclass(frozen=True)
class Subscriber:
    n: int
    day: int  # day index of creation
    minute: int  # minutes after 08:00
    prepaid: bool
    cycle_day: int
    has_method: bool
    recurring_topup: bool  # prepaid: tops up every 30 days
    pays_bills: bool  # postpaid: pays each bill 10 days after its date
    address: int
    lines: tuple[Line, ...]

    @property
    def name(self) -> str:
        return f"lt-{self.n}"


def msisdn(line_id: int) -> str:
    """A unique E.164 number per line; the last four digits stay clear of the stub
    network's test suffixes 0000-0002 (app/integrations/network/stub.py)."""
    return f"1302{line_id // 9997:03d}{3 + line_id % 9997:04d}"


def subscriber(params: Params, n: int) -> Subscriber:
    rng = random.Random(f"{params.seed}:{n}")  # noqa: S311 - test data, not security
    created = n * params.create_days // params.n
    prepaid = rng.random() < 0.70
    count = 2 if rng.random() < 0.15 else 1
    lines = []
    for index in range(count):
        offering = "MOB-S" if rng.random() < 0.8 else "MOB-L"
        roll = rng.random()
        later = range(created + 1, params.days)
        when = rng.choice(later) if later else None
        lines.append(
            Line(
                index=index,
                offering=offering,
                msisdn=msisdn(n * 2 + index),
                change_day=when if 0.01 <= roll < 0.03 else None,
                end_day=when if roll < 0.01 else None,
            )
        )
    return Subscriber(
        n=n,
        day=created,
        minute=rng.randrange(12 * 60),
        prepaid=prepaid,
        cycle_day=rng.randint(1, 28),
        has_method=(not prepaid) or rng.random() < 0.5,
        recurring_topup=rng.random() < 0.6,
        pays_bills=rng.random() < 0.9,
        address=rng.randrange(len(ADDRESSES)),
        lines=tuple(lines),
    )


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def iso_noon(day: date) -> str:
    return at(day, 12).isoformat()


# --- talking to the API ---

_DECIMAL = "\x00D"
_DECIMAL_TEXT = re.compile(r'"\\u0000D(-?[0-9.]+)"')


def dumps(body: Any) -> str:
    """JSON with every Decimal written as its exact number (never through a float)."""

    def encode(value: Any) -> str:
        if isinstance(value, Decimal):
            return f"{_DECIMAL}{value}"
        raise TypeError(f"not JSON serializable: {type(value).__name__}")

    return _DECIMAL_TEXT.sub(r"\1", json.dumps(body, default=encode))


class Api:
    """The in-process API with the generator's clock; anything but the expected status is
    a generator bug and stops the run."""

    def __init__(self, client: Any, clock: FixedClock) -> None:
        self.client = client
        self.clock = clock

    def call(self, method: str, url: str, body: Any = None, key: str | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        content = None if body is None else dumps(body)
        response = self.client.request(method, url, content=content, headers=headers)
        if response.status_code not in (200, 201):
            raise SeedError(f"{method} {url} -> {response.status_code}: {response.text[:500]}")
        return response.json()

    def post(self, url: str, body: Any, key: str | None = None) -> Any:
        return self.call("POST", url, body, key)

    def get(self, url: str) -> Any:
        return self.call("GET", url)


def open_api(clock: FixedClock) -> Api:
    """The app in this process, its clock overridden, behind a service credential that
    exists only in this process (as tests/seed.py does)."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_clock
    from app.main import app

    settings.bss_service_secret = secrets.token_urlsafe(32)
    app.dependency_overrides[get_clock] = lambda: clock
    client = TestClient(
        app,
        raise_server_exceptions=True,
        headers={"X-Bss-Service": settings.bss_service_secret, "X-Remote-User": OPERATOR},
    )
    client.__enter__()  # one event-loop thread for every request (tests/conftest.py)
    return Api(client, clock)


def close_api(api: Api) -> None:
    from app.api.deps import get_clock
    from app.main import app

    api.client.__exit__(None, None, None)
    app.dependency_overrides.pop(get_clock, None)


def ref(kind: str, url: str, row_id: Any, **extra: Any) -> dict[str, Any]:
    return {"id": str(row_id), "href": f"{url}/{row_id}", "@type": kind, **extra}


# --- the generator's own tables ---


def prepare(conn: psycopg.Connection, params: Params) -> Params:
    """Create the progress tables, or check a rerun's parameters against the first run's.
    A database with data and no generator row is not ours: refused."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS loadtest_config"
        " (id int PRIMARY KEY CHECK (id = 1), params jsonb NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS loadtest_progress"
        " (day date PRIMARY KEY, completed_at timestamptz NOT NULL DEFAULT now())"
    )
    row = conn.execute("SELECT params FROM loadtest_config").fetchone()
    if row is None:
        if conn.execute("SELECT EXISTS (SELECT FROM billing_account)").fetchone()[0]:
            raise SeedError(
                "the database already holds accounts the generator did not create;"
                " start from an empty database (make loadtest-reset)"
            )
        conn.execute("INSERT INTO loadtest_config VALUES (1, %s)", (json.dumps(params.stored()),))
        return params
    stored = row[0]
    first = Params(**{**stored, "day0": date.fromisoformat(stored["day0"])})
    if (first.n, first.days, first.usage_days, first.seed) != (
        params.n,
        params.days,
        params.usage_days,
        params.seed,
    ):
        raise SeedError(f"this database was seeded with {stored}; rerun with the same parameters")
    return first  # day 0 stays the first run's


def done_days(conn: psycopg.Connection) -> set[date]:
    return {row[0] for row in conn.execute("SELECT day FROM loadtest_progress")}


# --- the catalog ---


def ensure_catalog(api: Api, params: Params) -> dict[str, dict[str, Any]]:
    """The offerings `make seed` defines (tests/seed.py CATALOG_PLAN), priced from day 0."""
    from tests import seed as demo

    codes = [plan.code for plan in demo.CATALOG_PLAN]
    found = {
        code: api.get(f"{CATALOG}/productOffering?externalIdentifier.id={code}") for code in codes
    }
    if all(found.values()):
        return {code: rows[0] for code, rows in found.items()}
    if any(found.values()):
        raise SeedError("a partial catalog is in the database; reset it (make loadtest-reset)")
    api.clock.set(at(params.day0 - timedelta(days=1), 9))
    offerings = demo.build_catalog(api, params.day0)
    demo.build_classification(api, offerings)
    return offerings


# --- operators ---

OPERATORS = [f"lt-op-{i:02d}" for i in range(1, 21)]
ADMIN = "lt-admin"
SESSION = "/internal/v1/session"


def ensure_users(clock: FixedClock, password: str) -> int:
    """`lt-admin` (role admin) and the 20 UI operators `lt-op-01`..`lt-op-20` (role user),
    all with `password`, the way a real installation gets them: the administrator from the
    `app.users` command, the operators from the Users API, each first sign-in followed by
    the password change it requires. Returns how many users were created."""
    import subprocess

    from fastapi.testclient import TestClient

    from app.main import app

    def client() -> Any:
        return TestClient(app, raise_server_exceptions=True, headers={"X-Bss-Request": "1"})

    def sign_in(session: Any, username: str, secret: str) -> Any:
        clock.set(clock.now() + timedelta(minutes=1))  # under the sign-in throttle
        return session.post(SESSION, json={"username": username, "password": secret})

    def first_sign_in(username: str, temporary: str) -> None:
        with client() as session:
            if sign_in(session, username, temporary).status_code != 200:
                raise SeedError(f"{username} cannot sign in with its first password")
            changed = session.post(
                f"{SESSION}/password",
                json={"currentPassword": temporary, "newPassword": password},
            )
            if changed.status_code != 204:
                raise SeedError(f"{username}: password change -> {changed.status_code}")

    created = 0
    with psycopg.connect(settings.database_url) as conn:
        present = {
            row[0]
            for row in conn.execute(
                "SELECT username FROM app_user WHERE username = ANY(%s)", ([ADMIN, *OPERATORS],)
            )
        }
    if ADMIN not in present:
        temporary = secrets.token_urlsafe(18)
        subprocess.run(  # noqa: S603 - fixed argv, the real bootstrap command
            [sys.executable, "-m", "app.users", "create", ADMIN, "--role", "admin"],
            input=temporary + "\n",
            text=True,
            check=True,
        )
        first_sign_in(ADMIN, temporary)
        created += 1
    missing = [name for name in OPERATORS if name not in present]
    if not missing:
        return created
    with client() as admin:
        if sign_in(admin, ADMIN, password).status_code != 200:
            raise SeedError(f"{ADMIN} cannot sign in with LOADTEST_OPERATOR_PASSWORD")
        temporary = {}
        for name in missing:
            temporary[name] = secrets.token_urlsafe(18)
            made = admin.post(
                "/internal/v1/users",
                json={"username": name, "role": "user", "password": temporary[name]},
            )
            if made.status_code != 201:
                raise SeedError(f"creating {name} -> {made.status_code}: {made.text[:300]}")
    for name in missing:
        first_sign_in(name, temporary[name])
        created += 1
    return created


def catalog_ids(api: Api) -> dict[str, str]:
    return {
        code: api.get(f"{CATALOG}/productOffering?externalIdentifier.id={code}")[0]["id"]
        for code in TOPUP
    }


# --- creating subscribers ---


def existing(conn: psycopg.Connection, subs: list[Subscriber]) -> dict[str, dict[str, str]]:
    """What a crashed earlier attempt of this day already created, by name."""
    names = [s.name for s in subs]
    numbers = [line.msisdn for s in subs for line in s.lines]

    def lookup(sql: str, values: list[str]) -> dict[str, str]:
        return {key: str(value) for key, value in conn.execute(sql, (values,)).fetchall()}

    return {
        "party": lookup(
            "SELECT family_name, id FROM party WHERE family_name = ANY(%s)"
            " AND party_type = 'Individual'",
            names,
        ),
        "customer": lookup("SELECT name, id FROM customer WHERE name = ANY(%s)", names),
        "account": lookup("SELECT name, id FROM billing_account WHERE name = ANY(%s)", names),
        "number": lookup(
            "SELECT value, id FROM subscriber_number WHERE value = ANY(%s)"
            " AND resource_specification_id = 'msisdn'",
            numbers,
        ),
    }


def contact(address: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "@type": "Contact",
            "contactType": "serviceAddress",
            "contactMedium": [
                {"@type": "GeographicAddressContactMedium", "country": "US", **address}
            ],
        }
    ]


def bucket_of(api: Api, account_id: str) -> str:
    buckets = api.get(f"{BALANCE}/bucket?partyAccount.id={account_id}")
    if not buckets:
        raise SeedError(f"prepaid account {account_id} has no bucket")
    return buckets[0]["id"]


def topup(api: Api, account_id: str, value: int, key: str) -> None:
    api.post(
        f"{BALANCE}/topupBalance",
        {
            "@type": "TopupBalance",
            "amount": {"amount": value, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": bucket_of(api, account_id)},
            "partyAccount": {"id": account_id, "@referredType": "BillingAccount"},
        },
        key=key,
    )


def create(
    api: Api, sub: Subscriber, day: date, have: dict[str, dict[str, str]], offerings: dict[str, str]
) -> Counter:
    """One subscriber: party, customer, account, numbers, payment method, then the order
    for its lines (prepaid: after a top-up; the worker's tick completes the purchase)."""
    counts: Counter = Counter()
    api.clock.set(at(day, 8 + sub.minute // 60, sub.minute % 60))
    party_id = (
        have["party"].get(sub.name)
        or api.post(PARTY, {"@type": "Individual", "givenName": "Load", "familyName": sub.name})[
            "id"
        ]
    )
    customer_id = (
        have["customer"].get(sub.name)
        or api.post(
            CUSTOMER,
            {
                "@type": "Customer",
                "name": sub.name,
                "status": "Active",
                "engagedParty": ref("PartyRef", PARTY, party_id, **{"@referredType": "Individual"}),
            },
        )["id"]
    )
    account_id = have["account"].get(sub.name)
    resumed = account_id is not None
    if account_id is None:
        body: dict[str, Any] = {
            "@type": "BssBillingAccount",
            "@baseType": "BillingAccount",
            "name": sub.name,
            "billCycleDay": sub.cycle_day,
            "currency": "USD",
            "contact": contact(ADDRESSES[sub.address]),
            "relatedParty": [
                {
                    "@type": "RelatedPartyRefOrPartyRoleRef",
                    "role": "customer",
                    "partyOrPartyRole": ref(
                        "PartyRoleRef",
                        CUSTOMER,
                        customer_id,
                        name=sub.name,
                        **{"@referredType": "Customer"},
                    ),
                }
            ],
        }
        if sub.prepaid:
            body["ratingType"] = "prepaid"
        account_id = api.post(ACCOUNT, body)["id"]
    numbers = {}
    for line in sub.lines:
        numbers[line.index] = (
            have["number"].get(line.msisdn)
            or api.post(
                NUMBERS,
                {
                    "@type": "LogicalResource",
                    "name": f"MSISDN {line.msisdn}",
                    "value": line.msisdn,
                    "administrativeState": "unlocked",
                    "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                },
            )["id"]
        )
    if sub.has_method and not (resumed and api.get(f"{METHODS}?account.id={account_id}")):
        api.post(
            METHODS,
            {
                "@type": "tokenizedCard",
                "name": "Load test card",
                "account": [{"id": account_id, "@referredType": "BillingAccount"}],
                "brand": "visa",
                "lastFourDigits": "4242",
                "token": "tok_visa",
            },
        )
        counts["paymentMethods"] += 1
    if sub.prepaid:
        value = sum(TOPUP[line.offering] for line in sub.lines)
        topup(api, account_id, value, key=f"lt-{sub.n}-topup-{sub.day}")
        counts["topups"] += 1
    order: dict[str, Any] = {
        "@type": "ProductOrder",
        "billingAccount": {"id": account_id, "@type": "BillingAccountRef"},
        "productOrderItem": [
            {
                "@type": "ProductOrderItem",
                "id": str(line.index + 1),
                "action": "add",
                "productOffering": {"id": offerings[line.offering], "@type": "ProductOfferingRef"},
                "product": {
                    "@type": "ProductRefOrValue",
                    "realizingResource": [
                        {
                            "id": numbers[line.index],
                            "@type": "ResourceRef",
                            "@referredType": "LogicalResource",
                        }
                    ],
                },
            }
            for line in sub.lines
        ],
    }
    if not sub.prepaid:
        order["requestedStartDate"] = iso_noon(day)
    placed = api.post(ORDER, order, key=f"lt-{sub.n}-add")
    counts[f"order.add.{placed['state']}"] += 1
    counts["subscribers"] += 1
    counts["lines"] += len(sub.lines)
    return counts


def create_all(
    api: Api,
    conn: psycopg.Connection,
    subs: list[Subscriber],
    day: date,
    offerings: dict[str, str],
    on_subscriber: Callable[[int], None] | None = None,
) -> Counter:
    counts: Counter = Counter()
    have = existing(conn, subs)
    for done, sub in enumerate(sorted(subs, key=lambda s: (s.minute, s.n)), 1):
        counts += create(api, sub, day, have, offerings)
        if on_subscriber is not None:
            on_subscriber(done)
    return counts


# A creation process (--processes > 1): its own app, client, clock and connection.
_worker: dict[str, Any] = {}


def _init_worker(params: Params) -> None:
    logging.getLogger().setLevel(logging.WARNING)  # no per-request lines from the client
    clock = FixedClock(at(params.day0, 8))
    _worker["api"] = open_api(clock)
    _worker["conn"] = psycopg.connect(settings.database_url, autocommit=True)
    _worker["offerings"] = catalog_ids(_worker["api"])


def _create_chunk(subs: list[Subscriber], day: date) -> Counter:
    return create_all(_worker["api"], _worker["conn"], subs, day, _worker["offerings"])


# --- the day's other activity ---


def product_of(conn: psycopg.Connection, number: str) -> str | None:
    row = conn.execute(
        "SELECT a.product_id FROM number_assignment a"
        " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
        " WHERE n.resource_specification_id = 'msisdn' AND n.value = %s"
        " AND a.cancelled_at IS NULL ORDER BY a.effective_from DESC LIMIT 1",
        (number,),
    ).fetchone()
    return None if row is None else str(row[0])


def account_id_of(conn: psycopg.Connection, sub: Subscriber) -> str:
    return str(
        conn.execute("SELECT id FROM billing_account WHERE name = %s", (sub.name,)).fetchone()[0]
    )


def activity(
    api: Api,
    conn: psycopg.Connection,
    params: Params,
    index: int,
    events: dict[int, list[tuple[str, Subscriber, Line | None]]],
    by_n: dict[int, Subscriber],
    offerings: dict[str, str],
) -> Counter:
    """20:00: recurring top-ups, plan changes and terminations planned for the day, and
    the payment of every bill dated 10 days ago by the postpaid subscribers who pay."""
    counts: Counter = Counter()
    day = params.date(index)
    api.clock.set(at(day, 20))
    for kind, sub, line in events.get(index, []):
        if kind == "topup":
            ended = sum(1 for ln in sub.lines if ln.end_day is not None and ln.end_day < index)
            if ended == len(sub.lines):
                continue
            value = sum(TOPUP[ln.offering] for ln in sub.lines)
            topup(api, account_id_of(conn, sub), value, key=f"lt-{sub.n}-topup-{index}")
            counts["topups"] += 1
            continue
        assert line is not None
        product = product_of(conn, line.msisdn)
        if product is None:
            counts[f"{kind}.noProduct"] += 1
            continue
        if kind == "change":
            other = "MOB-L" if line.offering == "MOB-S" else "MOB-S"
            item = {
                "action": "modify",
                "product": {"id": product, "@type": "ProductRef"},
                "productOffering": {"id": offerings[other], "@type": "ProductOfferingRef"},
            }
        else:
            item = {"action": "delete", "product": {"id": product, "@type": "ProductRef"}}
        order = {
            "@type": "ProductOrder",
            "billingAccount": {"id": account_id_of(conn, sub), "@type": "BillingAccountRef"},
            "productOrderItem": [{"@type": "ProductOrderItem", "id": "1", **item}],
            "requestedStartDate": iso_noon(effective(conn, sub, kind, product, day)),
        }
        placed = api.post(ORDER, order, key=f"lt-{sub.n}-{kind}-{line.index}")
        counts[f"order.{kind}.{placed['state']}"] += 1
    bills = conn.execute(
        "SELECT b.id, a.id, a.name, b.amount_due FROM customer_bill b"
        " JOIN billing_account a ON a.id = b.billing_account_id"
        " WHERE b.bill_date = %s AND a.rating_type = 'postpaid' AND a.name LIKE 'lt-%%'"
        " AND b.amount_due > 0 ORDER BY b.id",
        (day - timedelta(days=10),),
    ).fetchall()
    for bill_id, account, name, amount in bills:
        if not by_n[int(name.removeprefix("lt-"))].pays_bills:
            continue
        api.post(
            PAYMENT,
            {
                "@type": "Payment",
                "account": {
                    "id": str(account),
                    "href": f"{ACCOUNT}/{account}",
                    "@referredType": "BillingAccount",
                },
                "totalAmount": {"unit": "USD", "value": amount},
                "paymentMethod": {"name": "bankTransfer", "@type": "BankAccountTransfer"},
            },
            key=f"lt-pay-{bill_id}",
        )
        counts["payments"] += 1
    return counts


def effective(
    conn: psycopg.Connection, sub: Subscriber, kind: str, product: str, day: date
) -> date:
    """When a change or termination ordered on `day` takes effect: tomorrow, except a
    prepaid plan change, which waits for the renewal (the latest grant's paid-through
    date, or today when that has passed) so that it needs no funds held
    (docs/decisions/prepaid.md, P3h)."""
    if not (sub.prepaid and kind == "change"):
        return day + timedelta(days=1)
    row = conn.execute(
        "SELECT max(upper(period)) FROM funded_interval WHERE product_id = %s", (product,)
    ).fetchone()
    return max(day, row[0]) if row[0] is not None else day


def plan_events(
    params: Params, subs: list[Subscriber]
) -> dict[int, list[tuple[str, Subscriber, Line | None]]]:
    events: dict[int, list[tuple[str, Subscriber, Line | None]]] = {}
    for sub in subs:
        if sub.prepaid and sub.recurring_topup:
            for index in range(sub.day + 30, params.days, 30):
                events.setdefault(index, []).append(("topup", sub, None))
        for line in sub.lines:
            if line.change_day is not None:
                events.setdefault(line.change_day, []).append(("change", sub, line))
            if line.end_day is not None:
                events.setdefault(line.end_day, []).append(("end", sub, line))
    return events


def active_lines(conn: psycopg.Connection, day: date) -> list[str]:
    """The generator's numbers held by a line on `day`."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT n.value FROM number_assignment a"
            " JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " WHERE n.resource_specification_id = 'msisdn' AND n.value LIKE '1302%%'"
            " AND a.cancelled_at IS NULL AND a.valid_for @> %s::date ORDER BY n.value",
            (day,),
        )
    ]


def records(number: str, day: date) -> list[tuple[str, str, int, datetime]]:
    """The line's 20 usage records of the day: (network id, kind, quantity, event time)."""
    rng = random.Random(f"{number}:{day}")  # noqa: S311 - test data
    out = []
    for i, kind in enumerate(USAGE_KINDS):
        quantity = {
            "data": rng.randint(100_000, 50_000_000),
            "voice": rng.randint(10, 600),
            "sms": 1,
        }[kind]
        event_at = at(day, 0) + timedelta(minutes=i * 69 + rng.randrange(60))
        out.append((f"lt-{number}-{day.isoformat()}-{i}", kind, quantity, event_at))
    return out


def usage(
    conn: psycopg.Connection, worker: psycopg.Connection, day: date, clock: FixedClock
) -> int:
    """23:30, one process: 20 records for every line that holds its number today, through
    the stub network and the worker's feed step, one feed batch at a time."""
    from app.integrations.network import adapter
    from app.worker.usage import process_usage

    clock.set(at(day, 23, 30))
    network = adapter()
    stored = 0
    pending = 0
    for number in active_lines(conn, day):
        for record_id, kind, quantity, event_at in records(number, day):
            network.inject_usage(record_id, kind, quantity, event_at, msisdn=number)
            pending += 1
            if pending == USAGE_BATCH:
                stored += process_usage(worker, clock=clock, tz=TZ)
                pending = 0
    while pending:
        stored += process_usage(worker, clock=clock, tz=TZ)
        pending = len(network.usage_records(USAGE_BATCH))
    return stored


def _usage_chunk(numbers: list[str], day: date) -> int:
    """23:30, in a creation process (--processes > 1): the same records through the feed's
    own `ingest` (app/network/usage.py), on this process's connection, in transactions of
    one feed batch. Lines are split between the processes, so no two touch one line.
    Approved by the human 2026-11-09 to bring the 100k seed within a day."""
    from app.network.usage import Incoming, ingest

    conn = _worker["conn"]
    now = at(day, 23, 30)
    batch = [(number, rec) for number in numbers for rec in records(number, day)]
    stored = 0
    for start in range(0, len(batch), USAGE_BATCH):
        with conn.transaction():
            for number, (record_id, kind, quantity, event_at) in batch[start : start + USAGE_BATCH]:
                _, new = ingest(
                    conn,
                    Incoming(
                        network_record_id=record_id,
                        kind=kind,
                        quantity=quantity,
                        event_at=event_at,
                        msisdn=number,
                    ),
                    now=now,
                    tz=TZ,
                )
                stored += new
    return stored


# --- the run ---


@dataclass
class Report:
    params: dict[str, Any]
    days_run: int = 0
    phases: dict[str, float] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)

    def timed(self, phase: str, started: float) -> None:
        self.phases[phase] = self.phases.get(phase, 0.0) + monotonic_time.monotonic() - started


def commit_wait(conn: psycopg.Connection, *, wait: bool) -> None:
    """--fast-commit: while the generator runs, new sessions of the database do not wait
    for the WAL flush at commit (`synchronous_commit = off`; a crash loses at most the
    last moments, which a rerun redoes). Restored at the end, so the load tests run with
    the default. Approved by the human 2026-11-09."""
    name = conn.execute("SELECT current_database()").fetchone()[0]
    action = "RESET synchronous_commit" if wait else "SET synchronous_commit = off"
    conn.execute(sql.SQL("ALTER DATABASE {} " + action).format(sql.Identifier(name)))


def run(
    params: Params,
    *,
    password: str,
    processes: int = 1,
    fast_commit: bool = False,
    on_subscriber: Callable[[int, int], None] | None = None,
) -> Report:
    """Seed (or continue seeding) the database of settings.database_url."""
    from app.worker import db as worker_db
    from app.worker.loop import tick
    from app.worker.process import bill_date
    from app.worker.runtime import check_run_at

    if settings.app_env != "test":
        raise SeedError(f"refusing to seed: APP_ENV is {settings.app_env!r}, not 'test'")
    from app.users import password_policy

    try:
        password_policy(OPERATORS[0], password)
    except Exception:  # noqa: BLE001 - the API's own rule, reported as the generator's
        raise SeedError("LOADTEST_OPERATOR_PASSWORD must be 12-1024 characters") from None
    run_at = check_run_at(settings.billing_run_at)
    conn = psycopg.connect(settings.database_url, autocommit=True)
    params = prepare(conn, params)
    report = Report(params=params.stored())
    if fast_commit:
        commit_wait(conn, wait=False)
    clock = FixedClock(at(params.day0 - timedelta(days=1), 9))
    subs = [subscriber(params, n) for n in range(params.n)]
    by_n = {s.n: s for s in subs}
    by_day: dict[int, list[Subscriber]] = {}
    for sub in subs:
        by_day.setdefault(sub.day, []).append(sub)
    events = plan_events(params, subs)
    finished = done_days(conn)
    pool = None
    api = None
    try:
        api = open_api(clock)
        started = monotonic_time.monotonic()
        offerings = {code: row["id"] for code, row in ensure_catalog(api, params).items()}
        report.counts["users"] += ensure_users(clock, password)
        report.timed("catalog", started)
        if processes > 1:
            context = multiprocessing.get_context("spawn")
            pool = context.Pool(processes, initializer=_init_worker, initargs=(params,))
        with worker_db.connect() as worker:  # owns billing while the generator runs
            for index in range(params.days):
                day = params.date(index)
                if day in finished:
                    continue
                t = monotonic_time.monotonic()
                clock.set(at(day, 0, 30))
                result = bill_date(worker, day, clock=clock, tz=TZ, trigger="scheduled")
                if result["status"] != "completed":
                    raise SeedError(f"the billing run of {day} ended {result['status']}")
                report.counts["billedAccounts"] += conn.execute(
                    "SELECT count(*) FROM billing_run_account WHERE run_id = %s",
                    (result["id"],),
                ).fetchone()[0]
                report.timed("billing", t)

                t = monotonic_time.monotonic()
                today = by_day.get(index, [])
                if pool is not None and today:
                    chunks = [today[i::processes] for i in range(processes)]
                    for counts in pool.starmap(_create_chunk, [(c, day) for c in chunks if c]):
                        report.counts += counts
                elif today:
                    hook = None if on_subscriber is None else partial(on_subscriber, index)
                    report.counts += create_all(api, conn, today, day, offerings, hook)
                report.timed("subscribers", t)

                t = monotonic_time.monotonic()
                report.counts += activity(api, conn, params, index, events, by_n, offerings)
                report.timed("activity", t)

                t = monotonic_time.monotonic()
                clock.set(at(day, 23))
                tick(worker, clock=clock, tz=TZ, run_at=run_at)
                report.timed("tick", t)

                if index >= params.days - params.usage_days:
                    t = monotonic_time.monotonic()
                    if pool is None:
                        report.counts["usageRecords"] += usage(conn, worker, day, clock)
                    else:
                        lines = active_lines(conn, day)
                        chunks = [(lines[i::processes], day) for i in range(processes)]
                        report.counts["usageRecords"] += sum(pool.starmap(_usage_chunk, chunks))
                    report.timed("usage", t)
                conn.execute("INSERT INTO loadtest_progress (day) VALUES (%s)", (day,))
                report.days_run += 1
                log.warning(
                    "day %s (%d/%d) done: %s", day, index + 1, params.days, dict(report.counts)
                )
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
        if api is not None:
            close_api(api)
        if fast_commit:
            commit_wait(conn, wait=True)
        conn.close()
    report.phases["total"] = sum(v for k, v in report.phases.items() if k != "total")
    return report


def table_rows(conn: psycopg.Connection) -> dict[str, int]:
    conn.execute("ANALYZE")
    return {
        name: int(rows)
        for name, rows in conn.execute(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC"
        )
        if rows
    }


def write_report(report: Report, path: Path) -> dict[str, Any]:
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        rows = table_rows(conn)
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    total = report.phases.get("total") or 0.0
    phases = report.phases

    def rate(count_key: str, phase: str) -> float | None:
        seconds = phases.get(phase)
        return round(report.counts[count_key] / seconds, 1) if seconds else None

    data = {
        "params": report.params,
        "daysRun": report.days_run,
        "seconds": {k: round(v, 1) for k, v in phases.items()},
        "counts": dict(sorted(report.counts.items())),
        "rates": {
            "subscribersPerSecond": rate("subscribers", "subscribers"),
            "billedAccountsPerSecond": rate("billedAccounts", "billing"),
            "usageRecordsPerSecond": rate("usageRecords", "usage"),
            "rowsPerSecond": round(sum(rows.values()) / total, 1) if total else None,
        },
        "databaseBytes": size,
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m loadtest.seed")
    parser.add_argument("--n", type=int, required=True, help="subscribers")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--usage-days", type=int, default=30)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--seed", type=int, default=289)
    parser.add_argument(
        "--fast-commit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="synchronous_commit off while seeding (default on)",
    )
    parser.add_argument("--report", type=Path, default=OUT)
    args = parser.parse_args(argv)
    logging.getLogger().setLevel(logging.WARNING)  # no per-request lines from the client
    if args.n < 1 or args.days < 1 or not 0 <= args.usage_days <= args.days or args.processes < 1:
        parser.error("need N >= 1, DAYS >= 1, 0 <= USAGE_DAYS <= DAYS and PROCESSES >= 1")
    params = Params(
        n=args.n,
        days=args.days,
        usage_days=args.usage_days,
        seed=args.seed,
        day0=SystemClock().today() - timedelta(days=args.days),
    )
    try:
        report = run(
            params,
            password=os.environ.get("LOADTEST_OPERATOR_PASSWORD", ""),
            processes=args.processes,
            fast_commit=args.fast_commit,
        )
    except SeedError as error:
        print(f"loadtest seed: {error}", file=sys.stderr)
        return 1
    data = write_report(report, args.report)
    print(json.dumps({k: data[k] for k in ("daysRun", "seconds", "counts", "rates")}, indent=2))
    print(f"loadtest seed: report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
