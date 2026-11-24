"""Builders other test modules share, from tests/orders/test_api_product_writes.py (#423)."""

from datetime import date

import pytest

from tests import apidata
from tests.builders.billing.billing_db import at
from tests.timeline import midnight, z

OCT20, NOV1 = date(2026, 10, 20), date(2026, 11, 1)


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    api.clock.set(at(OCT20))
    return offering, account


def product_body(account, offering, **over):
    return {
        "@type": "Product",
        "billingAccount": {"id": account["id"]},
        "productOffering": {"id": offering["id"]},
        **over,
    }


def orders_of(api, account) -> list[dict]:
    return api.get(f"{apidata.ORDER}?billingAccount.id={account['id']}").json()


def timeline_z(day: date) -> str:
    return z(midnight(day))
