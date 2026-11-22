"""Shared builders of tests/test_billing_completion.py, used by other test modules (#423)."""

import pytest

from app.worker import db as worker_db
from app.worker.process import bill_date
from tests.builders.billing_db import at
from tests.timeline import TZ

DEMAND = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"

FORMAT = "/tmf-api/accountManagement/v5/billFormat"

MEDIA = "/tmf-api/accountManagement/v5/billPresentationMedia"


@pytest.fixture
def worker(migrated_db_url):
    with worker_db.connect(migrated_db_url) as conn:
        assert worker_db.acquire_ownership(conn)
        yield conn


def run(worker, api, day):
    api.clock.set(at(day))
    bill_date(worker, day, clock=api.clock, tz=TZ, trigger="scheduled")


def demand(api, account):
    response = api.post(DEMAND, {"billingAccount": {"id": account["id"]}})
    assert response.status_code == 201, response.text
    return response.json()
