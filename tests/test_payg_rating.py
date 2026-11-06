"""Pay-as-you-go rating of usage against the prepaid balance (issue #191)."""
# ruff: noqa: F811

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest

from app.integrations.network import adapter as network
from app.repository import usage as usage_repo
from tests import apidata, contract, timeline
from tests.apidata import CATALOG
from tests.test_prepaid_plans import (  # noqa: F401 - fixtures and helpers
    RATES,
    VAT,
    at,
    balance,
    build,
    buy,
    create,
    offering,
    stubs,
)
from tests.test_prepaid_renewal_producer import tick

pytestmark = pytest.mark.capability("worker.usageRating")

USAGE = "/tmf-api/productUsageManagement/v5/productUsage"
SUMMARY = "/internal/v1/usage/summary"
TOPUP = "/tmf-api/prepayBalanceManagement/v4/topupBalance"
MIB = 1024 * 1024
RECORDS = iter(range(10**6))


def usage_price(api, units, value, amount=1, name=None, tax=VAT, **over):
    body = {
        "@type": "ProductOfferingPrice",
        "name": name or f"Usage per {units}",
        "lifecycleStatus": "Active",
        "priceType": "usage",
        "price": {"unit": "USD", "value": value},
        "validFor": {"startDateTime": timeline.iso(timeline.START)},
        "tax": tax,
        "unitOfMeasure": {"amount": amount, "units": units},
    } | over
    return create(api, "productOfferingPrice", body)


@pytest.fixture
def payg(api, db_conn, build):
    """A funded prepaid account with a pay-as-you-go line whose offering rates data per
    MB (0.10), voice per minute (0.05) and SMS per message (0.02), each +20% tax."""

    def make(funds="10", kinds=("data", "voice", "sms")):
        world = build(funds)
        prices = {
            "data": lambda: usage_price(api, "MB", 0.10, name="Data"),
            "voice": lambda: usage_price(api, "minute", 0.05, name="Voice"),
            "sms": lambda: usage_price(api, "message", 0.02, name="SMS"),
        }
        api.clock.set(timeline.START)  # prices valid from the start of the timeline
        world.offering = offering(
            api, "Usage line", [prices[k]() for k in kinds], kind="payAsYouGo"
        )
        api.clock.set(timeline.START + timedelta(hours=9))
        placed = apidata.order(api, world.acc, apidata.add_item(world.offering))
        assert placed["state"] == "inProgress", placed
        tick(db_conn, api.clock.now())
        done = api.get(placed["href"]).json()
        assert done["state"] == "completed", done
        world.product = apidata.product_of(api, done)
        network().received.clear()
        return world

    return make


def report(api, product, kind, quantity, when, record=None):
    record = record or f"mediation-{next(RECORDS)}"
    body = {
        "@type": "ProductUsage",
        "usageSpecification": {"id": kind, "@type": "ProductUsageSpecificationRef"},
        "product": {"id": product["id"], "@type": "ProductRef", "@referredType": "Product"},
        "usageCharacteristic": [
            {
                "@type": "IntegerCharacteristic",
                "name": "quantity",
                "valueType": "integer",
                "value": quantity,
            },
            {
                "@type": "StringCharacteristic",
                "name": "eventTime",
                "valueType": "string",
                "value": when.isoformat(),
            },
        ],
        "externalIdentifier": [{"@type": "ExternalIdentifier", "owner": "mediation", "id": record}],
    }
    response = api.post(USAGE, body)
    assert response.status_code == 201, response.text
    return response.json()


def top_up(api, db_conn, world, value):
    """A balance top-up through TMF654: the payment settles the account's open bills."""
    bucket = db_conn.execute("SELECT id FROM prepaid_bucket").fetchone()[0]
    db_conn.commit()
    response = api.post(
        TOPUP,
        {
            "@type": "TopupBalance",
            "amount": {"amount": float(value), "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": str(bucket)},
            "partyAccount": {"id": world.acc["id"], "@referredType": "BillingAccount"},
        },
    )
    assert response.status_code == 201, response.text


def next_day(api, db_conn, days=1):
    api.clock.set(at(api.clock.today() + timedelta(days=days)))
    return tick(db_conn, api.clock.now())


