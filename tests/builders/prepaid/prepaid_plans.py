"""Builders other test modules share, from tests/prepaid/test_prepaid_plans.py (#423)."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.integrations.network import adapter as network
from app.integrations.payment import adapter as gateway
from app.repository import funding
from app.worker import loop
from tests import apidata, timeline
from tests.apidata import CATALOG
from tests.builders.billing.settlement_db import make_payment
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import PROFILE, characteristics
from tests.factories import NOW, prepaid_bucket

VAT = [{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]

ACCOUNTS = "/internal/v1/billingAccount"

RATES = "/tmf-api/customerBillManagement/v5/appliedCustomerBillingRate"

ADDON_PROFILE = {
    "dataAllowanceMb": 2000,
    "dataCapMb": 2000,
    "throttleKbps": 128,
    "voiceMinutes": 0,
    "smsCount": 0,
    "hotspot": 0,
    "international": 1,
    "voicemail": 0,
}


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    network().reset()
    gateway().reset()
    yield
    network().reset()
    gateway().reset()


def at(day, hour=9):
    return datetime.combine(day, time(hour), timeline.TZ)


def create(api, path, body):
    response = api.post(f"{CATALOG}/{path}", body)
    assert response.status_code == 201, response.text
    return response.json()


def price(api, kind, value, name, period=None, length=None, tax=VAT):
    body = {
        "@type": "ProductOfferingPrice",
        "name": name,
        "lifecycleStatus": "Active",
        "priceType": kind,
        "price": {"unit": "USD", "value": value},
        "validFor": {"startDateTime": timeline.iso(timeline.START)},
        "tax": tax,
    }
    if period:
        body["recurringChargePeriodType"] = period
        body["recurringChargePeriodLength"] = length
    return create(api, "productOfferingPrice", body)


def kind_characteristic(kind):
    return {
        "@type": "CharacteristicSpecification",
        "name": "offeringKind",
        "valueType": "string",
        "characteristicValueSpecification": [
            {
                "@type": "StringCharacteristicValueSpecification",
                "valueType": "string",
                "isDefault": True,
                "value": kind,
            }
        ],
    }


def offering(api, name, prices, kind=None, profile=None):
    spec = create(
        api,
        "productSpecification",
        {
            "@type": "ProductSpecification",
            "name": name,
            "lifecycleStatus": "Active",
            **(
                {} if profile is None else {"productSpecCharacteristic": characteristics(**profile)}
            ),
        },
    )
    return create(
        api,
        "productOffering",
        {
            "@type": "ProductOffering",
            "name": name,
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": timeline.iso(timeline.START)},
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [
                {"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in prices
            ],
            **(
                {}
                if kind is None
                else {"productOfferingCharacteristic": [kind_characteristic(kind)]}
            ),
        },
    )


@pytest.fixture
def build(api, db_conn):
    """A prepaid account with `funds` and a plan of 10 (+20% tax) a month, an activation fee
    of 5 (+20% tax), an add-on pack of 4 (+20% tax) and a pay-as-you-go offering."""

    def make(funds="30", fee=True, period=("monthly", 1)):
        plan_prices = [price(api, "recurring", 10, "Monthly", *period)]
        if fee:
            plan_prices.append(price(api, "oneTime", 5, "Activation fee"))
        plan = offering(api, "Plan 10", plan_prices, profile=PROFILE)
        addon = offering(
            api,
            "Extra 2 GB",
            [price(api, "oneTime", 4, "Extra 2 GB")],
            kind="addOn",
            profile=ADDON_PROFILE,
        )
        payg = offering(
            api, "Pay as you go", [price(api, "oneTime", 2, "SIM activation")], kind="payAsYouGo"
        )
        acc = apidata.account(api)
        db_conn.execute(
            "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],)
        )
        prepaid_bucket(db_conn, acc["id"], NOW)
        if D(funds):
            make_payment(db_conn, dict(acc, currency="USD"), funds)
        db_conn.commit()
        api.clock.set(timeline.START + timedelta(hours=9))
        return SimpleNamespace(plan=plan, addon=addon, payg=payg, acc=acc)

    return make


def balance(db_conn, acc):
    spendable = funding.load(db_conn, UUID(acc["id"])).spendable
    db_conn.commit()
    return spendable


def buy(api, db_conn, world, offering_=None):
    placed = apidata.order(api, world.acc, apidata.add_item(offering_ or world.plan))
    assert placed["state"] == "inProgress", placed
    tick(db_conn, api.clock.now())
    done = api.get(placed["href"]).json()
    assert done["state"] == "completed", done
    row = db_conn.execute(
        "SELECT id, upper(period) FROM funded_interval ORDER BY authorized_at DESC LIMIT 1"
    ).fetchone()
    return SimpleNamespace(order=done, product=apidata.product_of(api, done), due=row[1])


def add_on_item(world, parent, item_id="1", offering_=None):
    return apidata.add_item(
        offering_ or world.addon,
        item_id,
        product={
            "@type": "ProductRefOrValue",
            "productRelationship": [
                {
                    "@type": "ProductRelationship",
                    "relationshipType": "addOn",
                    "id": parent["id"],
                }
            ],
        },
    )


def charges(db_conn):
    db_conn.commit()
    return db_conn.execute(
        "SELECT charge_type::text, amount, tax_amount, charge_name"
        " FROM applied_customer_billing_rate ORDER BY charge_type::text DESC, amount DESC"
    ).fetchall()


def line(api, db_conn, world):
    bought = buy(api, db_conn, world)
    network().received.clear()
    return bought
