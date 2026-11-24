"""Builders other test modules share, from tests/billing/test_api_bill_settlement.py (#423)."""

from datetime import date
from decimal import Decimal

import pytest

from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, BILL, PAYMENT, RATE
from tests.builders.billing.billing_db import add, at, bill

D = Decimal

INTERNAL = "/internal/v1"


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300)


def account_with_bills(api, db_conn, offering, *days: date) -> dict:
    """An account with one 300.00 product from Dec 1 and the bills of `days` issued."""
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    for day in days:
        bill(db_conn, account, day)
    return account


def bills(api, account: dict) -> list[dict]:
    """The account's bills, oldest first (the list itself is newest first, #314)."""
    return decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))[::-1]


def pay(api, account: dict, value, day: date, **over):
    api.clock.set(at(day, hour=12))
    return api.post(PAYMENT, apidata.payment_body(account, value, **over))


def summary(api, account: dict) -> dict:
    return decimals(api.get(f"{INTERNAL}/billingAccount/{account['id']}/settlement"))


def check_invariant(api, account: dict) -> None:
    """outstanding - unallocatedCredit = -(balance + charges not billed yet), via the API."""
    s = summary(api, account)
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    unbilled = sum(
        (
            r["taxIncludedAmount"]["value"]
            for r in decimals(api.get(f"{RATE}?billingAccount.id={account['id']}&isBilled=false"))
        ),
        D("0"),
    )
    assert s["outstanding"]["value"] - s["unallocatedCredit"]["value"] == -(
        balance["amount"]["value"] + unbilled
    )
