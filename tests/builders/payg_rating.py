"""Shared builders of tests/test_payg_rating.py, used by other test modules (#423)."""

from datetime import timedelta

import pytest

from app.integrations.network import adapter as network
from tests import apidata, timeline
from tests.builders.prepaid_plans import (
    VAT,
    at,
    create,
    offering,
)
from tests.builders.prepaid_renewal_producer import tick

USAGE = "/tmf-api/productUsageManagement/v5/productUsage"

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


def next_day(api, db_conn, days=1):
    api.clock.set(at(api.clock.today() + timedelta(days=days)))
    return tick(db_conn, api.clock.now())
