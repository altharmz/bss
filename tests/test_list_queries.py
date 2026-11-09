"""A list endpoint runs the same number of statements for a page of 1 or of 100 (#285):
the related rows of a page are read in one query each, never once per row."""

import pytest

from tests import apidata
from tests.querycount import statements


@pytest.fixture
def three_customers(api):
    for name in ("One", "Two", "Three"):
        customer = apidata.customer(api, name=name)
        apidata.account(api, customer, name=f"{name} main")
        apidata.account(api, customer, name=f"{name} spare")


def test_customer_list(api, monkeypatch, three_customers):
    one = statements(api, monkeypatch, f"{apidata.CUSTOMER}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.CUSTOMER}?limit=100")
    assert one == hundred


def test_billing_account_list(api, monkeypatch, three_customers):
    one = statements(api, monkeypatch, f"{apidata.ACCOUNT}?limit=1")
    hundred = statements(api, monkeypatch, f"{apidata.ACCOUNT}?limit=100")
    assert one == hundred
