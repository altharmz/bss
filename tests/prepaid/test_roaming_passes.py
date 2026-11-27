"""International calling and roaming passes (issue #200): pass profiles, the end-of-pass
change_profile, usage zones and pay-as-you-go zone prices. docs/decisions/prepaid.md,
usage.md, network.md."""
# ruff: noqa: F811

from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest

from app.integrations.network import adapter as network
from app.network import usage as rules
from app.network.usage import Incoming, ingest, ingest_batch
from app.notify import events
from tests import apidata, timeline
from tests.apidata import CATALOG
from tests.builders.prepaid.payg_rating import usage_price
from tests.builders.prepaid.prepaid_plans import (  # noqa: F401 - fixtures
    ADDON_PROFILE,
    RATES,
    add_on_item,
    at,
    build,
    buy,
    create,
    kind_characteristic,
    offering,
    price,
    stubs,
)
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import PROFILE, characteristics

pytestmark = pytest.mark.capability("worker.roamingPasses")

SUMMARY = "/internal/v1/usage/summary"
USAGE = "/tmf-api/productUsageManagement/v5/productUsage"
MIB = 1024 * 1024
ZERO = {name: 0 for name in PROFILE} | {"throttleKbps": 128}
ROAMING = ZERO | {"roaming": 1, "roamingDataMb": 1000, "roamingMinutes": 60, "roamingSmsCount": 50}
WORLD = ZERO | {"international": 1, "internationalMinutes": 100}


def pass_offering(api, name, profile, days=None, fee=5):
    spec = create(
        api,
        "productSpecification",
        {
            "@type": "ProductSpecification",
            "name": name,
            "lifecycleStatus": "Active",
            "productSpecCharacteristic": characteristics(**profile),
        },
    )
    chars = [kind_characteristic("addOn")]
    if days is not None:
        chars.append(
            {
                "@type": "CharacteristicSpecification",
                "name": "passDays",
                "valueType": "integer",
                "characteristicValueSpecification": [
                    {
                        "@type": "IntegerCharacteristicValueSpecification",
                        "valueType": "integer",
                        "isDefault": True,
                        "value": days,
                    }
                ],
            }
        )
    return api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": name,
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": timeline.iso(max(timeline.START, api.clock.now()))},
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [
                {"id": price(api, "oneTime", fee, name)["id"], "@type": "ProductOfferingPriceRef"}
            ],
            "productOfferingCharacteristic": chars,
        },
    )


@pytest.fixture
def passes(api, db_conn, build):
    """A funded plan line (PROFILE) with a 7-day roaming pass, a world-calling pass that lasts
    the period, and the regular data pack of the build."""
    api.clock.set(timeline.START)  # the prices start then
    roaming = pass_offering(api, "Roam 7 days", ROAMING, days=7)
    world_pass = pass_offering(api, "World calling", WORLD)
    assert roaming.status_code == 201 and world_pass.status_code == 201, roaming.text
    made = build("200", fee=False)
    made.roaming, made.world = roaming.json(), world_pass.json()
    made.line = buy(api, db_conn, made)
    network().received.clear()
    return made


def buy_add_on(api, db_conn, world, offering_):
    placed = apidata.order(
        api, world.acc, add_on_item(world, world.line.product, offering_=offering_)
    )
    assert placed["state"] == "inProgress", placed
    tick(db_conn, api.clock.now())
    done = api.get(placed["href"]).json()
    assert done["state"] == "completed", done
    return apidata.product_of(api, done)


def sent(kind="change_profile"):
    return [request for command, request in network().received.values() if command == kind]


def termination(db_conn, product):
    row = db_conn.execute(
        "SELECT termination_date FROM product WHERE id = %s", (product["id"],)
    ).fetchone()
    db_conn.commit()
    return row[0]


# --- passes and the network ---


