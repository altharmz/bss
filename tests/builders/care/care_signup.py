"""Shared signup fixtures for API and browser tests."""

import re
from datetime import timedelta

import pytest

from app.integrations.identity import adapter as identity
from app.integrations.notify import adapter as sender
from tests import timeline
from tests.builders.prepaid.prepaid_plans import offering, price
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import PROFILE


@pytest.fixture(autouse=True)
def identity_stub():
    identity().reset()
    yield identity()
    identity().reset()


@pytest.fixture
def shop(api, db_conn, stubs):
    """A plan of 10 a month (+20% tax) that needs a number and a SIM, eSIM stock and a pool."""
    plan = offering(api, "Plan 10", [price(api, "recurring", 10, "Monthly", "monthly", 1)],
                    kind="plan", profile=PROFILE)  # fmt: skip
    spec = api.get(plan["productSpecification"]["href"]).json()
    response = api.patch(
        spec["href"],
        {
            "resourceSpecification": [
                {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                {"id": "imsi", "@type": "ResourceSpecificationRef"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    api.clock.set(timeline.START + timedelta(hours=9))
    return plan


def stock(api, esims=2, numbers=2):
    if esims:
        assert (
            api.post("/internal/v1/sim/esim-generate", {"batch": "E1", "count": esims}).status_code
            == 201
        )
    if numbers:
        assert (
            api.post("/internal/v1/number/pool", {"areaCode": "212", "count": numbers}).status_code
            == 201
        )


def last_code():
    return re.search(
        r"code to confirm this email for your new line is (\d{6})\.", sender().sent[-1].body
    ).group(1)


def wake(api, db_conn, times=1):
    for _ in range(times):
        tick(db_conn, api.clock.now())
        api.clock.set(api.clock.now() + timedelta(minutes=1))
