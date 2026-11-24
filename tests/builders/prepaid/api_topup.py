"""Builders other test modules share, from tests/prepaid/test_api_topup.py (#423)."""

from tests import apidata
from tests.apiclient import decimals
from tests.builders.prepaid.prepaid_orders import shop
from tests.factories import NOW, prepaid_bucket

BASE = "/tmf-api/prepayBalanceManagement/v4"

TOPUP = f"{BASE}/topupBalance"


class Acc(dict):
    """The account as the API answered it, with attribute access and its bucket id."""

    __getattr__ = dict.__getitem__


def prepaid(api, db_conn, funds="0"):
    """A prepaid account with its bucket (prepaid creation through the API waits for P5a)."""
    off, acc = shop(api, db_conn, funds)
    bucket = prepaid_bucket(db_conn, acc["id"], NOW)
    db_conn.commit()
    return off, Acc(acc, bucket=str(bucket["id"]))


def body(acc, value=50, **over):
    return {
        "@type": "TopupBalance",
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": acc.bucket},
        "partyAccount": {"id": acc.id, "@referredType": "BillingAccount"},
        **over,
    }


def post(api, acc, value=50, headers=None, **over):
    return api.post(TOPUP, body(acc, value, **over), headers=headers)


def remaining(api, acc):
    response = api.get(f"{BASE}/bucket/{acc.bucket}")
    assert response.status_code == 200, response.text
    return decimals(response)["remainingValue"]["amount"]


def current_balance(api, acc):
    (entry,) = decimals(api.get(f"{apidata.ACCOUNT}/{acc.id}"))["accountBalance"]
    return entry["amount"]["value"]