def test_a_7_day_pass_ends_with_one_change_profile_back_to_the_plan_and_the_live_add_ons(
    api, db_conn, passes
):
    pack = buy_add_on(api, db_conn, passes, passes.addon)  # lasts the period
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    roam = buy_add_on(api, db_conn, passes, passes.roaming)
    profiles = [request["profile"] for request in sent()]
    # the pass keeps the pack bought before it (plan + pack + pass)
    with_pack = PROFILE | {
        "dataAllowanceMb": 7000,
        "dataCapMb": 7000,
        "international": 1,
    }
    assert profiles[-1] == with_pack | {
        "roaming": 1,
        "roamingDataMb": 1000,
        "roamingMinutes": 60,
        "roamingSmsCount": 50,
    }
    start = timeline.START.date()
    assert termination(db_conn, roam) == start + timedelta(days=7)
    assert termination(db_conn, pack) == passes.line.due
    network().received.clear()
    tick(db_conn, at(start + timedelta(days=6)))
    assert sent() == []
    tick(db_conn, at(start + timedelta(days=7)))
    (revert,) = sent()
    assert revert["profile"] == with_pack  # the plan's profile has no roaming of its own
    # once only
    tick(db_conn, at(start + timedelta(days=8)))
    assert len(sent()) == 1
    purposes = db_conn.execute(
        "SELECT purpose, state FROM network_command WHERE purpose = 'passEnd'"
    ).fetchall()
    db_conn.commit()
    assert purposes == [("passEnd", "confirmed")]


def test_a_pass_bought_3_days_before_the_period_ends_ends_with_it_and_sends_no_revert(
    api, db_conn, passes
):
    late = passes.line.due - timedelta(days=3)
    api.clock.set(at(late))
    roam = buy_add_on(api, db_conn, passes, passes.roaming)
    assert termination(db_conn, roam) == passes.line.due
    network().received.clear()
    for day in range(1, 5):
        tick(db_conn, at(late + timedelta(days=day)))
    assert [r for r in sent() if r["profile"].get("roaming") == 0] == []
    assert db_conn.execute(
        "SELECT count(*) FROM network_command WHERE purpose = 'passEnd'"
    ).fetchone() == (0,)
    db_conn.commit()


def test_the_add_on_quote_and_the_products_view_give_the_pass_length_and_end(api, db_conn, passes):
    """Issue 383: firstCharge with parentProduct.id gives the end the order sets, and the
    products view gives each add-on's passDays (the Buy add-on dialog and the passes list)."""
    acc, plan = passes.acc, passes.line.product
    base = f"/internal/v1/billingAccount/{acc['id']}/firstCharge"
    start = api.clock.now().astimezone(timeline.TZ).date()

    def quote(offering_, parent=plan["id"]):
        query = f"?productOffering.id={offering_['id']}&startDate={start}"
        return api.get(base + query + ("" if parent is None else f"&parentProduct.id={parent}"))

    roam = quote(passes.roaming)
    assert roam.status_code == 200, roam.text
    assert roam.json()["addOn"] == {
        "parentProduct": {"id": plan["id"], "href": plan["href"]},
        "passDays": 7,
        "endDate": str(start + timedelta(days=7)),
    }
    world = quote(passes.world).json()["addOn"]
    assert world["passDays"] is None
    assert world["endDate"] == str(passes.line.due)  # the rest of the plan period
    assert "addOn" not in quote(passes.roaming, parent=None).json()
    assert quote(passes.plan).status_code == 422  # not an add-on offering
    assert quote(passes.roaming, parent=passes.roaming["id"]).status_code == 422

    bought = buy_add_on(api, db_conn, passes, passes.roaming)
    assert str(termination(db_conn, bought)) == roam.json()["addOn"]["endDate"]
    rows = api.get(f"/internal/v1/billingAccount/{acc['id']}/products").json()
    days = {p["id"]: p["passDays"] for p in rows}
    assert days[bought["id"]] == 7
    assert days[plan["id"]] is None


# --- usage zones ---


