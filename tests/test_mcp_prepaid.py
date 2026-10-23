"""P5b: the prepaid write tools take an explicit idempotency_key (the Idempotency-Key
header of the API), amounts are decimal strings, and a retry replays instead of repeating."""

from decimal import Decimal as D
from uuid import UUID

import pytest

from app.repository import funding
from bss_mcp.tools import IDEMPOTENCY_ARG
from tests import factories
from tests.test_idempotency import body
from tests.test_mcp_tools import generated
from tests.test_prepaid_orders import shop

KEYED = {
    "create_topup_balance",
    "create_adjust_balance",
    "create_reserve_balance",
    "create_transfer_balance",
    "create_order",
    "create_payment",
}


def test_exactly_the_journaled_posts_take_an_idempotency_key():
    tools = generated()
    keyed = {n for n, t in tools.items() if "idempotency_key" in t.input_schema["properties"]}
    assert keyed == KEYED
    for name in KEYED:
        assert tools[name].input_schema["properties"]["idempotency_key"] == IDEMPOTENCY_ARG
        assert "idempotency_key" not in tools[name].input_schema["required"]
        assert "idempotency_key" in tools[name].description


def test_a_retried_top_up_replays_and_a_changed_body_is_a_409(api, db_conn, mcp):
    off, acc = shop(api, db_conn, funds="0")
    bucket_id = str(factories.prepaid_bucket(db_conn, acc["id"], api.clock.now())["id"])
    db_conn.commit()
    token = mcp.token("writer", "write")

    def topup(amount, key):
        return mcp.result(
            token,
            "create_topup_balance",
            {
                "idempotency_key": key,
                "body": {
                    "usageType": "monetary",
                    "amount": {"amount": amount, "units": "USD"},
                    "bucket": {"id": bucket_id},
                    "partyAccount": {"id": acc["id"]},
                },
            },
        )

    _, first = topup("12.50", "mcp-topup-1")
    _, again = topup("12.50", "mcp-topup-1")
    assert first["status"] == again["status"] == 201
    assert again["body"] == first["body"]
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D("12.50")
    failed, other = topup("13.00", "mcp-topup-1")
    assert failed and other["status"] == 409
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D("12.50")


def test_a_retried_purchase_places_one_order(api, db_conn, mcp):
    off, acc = shop(api, db_conn)
    token = mcp.token("writer", "write")
    order = body(api, off, acc)
    results = [
        mcp.result(token, "create_order", {"idempotency_key": "mcp-buy-1", "body": order})
        for _ in range(2)
    ]
    (_, first), (_, again) = results
    assert first["status"] == again["status"] == 201, first
    assert again["body"] == first["body"]
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)


@pytest.mark.parametrize("key", ["", "has space", "x" * 256])
def test_a_malformed_key_is_refused_before_the_app(mcp, key):
    token = mcp.token("writer", "write")
    is_error, text = mcp.result(token, "create_order", {"idempotency_key": key, "body": {}})
    assert is_error and "idempotency_key" in text
