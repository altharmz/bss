"""Slice 2: the transaction history of a prepaid account includes balance actions, so its
newest balanceAfter equals the ledger (accountBalance). Postpaid lists are unchanged."""

from decimal import Decimal as D
from uuid import UUID

from app.repository import billing
from tests import apidata
from tests.apiclient import decimals, total
from tests.builders.billing.settlement_db import make_payment
from tests.builders.prepaid.api_adjust import ADJUST
from tests.builders.prepaid.api_adjust import post as adjust
from tests.builders.prepaid.api_transfer import pair
from tests.builders.prepaid.api_transfer import post as transfer
from tests.builders.prepaid.prepaid_renewal_producer import tick

BASE = "/internal/v1/billingAccount"


def rows(api, account_id):
    response = api.get(f"{BASE}/{account_id}/transactions?limit=100")
    assert response.status_code == 200, response.text
    return decimals(response), int(total(response, api))


def test_every_balance_action_is_a_transaction_and_the_newest_balance_is_the_ledger(api, db_conn):
    tx, rx = pair(api, db_conn)
    adjust(api, tx, 5, reason="Goodwill")
    adjust(api, tx, -2)
    gone = adjust(api, tx, 3, reason="Typo").json()
    cancelled = api.patch(f"{ADJUST}/{gone['id']}", {"status": "cancelled"})
    assert cancelled.status_code == 200, cancelled.text
    moved = transfer(api, tx, rx, 10, transferCost={"unit": "USD", "value": 1}).json()
    tick(db_conn, api.clock.now())
    assert (
        api.get(f"/tmf-api/prepayBalanceManagement/v4/transferBalance/{moved['id']}").json()[
            "status"
        ]
        == "completed"
    )

    items, total = rows(api, tx.id)
    assert total == len(items)
    kinds = sorted(t["type"] for t in items)
    assert kinds == [
        "adjustment",
        "adjustment",
        "adjustment",
        "charge",  # the purchase that made the account's first spendable funds
        "charge",  # the transfer fee
        "payment",
        "reversal",
        "transferOut",
    ]
    notes = {t["type"]: t["note"] for t in items if t["type"] in ("reversal", "transferOut")}
    assert notes == {"reversal": "Cancelled adjustment", "transferOut": notes["transferOut"]}
    assert notes["transferOut"].startswith("To ")
    assert sorted(t["note"] for t in items if t["type"] == "adjustment") == [
        "Adjustment",
        "Goodwill",
        "Typo",
    ]
    out = next(t for t in items if t["type"] == "transferOut")
    assert out["amount"]["value"] == D(-10)
    assert out["href"].endswith(f"/balanceActionHistory/{moved['id']}")
    assert len({(t["type"], t["id"]) for t in items}) == len(items)
    assert items[0]["balanceAfter"]["value"] == billing.account_balance(db_conn, UUID(tx.id))

    items, total = rows(api, rx.id)
    assert total == len(items)
    (inbound,) = [t for t in items if t["type"] == "transferIn"]
    assert inbound["note"].startswith("From ") and inbound["amount"]["value"] == D(10)
    assert items[0]["balanceAfter"]["value"] == billing.account_balance(db_conn, UUID(rx.id))


def test_a_postpaid_account_lists_no_balance_actions(api, db_conn):
    account = apidata.account(api)
    make_payment(db_conn, dict(account, currency="USD"), "20")
    db_conn.commit()
    items, total = rows(api, account["id"])
    assert [t["type"] for t in items] == ["payment"] and total == 1