def test_roaming_and_international_usage_count_only_against_their_zone(api, db_conn, passes):
    buy_add_on(api, db_conn, passes, passes.roaming)
    # a minute later: the line's latest profile is the one confirmed and made last
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    buy_add_on(api, db_conn, passes, passes.world)
    product = passes.line.product
    when = api.clock.now()
    stored = [
        ingest(db_conn, record, now=when, tz=timeline.TZ)[0]
        for record in (
            Incoming("z-1", "data", 850 * MIB, when, product_id=product["id"], roaming=True),
            Incoming("z-2", "voice", 85 * 60, when, product_id=product["id"], international=True),
            Incoming("z-3", "sms", 5, when, product_id=product["id"], international=True),
            Incoming("z-4", "data", 100 * MIB, when, product_id=product["id"]),
        )
    ]
    db_conn.commit()
    assert [rules.zone(row) for row in stored] == [
        "roaming",
        "international",
        "international",
        "domestic",
    ]
    summary = api.get(f"{SUMMARY}?product.id={product['id']}").json()
    pools = {(a["zone"], a["kind"]): a for a in summary["allowances"]}
    assert pools[("roaming", "data")]["used"] == 850 * MIB
    assert pools[("roaming", "data")]["included"] == 1000 * MIB
    assert pools[("roaming", "data")]["thresholds"] == [80]
    assert pools[("domestic", "data")]["used"] == 100 * MIB  # the roaming 850 MB is not here
    assert pools[("domestic", "data")]["thresholds"] == []
    assert pools[("international", "voice")]["thresholds"] == [80]
    # international SMS has no allowance: counted, no threshold
    assert pools[("international", "sms")]["included"] == 0
    assert pools[("international", "sms")]["used"] == 5
    marks = db_conn.execute(
        "SELECT zone, allowance, threshold FROM usage_threshold ORDER BY 1, 2, 3"
    ).fetchall()
    db_conn.commit()
    assert marks == [("international", "voice", 80), ("roaming", "data", 80)]
    # the notifier's SQL key of a threshold is the event's key, zones included (#340)
    found = sorted(e.key for e in events._usage(db_conn, when - timedelta(days=1)))
    in_sql = db_conn.execute(
        "SELECT " + events._USAGE_KEY + " FROM usage_threshold t ORDER BY 1"  # noqa: S608
    ).fetchall()
    db_conn.commit()
    assert len(found) == 2 and found == [row[0] for row in in_sql]


def test_roaming_without_a_pass_is_stored_with_nothing_included(api, db_conn, passes):
    product = passes.line.product
    when = api.clock.now()
    ingest(
        db_conn,
        Incoming("n-1", "data", 5000 * MIB, when, product_id=product["id"], roaming=True),
        now=when,
        tz=timeline.TZ,
    )
    db_conn.commit()
    summary = api.get(f"{SUMMARY}?product.id={product['id']}").json()
    pools = {(a["zone"], a["kind"]): a for a in summary["allowances"]}
    assert pools[("roaming", "data")]["included"] == 0
    assert pools[("roaming", "data")]["percent"] is None
    assert db_conn.execute("SELECT count(*) FROM usage_threshold").fetchone() == (0,)
    db_conn.commit()


def test_ingest_and_ingest_batch_agree_on_zones(api, db_conn, passes, monkeypatch):
    buy_add_on(api, db_conn, passes, passes.roaming)
    product_msisdn = "12025550177"  # the line's number, for the feed's matching
    number_id = db_conn.execute(
        "INSERT INTO subscriber_number (value, name, created_at, updated_at)"
        " VALUES (%s, 'n', now(), now()) RETURNING id",
        (product_msisdn,),
    ).fetchone()[0]
    db_conn.execute(
        "INSERT INTO number_assignment (subscriber_number_id, product_id, assigned_by_item_id,"
        " effective_from, quarantine_days, created_at, updated_at)"
        " SELECT %s, id, created_by_item_id, start_date, 0, now(), now() FROM product"
        " WHERE id = %s",
        (number_id, passes.line.product["id"]),
    )
    db_conn.commit()
    when = api.clock.now()
    batch = [
        Incoming("q-1", "data", 700 * MIB, when, msisdn=product_msisdn, roaming=True),
        Incoming("q-2", "data", 300 * MIB, when, msisdn=product_msisdn, roaming=True),
        Incoming("q-3", "voice", 60, when, msisdn=product_msisdn, international=True),
        Incoming("q-4", "data", 4500 * MIB, when, msisdn=product_msisdn),
    ]

    def stored():
        rows = db_conn.execute(
            "SELECT network_record_id, roaming, international, status FROM usage_record"
            " WHERE network_record_id LIKE 'q-%' ORDER BY 1"
        ).fetchall()
        marks = db_conn.execute(
            "SELECT t.zone, t.allowance, t.threshold, u.network_record_id, t.used, t.included"
            " FROM usage_threshold t JOIN usage_record u ON u.id = t.usage_record_id"
            " ORDER BY 1, 2, 3"
        ).fetchall()
        return rows, marks

    with db_conn.transaction() as one_by_one:
        for record in batch:
            ingest(db_conn, record, now=when, tz=timeline.TZ)
        expected = stored()
        raise psycopg.Rollback(one_by_one)
    assert ingest_batch(db_conn, batch, now=when, tz=timeline.TZ) == 4
    assert stored() == expected
    assert [m[:4] for m in expected[1]] == [
        ("domestic", "data", 80, "q-4"),
        ("roaming", "data", 80, "q-2"),
        ("roaming", "data", 100, "q-2"),
    ]
    db_conn.rollback()