def rates(db_conn):
    db_conn.commit()
    return db_conn.execute(
        "SELECT charge_name, amount, tax_amount, lower(period), customer_bill_id"
        " FROM applied_customer_billing_rate WHERE usage_command_id IS NOT NULL"
        " ORDER BY charge_name, lower(period), created_at"
    ).fetchall()


def commands_sent(kind):
    return [key for key, (command, _) in network().received.items() if command == kind]


def test_records_rate_to_exact_decimal_amounts_with_the_rounding_unit(api, db_conn, payg):
    world = payg("10")
    start = api.clock.now()
    # data: 1.5 + 0.7 MiB = 2.2 MiB, rounded up once to 3 MB blocks; voice 61 s + 60 s = 121 s
    # = 3 minutes; sms 5 messages
    sent = [
        report(api, world.product, "data", int(1.5 * MIB), start),
        report(api, world.product, "data", int(0.7 * MIB), start + timedelta(hours=1)),
        report(api, world.product, "voice", 61, start + timedelta(hours=2)),
        report(api, world.product, "voice", 60, start + timedelta(hours=3)),
        report(api, world.product, "sms", 5, start + timedelta(hours=4)),
    ]
    tick(db_conn, api.clock.now())
    assert {r["status"] for r in sent} == {"notRated"}
    assert rates(db_conn) == []  # the day is not closed: nothing is rated or charged yet
    assert balance(db_conn, world.acc) == D("10")
    next_day(api, db_conn)
    charged = rates(db_conn)
    assert [(n, a, t) for n, a, t, _, _ in charged] == [
        ("Data", D("0.30"), D("0.06")),
        ("SMS", D("0.10"), D("0.02")),
        ("Voice", D("0.15"), D("0.03")),
    ]
    assert {day for _, _, _, day, _ in charged} == {start.date()}  # charged for the event day
    assert len({bill for *_, bill in charged}) == 1  # one bill for the pass
    assert balance(db_conn, world.acc) == D("10") - D("0.66")
    ((state, due),) = db_conn.execute("SELECT state, amount_due FROM customer_bill").fetchall()[-1:]
    assert (state, due) == ("settled", D("0.66"))  # paid from the balance at once
    # TMF635: rated, with the price applied
    for record in sent:
        rated = api.get(record["href"]).json()
        contract.assert_valid("TMF635", "ProductUsage", rated)
        assert rated["status"] == "rated"
    data = api.get(sent[0]["href"]).json()["usageProductPrice"][0]
    assert data["name"] == "Data" and data["priceType"] == "usage" and data["unitOfMeasure"] == "MB"
    assert data["price"]["dutyFreeAmount"] == {"unit": "USD", "value": 0.1}
    # TMF678: the charges, named after their prices, on the ledger
    listed = api.get(f"{RATES}?product.id={world.product['id']}").json()
    by_name = {r["name"]: r for r in listed}
    assert by_name["Data"]["appliedBillingRateType"] == "appliedBillingCharge"
    assert by_name["Data"]["taxExcludedAmount"]["value"] == 0.3
    assert by_name["Data"]["taxIncludedAmount"]["value"] == 0.36
    for rate in listed:
        contract.assert_valid("TMF678", "AppliedCustomerBillingRate", rate)


def test_a_tiny_block_is_rounded_per_group_not_per_record(api, db_conn, payg):
    world = payg("10")
    start = api.clock.now()
    for index in range(4):  # four 300 KB records: 1.17 MiB together -> 2 MB blocks, not 4
        report(api, world.product, "data", 300 * 1024, start + timedelta(minutes=index))
    next_day(api, db_conn)
    assert [(n, a) for n, a, *_ in rates(db_conn)] == [("Data", D("0.20"))]


def test_a_record_without_a_usage_price_is_rated_with_no_charge(api, db_conn, payg):
    world = payg("10", kinds=("data",))
    record = report(api, world.product, "sms", 7, api.clock.now())
    next_day(api, db_conn)
    assert api.get(record["href"]).json()["status"] == "rated"
    assert api.get(record["href"]).json()["usageProductPrice"] == []
    assert rates(db_conn) == [] and balance(db_conn, world.acc) == D("10")
    assert db_conn.execute("SELECT price_id, abr_id FROM usage_charge").fetchall() == [(None, None)]


