"""Shared builders of tests/test_api_transfer.py, used by other test modules (#423)."""

from decimal import Decimal as D

from tests import apidata
from tests.apidata import ORDER
from tests.builders.api_resource_inventory import number
from tests.builders.api_topup import BASE, Acc, remaining
from tests.builders.number_orders import refs
from tests.builders.prepaid_orders import shop
from tests.builders.prepaid_renewal_producer import tick
from tests.builders.settlement_db import make_payment
from tests.factories import NOW, prepaid_bucket

TRANSFER = f"{BASE}/transferBalance"


def party(api, db_conn, off, acc, digits):
    """A prepaid account with its bucket and one bought number (`acc.number`)."""
    bucket = prepaid_bucket(db_conn, acc["id"], NOW)
    db_conn.commit()
    acc = Acc(acc, bucket=str(bucket["id"]))
    item = apidata.add_item(off, "1")
    acc.number = number(api, value=digits)["id"]
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(acc.number)}
    response = api.post(ORDER, apidata.order_body(acc, item))
    assert response.status_code == 201, response.text
    return acc


def pair(api, db_conn):
    """The sender (50 spendable after its purchase) and the receiver (8)."""
    off, first = shop(api, db_conn, "62")
    second = apidata.account(api)
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (second["id"],)
    )
    make_payment(db_conn, dict(second, currency="USD"), "20")
    db_conn.commit()
    tx = party(api, db_conn, off, first, "79991234000")
    rx = party(api, db_conn, off, second, "79991234001")
    tick(db_conn, api.clock.now())
    assert remaining(api, tx) == D(50) and remaining(api, rx) == D(8)
    return tx, rx


def body(tx, rx, value=10, **over):
    return {
        "@type": "TransferBalance",
        "reason": "Customer requested credit transfer",
        "channel": {"id": "admin-ui", "name": "Admin UI"},
        "logicalResource": [{"id": str(tx.number)}],
        "receiverLogicalResource": {"id": str(rx.number)},
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": tx.bucket},
        "receiverBucket": {"id": rx.bucket},
        "receiverBucketUsageType": "monetary",
        **over,
    }


def post(api, tx, rx, value=10, headers=None, **over):
    return api.post(TRANSFER, body(tx, rx, value, **over), headers=headers)


def reserved(api, acc):
    return api.get(f"{BASE}/bucket/{acc.bucket}").json()["reservedValue"]["amount"]