def test_tmf635_takes_the_international_characteristic(api, db_conn, passes):
    product = passes.line.product
    body = {
        "@type": "ProductUsage",
        "usageSpecification": {"id": "voice", "@type": "ProductUsageSpecificationRef"},
        "product": {"id": product["id"], "@type": "ProductRef", "@referredType": "Product"},
        "usageCharacteristic": [
            {
                "@type": "IntegerCharacteristic",
                "name": "quantity",
                "valueType": "integer",
                "value": 60,
            },
            {
                "@type": "StringCharacteristic",
                "name": "eventTime",
                "valueType": "string",
                "value": api.clock.now().isoformat(),
            },
            {
                "@type": "BooleanCharacteristic",
                "name": "international",
                "valueType": "boolean",
                "value": True,
            },
        ],
        "externalIdentifier": [{"@type": "ExternalIdentifier", "owner": "m", "id": "intl-1"}],
    }
    response = api.post(USAGE, body)
    assert response.status_code == 201, response.text
    traits = {c["name"]: c["value"] for c in response.json()["usageCharacteristic"]}
    assert traits["international"] is True
    bad = dict(body, externalIdentifier=[{"@type": "ExternalIdentifier", "owner": "m", "id": "x"}])
    bad["usageCharacteristic"] = body["usageCharacteristic"][:2] + [
        {
            "@type": "StringCharacteristic",
            "name": "international",
            "valueType": "string",
            "value": "yes",
        }
    ]
    assert api.post(USAGE, bad).status_code == 422


# --- pay-as-you-go zone prices ---


def zone_use(zone):
    return {
        "prodSpecCharValueUse": [
            {
                "@type": "ProductSpecCharacteristicValueUse",
                "name": "usageZone",
                "valueType": "string",
                "productSpecCharacteristicValue": [
                    {
                        "@type": "StringCharacteristicValueSpecification",
                        "valueType": "string",
                        "value": zone,
                    }
                ],
            }
        ]
    }


def test_payg_rates_each_zone_at_its_price_and_a_zone_without_price_free(api, db_conn, build):
    world = build("100")
    api.clock.set(timeline.START)
    data = usage_price(api, "MB", 0.10, name="Data")
    roaming_data = usage_price(api, "MB", 1.00, name="Roaming data", **zone_use("roaming"))
    voice = usage_price(api, "minute", 0.05, name="Voice")
    world.offering = offering(api, "Zones", [data, roaming_data, voice], kind="payAsYouGo")
    api.clock.set(timeline.START + timedelta(hours=9))
    placed = apidata.order(api, world.acc, apidata.add_item(world.offering))
    tick(db_conn, api.clock.now())
    product = apidata.product_of(api, api.get(placed["href"]).json())
    when = api.clock.now()
    for record in (
        Incoming("p-1", "data", 10 * MIB, when, product_id=product["id"]),
        Incoming("p-2", "data", 3 * MIB, when, product_id=product["id"], roaming=True),
        Incoming("p-3", "voice", 600, when, product_id=product["id"], international=True),
    ):
        ingest(db_conn, record, now=when, tz=timeline.TZ)
    db_conn.commit()
    tick(db_conn, when + timedelta(days=1))  # the day is closed: rated
    rows = db_conn.execute(
        "SELECT charge_name, amount FROM applied_customer_billing_rate"
        " WHERE usage_command_id IS NOT NULL ORDER BY charge_name"
    ).fetchall()
    db_conn.commit()
    # domestic data 10 x 0.10, roaming data 3 x 1.00; international voice has no price
    assert rows == [("Data", D("1.00")), ("Roaming data", D("3.00"))]
    statuses = db_conn.execute(
        "SELECT network_record_id, status FROM usage_record ORDER BY 1"
    ).fetchall()
    db_conn.commit()
    assert all(status == "rated" for _, status in statuses)
    rates = api.get(f"{RATES}?product.id={product['id']}").json()
    assert {r["name"] for r in rates} >= {"Data", "Roaming data"}