def test_a_plan_line_ignores_usage_prices_its_usage_is_included(api, db_conn, build):
    world = build("30")
    bought = buy(api, db_conn, world)
    record = report(api, bought.product, "data", 5 * MIB, api.clock.now())
    before = balance(db_conn, world.acc)
    next_day(api, db_conn)
    assert api.get(record["href"]).json()["status"] == "notRated"
    assert rates(db_conn) == [] and balance(db_conn, world.acc) <= before
    assert db_conn.execute("SELECT count(*) FROM usage_charge").fetchone() == (0,)


def test_the_tax_engine_taxes_the_usage_charge_and_an_outage_leaves_it_unrated(api, db_conn, payg):
    world = payg("10")
    address = (
        '{"street1": "1 Main St", "city": "Los Angeles", "stateOrProvince": "CA",'
        ' "postCode": "00000", "country": "US"}'
    )
    db_conn.execute("UPDATE billing_account SET service_address = %s::jsonb", (address,))
    db_conn.commit()
    record = report(api, world.product, "data", 2 * MIB, api.clock.now())
    next_day(api, db_conn)  # ZIP 00000: the stub engine is down
    assert api.get(record["href"]).json()["status"] == "notRated"
    assert rates(db_conn) == [] and balance(db_conn, world.acc) == D("10")
    db_conn.execute(
        "UPDATE billing_account SET service_address = jsonb_set(service_address, '{postCode}',"
        " '\"90001\"')"
    )
    db_conn.commit()
    tick(db_conn, api.clock.now() + timedelta(minutes=5))
    assert api.get(record["href"]).json()["status"] == "rated"
    ((name, amount, tax, *_),) = rates(db_conn)
    assert (name, amount) == ("Data", D("0.20")) and tax > D("0.20") * D("0.03")
    assert db_conn.execute("SELECT count(*) FROM tax_transaction").fetchone() == (1,)
    lines = db_conn.execute(
        "SELECT applied_tax FROM applied_customer_billing_rate WHERE usage_command_id IS NOT NULL"
    ).fetchone()[0]
    assert {line["taxCategory"] for line in lines} >= {"sales_tax", "federal_usf"}


def test_a_balance_at_zero_sends_suspend_and_a_top_up_sends_restore(api, db_conn, payg):
    world = payg("1")
    assert commands_sent("suspend") == []  # a positive balance is served
    report(api, world.product, "data", 10 * MIB, api.clock.now())  # 1.00 + 0.20 tax
    next_day(api, db_conn)
    assert balance(db_conn, world.acc) == D("-0.20")
    assert len(commands_sent("suspend")) == 1 and commands_sent("restore") == []
    key = commands_sent("suspend")[0]
    request = network().received[key][1]
    assert request["serviceOrderItem"]["service"]["state"] == "suspended"
    state = api.get(f"{SUMMARY}?product.id={world.product['id']}").json()["payAsYouGo"]
    assert state["barred"] is True and state["barring"]["state"] == "confirmed"
    assert state["balance"]["value"] == -0.2
    top_up(api, db_conn, world, "5")
    tick(db_conn, api.clock.now() + timedelta(minutes=1))
    assert len(commands_sent("restore")) == 1 and len(commands_sent("suspend")) == 1
    state = api.get(f"{SUMMARY}?product.id={world.product['id']}").json()["payAsYouGo"]
    assert state["barred"] is False and state["barring"]["command"] == "restore"
    contract_free = api.get(f"{SUMMARY}?product.id={world.product['id']}")
    assert contract_free.status_code == 200
    # nothing more is sent while the state holds
    tick(db_conn, api.clock.now() + timedelta(minutes=2))
    assert len(commands_sent("restore")) == 1 and len(commands_sent("suspend")) == 1


def test_a_negative_balance_is_recovered_from_the_next_top_up(api, db_conn, payg):
    world = payg("1")
    report(api, world.product, "data", 10 * MIB, api.clock.now())
    next_day(api, db_conn)
    assert balance(db_conn, world.acc) == D("-0.20")
    ((state,),) = db_conn.execute(
        "SELECT state FROM customer_bill ORDER BY created_at DESC LIMIT 1"
    ).fetchall()
    assert state == "partiallyPaid"  # 1.00 of the 1.20 was paid from the balance
    # a top-up that does not clear the debt: still barred, no restore
    top_up(api, db_conn, world, "0.10")
    tick(db_conn, api.clock.now() + timedelta(minutes=1))
    assert balance(db_conn, world.acc) == D("-0.10") and commands_sent("restore") == []
    top_up(api, db_conn, world, "5")
    tick(db_conn, api.clock.now() + timedelta(minutes=2))
    # the debt of 0.20 was taken out of the top-ups: 1 + 0.10 + 5 - 1.20
    assert balance(db_conn, world.acc) == D("4.90")
    assert db_conn.execute(
        "SELECT state FROM customer_bill WHERE id IN"
        " (SELECT customer_bill_id FROM applied_customer_billing_rate"
        "  WHERE usage_command_id IS NOT NULL)"
    ).fetchall() == [("settled",)]
    assert len(commands_sent("restore")) == 1


def test_rating_the_same_record_again_never_charges_twice(api, db_conn, payg):
    world = payg("10")
    first = report(api, world.product, "data", 2 * MIB, api.clock.now(), record="same-1")
    next_day(api, db_conn)
    assert len(rates(db_conn)) == 1
    spent = balance(db_conn, world.acc)
    # the network resends it, the mediation posts it again, the worker runs again
    again = report(api, world.product, "data", 2 * MIB, api.clock.now(), record="same-1")
    assert again["id"] == first["id"] and again["status"] == "rated"
    for minutes in (1, 2):
        tick(db_conn, api.clock.now() + timedelta(minutes=minutes))
    assert len(rates(db_conn)) == 1 and balance(db_conn, world.acc) == spent
    # the guard is the database's: a second charge of the record is refused
    record = UUID(first["id"])
    with pytest.raises(psycopg.errors.UniqueViolation):
        usage_repo.insert_charge(
            db_conn,
            record_id=record,
            command_id=db_conn.execute(
                "SELECT id FROM prepaid_command WHERE kind = 'usage'"
            ).fetchone()[0],
            account_id=UUID(world.acc["id"]),
            product_id=UUID(world.product["id"]),
            price_id=None,
            abr_id=None,
            blocks=None,
            now=api.clock.now(),
        )
    db_conn.rollback()


def test_a_late_record_of_a_rated_day_is_charged_by_its_own_pass(api, db_conn, payg):
    world = payg("10")
    day = api.clock.now()
    report(api, world.product, "data", 1 * MIB, day)
    next_day(api, db_conn)
    report(api, world.product, "data", 1 * MIB, day + timedelta(hours=1))
    tick(db_conn, api.clock.now() + timedelta(minutes=1))
    charged = rates(db_conn)
    assert [(n, a) for n, a, *_ in charged] == [("Data", D("0.10")), ("Data", D("0.10"))]
    assert {d for _, _, _, d, _ in charged} == {day.date()}
    assert balance(db_conn, world.acc) == D("10") - D("0.24")
    assert db_conn.execute("SELECT count(DISTINCT command_id) FROM usage_charge").fetchone() == (2,)


def test_a_barring_the_network_refuses_is_recorded_and_not_repeated(api, db_conn, payg):
    world = payg("0.50")
    network().refuse_kinds.add("suspend")
    report(api, world.product, "data", 10 * MIB, api.clock.now())
    next_day(api, db_conn)
    for minutes in (1, 2, 3):
        tick(db_conn, api.clock.now() + timedelta(minutes=minutes))
    rows = db_conn.execute("SELECT kind, state, attempts FROM usage_bar").fetchall()
    assert rows == [("suspend", "refused", 1)]
    state = api.get(f"{SUMMARY}?product.id={world.product['id']}").json()["payAsYouGo"]
    assert state["barred"] is True and state["barring"]["state"] == "refused"


def test_a_temporary_network_error_retries_the_same_barring(api, db_conn, payg):
    world = payg("0.50")
    network().refuse_kinds.clear()
    original = network().send
    failures = []

    def flaky(key, command, request):
        if command == "suspend" and not failures:
            failures.append(key)
            from app.integrations.network import NetworkTemporary

            raise NetworkTemporary("down")
        return original(key, command, request)

    network().send = flaky
    try:
        report(api, world.product, "data", 10 * MIB, api.clock.now())
        next_day(api, db_conn)
        assert db_conn.execute("SELECT state, attempts FROM usage_bar").fetchall() == [
            ("pending", 1)
        ]
        tick(db_conn, api.clock.now() + timedelta(minutes=2))
    finally:
        del network().send
    assert db_conn.execute("SELECT state, attempts FROM usage_bar").fetchall() == [("confirmed", 2)]
    assert commands_sent("suspend") == [failures[0]]  # the same idempotency key