# --- catalog ---


def test_catalog_validation(api, db_conn, build):
    api.clock.set(timeline.START)
    # passDays only on an add-on, 1 to 30 days
    on_plan = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Plan with days",
            "lifecycleStatus": "Active",
            "productOfferingCharacteristic": [
                {
                    "@type": "CharacteristicSpecification",
                    "name": "passDays",
                    "valueType": "integer",
                    "characteristicValueSpecification": [
                        {
                            "@type": "IntegerCharacteristicValueSpecification",
                            "valueType": "integer",
                            "value": 7,
                        }
                    ],
                }
            ],
        },
    )
    assert on_plan.status_code in (400, 422)
    assert pass_offering(api, "Too long", ROAMING, days=31).status_code in (400, 422)
    assert pass_offering(api, "Zero", ROAMING, days=0).status_code in (400, 422)
    # the roaming characteristics follow the profile rules
    bad = api.post(
        f"{CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Bad flag",
            "lifecycleStatus": "Active",
            "productSpecCharacteristic": characteristics(**(ZERO | {"roaming": 2})),
        },
    )
    assert bad.status_code in (400, 422)
    # usageZone only on a usage price, and one known zone
    recurring = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 10},
            **zone_use("roaming"),
        },
    )
    assert recurring.status_code == 422
    unknown = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Moon data",
            "lifecycleStatus": "Active",
            "priceType": "usage",
            "price": {"unit": "USD", "value": 1},
            "unitOfMeasure": {"amount": 1, "units": "MB"},
            **zone_use("moon"),
        },
    )
    assert unknown.status_code == 422
    # two prices of one kind and currency in different zones coexist on one offering
    data = usage_price(api, "MB", 0.10, name="Data")
    roam = usage_price(api, "MB", 1.00, name="Roam", **zone_use("roaming"))
    made = offering(api, "Zones ok", [data, roam], kind="payAsYouGo")
    assert made["id"]
    # the zone of a usage price cannot change
    url = f"{CATALOG}/productOfferingPrice/{roam['id']}"
    assert api.patch(url, zone_use("international")).status_code == 409
    assert api.get(url).json()["prodSpecCharValueUse"][0]["name"] == "usageZone"


def test_the_add_on_profile_used_to_be_plan_plus_pack_only(api, db_conn, passes):
    """A second pack keeps the first (#200): the profile sent with the roaming pass carries
    the data pack bought before it."""
    buy_add_on(api, db_conn, passes, passes.addon)
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    buy_add_on(api, db_conn, passes, passes.world)
    last = max(sent(), key=lambda r: r["profile"].get("internationalMinutes", 0))["profile"]
    assert last["dataAllowanceMb"] == PROFILE["dataAllowanceMb"] + ADDON_PROFILE["dataAllowanceMb"]
    assert last["internationalMinutes"] == 100


def test_an_add_on_quote_needs_the_plans_paid_period(api, db_conn, passes):
    acc, plan = passes.acc, passes.line.product
    base = f"/internal/v1/billingAccount/{acc['id']}/firstCharge"
    after = passes.line.due  # the first day not paid for
    response = api.get(
        f"{base}?productOffering.id={passes.roaming['id']}&startDate={after}"
        f"&parentProduct.id={plan['id']}"
    )
    assert response.status_code == 422, response.text
    assert response.json()["message"] == (
        f"product {plan['id']} is not a plan with a paid period on {after}"
    )