def test_the_summary_of_a_plan_line_has_no_pay_as_you_go_part(api, db_conn, build):
    world = build("30")
    bought = buy(api, db_conn, world)
    body = api.get(f"{SUMMARY}?product.id={bought.product['id']}").json()
    assert body["payAsYouGo"] is None


# --- the catalog ---


def test_a_usage_price_needs_a_unit_of_measure_and_belongs_on_a_pay_as_you_go_offering(api):
    base = {
        "@type": "ProductOfferingPrice",
        "name": "Data",
        "lifecycleStatus": "Active",
        "priceType": "usage",
        "price": {"unit": "USD", "value": 0.1},
        "validFor": {"startDateTime": timeline.iso(timeline.START)},
    }
    assert api.post(f"{CATALOG}/productOfferingPrice", base).status_code == 422
    for bad in (
        {"amount": 1, "units": "parsec"},
        {"amount": 0, "units": "MB"},
        {"amount": 0.0001, "units": "KB"},  # not a whole number of bytes
    ):
        response = api.post(f"{CATALOG}/productOfferingPrice", base | {"unitOfMeasure": bad})
        assert response.status_code == 422, bad
    ok = usage_price(api, "GB", 1.5, amount=2)
    assert ok["priceType"] == "usage" and ok["unitOfMeasure"] == {"amount": 2, "units": "GB"}
    contract.assert_valid("TMF620", "ProductOfferingPrice", ok)
    plan = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "A plan",
            "lifecycleStatus": "Active",
            "productOfferingPrice": [{"id": ok["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )
    assert plan.status_code == 422 and "usage price" in plan.text
    payg = offering(api, "PAYG", [usage_price(api, "MB", 0.1)], kind="payAsYouGo")
    # a pay-as-you-go offering cannot become a plan while it has usage prices
    moved = api.patch(payg["href"], {"productOfferingCharacteristic": []})
    assert moved.status_code == 422


def test_one_usage_price_per_kind_and_the_unit_and_type_are_fixed(api):
    data = usage_price(api, "MB", 0.1)
    voice = usage_price(api, "minute", 0.05)
    sms = usage_price(api, "message", 0.02)
    clash = usage_price(api, "GB", 1)  # a second data price valid over the same time
    spec = create(
        api,
        "productSpecification",
        {"@type": "ProductSpecification", "name": "S", "lifecycleStatus": "Active"},
    )
    body = {
        "@type": "ProductOffering",
        "name": "Usage only",
        "lifecycleStatus": "Active",
        "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
        "productOfferingCharacteristic": [
            {
                "@type": "CharacteristicSpecification",
                "name": "offeringKind",
                "valueType": "string",
                "characteristicValueSpecification": [
                    {
                        "@type": "StringCharacteristicValueSpecification",
                        "valueType": "string",
                        "isDefault": True,
                        "value": "payAsYouGo",
                    }
                ],
            }
        ],
    }
    refs = [{"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in (data, voice, sms)]
    created = api.post(f"{CATALOG}/productOffering", body | {"productOfferingPrice": refs})
    assert created.status_code == 201, created.text  # one per kind is fine
    again = api.patch(
        created.json()["href"],
        {"productOfferingPrice": [*refs, {"id": clash["id"], "@type": "ProductOfferingPriceRef"}]},
    )
    assert again.status_code == 409
    # the unit of a usage price and its type never change
    unit = api.patch(data["href"], {"unitOfMeasure": {"amount": 1, "units": "GB"}})
    assert unit.status_code == 409
    same = api.patch(data["href"], {"unitOfMeasure": {"amount": 1, "units": "MB"}})
    assert same.status_code == 200
    assert api.patch(data["href"], {"priceType": "recurring"}).status_code == 422
    recurring = create(
        api,
        "productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "R",
            "lifecycleStatus": "Active",
            "priceType": "oneTime",
            "price": {"unit": "USD", "value": 1},
        },
    )
    assert api.patch(recurring["href"], {"priceType": "usage"}).status_code == 422
