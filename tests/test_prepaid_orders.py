"""TMF622 prepaid acceptance and billing-owner completion are atomic."""

import threading
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import psycopg
import pytest
from psycopg import sql

from app.billing import prepaid
from app.ordering.process import ItemRequest, OrderRequest, place_order
from app.repository import funding, payments
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests import apidata, timeline
from tests.apidata import ORDER
from tests.test_api_resource_inventory import number
from tests.test_events import stored, subscribe
from tests.test_number_orders import refs
from tests.test_prepaid_dispatch import dispatch
from tests.test_prepaid_posting import TZ
from tests.test_settlement_db import make_payment


def shop(api, conn, funds="24", *, amount=10, numbers=True):
    off = apidata.sellable_offering(
        api, value=amount, tax=[{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]
    )
    if numbers:
        response = api.patch(
            off["productSpecification"]["href"],
            {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
        )
        assert response.status_code == 200, response.text
    acc = apidata.account(api)
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],))
    if D(funds):
        make_payment(conn, dict(acc, currency="USD"), funds)
    conn.commit()
    api.clock.set(timeline.START + timedelta(hours=9))
    return off, acc


def purchase(api, off, acc, *, nested=False, numbers=True, status=201):
    items = []
    for i in range(2):
        item = apidata.add_item(off, str(i + 1))
        if numbers:
            item["product"] = {
                "@type": "ProductRefOrValue",
                "realizingResource": refs(number(api, value=f"7999123400{i}")["id"]),
            }
        items.append(item)
    if nested:
        items[0]["productOrderItem"] = [items.pop()]
    response = api.post(ORDER, apidata.order_body(acc, *items))
    assert response.status_code == status, response.text
    return response.json()


@pytest.mark.parametrize("nested", [False, True])
def test_acceptance_then_worker_completes_entire_tree_once(api, db_conn, nested):
    off, acc = shop(api, db_conn)
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")
    subscribe(api, "TMF637", "ProductCreateEvent", "ProductStateChangeEvent")
    order = purchase(api, off, acc, nested=nested)
    assert order["state"] == "inProgress" and "completionDate" not in order
    assert db_conn.execute("SELECT DISTINCT status FROM product").fetchall() == [("pendingActive",)]
    assert db_conn.execute("SELECT count(*) FROM number_assignment").fetchone() == (2,)
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D(0)
    cmd_id, request = db_conn.execute("SELECT id, request FROM prepaid_command").fetchone()
    assert request["order_id"] == order["id"]
    assert worker_db.acquire_ownership(db_conn)
    db_conn.commit()
    assert dispatch(db_conn, api.clock.now()) == [cmd_id]
    result = commands.get(db_conn, cmd_id)["result"]
    db_conn.commit()
    final = api.get(order["href"]).json()
    assert final["state"] == "completed" and "completionDate" in final
    assert db_conn.execute("SELECT DISTINCT state FROM product_order_item").fetchall() == [
        ("completed",)
    ]
    assert db_conn.execute("SELECT DISTINCT status FROM product").fetchall() == [("active",)]
    assert len(result["purchases"]) == 2
    assert len(stored(db_conn, "TMF622")) == 1
    events = stored(db_conn, "TMF637")
    assert [event["event"]["product"]["status"] for _, event in events].count("pendingActive") == 2
    assert [event["event"]["product"]["status"] for _, event in events].count("active") == 2
    assert (
        prepaid.post_interval(db_conn, cmd_id, now=api.clock.now() + timedelta(days=1), tz=TZ)
        == result
    )
    assert len(stored(db_conn, "TMF622")) == 1


def test_tax_inclusive_one_cent_short_has_no_hold_claim_product_or_command(api, db_conn):
    off, acc = shop(api, db_conn, funds="23.99")
    order = purchase(api, off, acc)
    assert order["state"] == "rejected"
    assert order["productOrderErrorMessage"][0]["code"] == "insufficientFunds"
    for table in ("funding_hold", "number_assignment", "product", "prepaid_command"):
        assert db_conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone() == (0,)
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D("23.99")


def test_free_multi_purchase_needs_no_payment(api, db_conn):
    off, acc = shop(api, db_conn, funds="0", amount=0, numbers=False)
    order = purchase(api, off, acc, numbers=False)
    assert order["state"] == "inProgress"
    cmd_id = db_conn.execute("SELECT id FROM prepaid_command").fetchone()[0]
    assert worker_db.acquire_ownership(db_conn)
    prepaid.post_interval(db_conn, cmd_id, now=api.clock.now(), tz=TZ)
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "completed"
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert db_conn.execute("SELECT count(*) FROM bill_allocation").fetchone() == (0,)


def test_acceptance_rollback_leaves_no_reservation_or_claim(api, db_conn, monkeypatch):
    off, acc = shop(api, db_conn)

    def crash(*args, **kwargs):
        raise RuntimeError("enqueue crash")

    monkeypatch.setattr(commands, "enqueue", crash)
    purchase(api, off, acc, status=500)
    for table in (
        "funding_hold",
        "number_assignment",
        "product",
        "prepaid_command",
        "product_order",
    ):
        assert db_conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone() == (0,)
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D(24)


def test_draft_submission_reserves_only_on_submit(api, db_conn):
    off, acc = shop(api, db_conn, funds="12", numbers=False)
    response = api.post(
        ORDER, apidata.order_body(acc, apidata.add_item(off), requestedInitialState="draft")
    )
    assert response.status_code == 201, response.text
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (0,)
    db_conn.commit()
    submitted = api.patch(response.json()["href"], {"state": "acknowledged"})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "inProgress"
    assert db_conn.execute("SELECT count(*) FROM prepaid_command").fetchone() == (1,)


def test_future_start_claims_without_funds_then_authorizes_after_shortfall(api, db_conn):
    off, acc = shop(api, db_conn, funds="0")
    item = apidata.add_item(off)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number(api)["id"])}
    due = api.clock.now() + timedelta(days=1)
    body = apidata.order_body(acc, item, requestedStartDate=due.isoformat())
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "inProgress"
    cmd_id = UUID(order["id"])
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM number_assignment").fetchone() == (1,)
    db_conn.commit()
    assert dispatch(db_conn, api.clock.now()) == []
    assert dispatch(db_conn, due) == [cmd_id]
    assert commands.needs_topup(db_conn, cmd_id)["required"] == D(12)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    make_payment(db_conn, dict(acc, currency="USD"), "12")
    db_conn.commit()
    assert dispatch(db_conn, due + timedelta(days=2)) == [cmd_id]
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "completed"
    assert db_conn.execute("SELECT lower(period) FROM funded_interval").fetchone() == (
        (due + timedelta(days=2)).astimezone(TZ).date(),
    )


@pytest.mark.parametrize("future", [False, True])
def test_cancel_before_first_grant_releases_funds_and_number(api, db_conn, future):
    from tests.test_api_order_cancel import CANCEL, cancel_body

    off, acc = shop(api, db_conn, funds="12")
    item = apidata.add_item(off)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number(api)["id"])}
    body = apidata.order_body(acc, item)
    if future:
        body["requestedStartDate"] = (api.clock.now() + timedelta(days=1)).isoformat()
    order = api.post(ORDER, body).json()
    assert order["state"] == "inProgress"
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert commands.get(db_conn, UUID(order["id"]))["result"] == {"outcome": "cancelled"}
    assert funding.load(db_conn, UUID(acc["id"])).spendable == D(12)
    assert db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE cancelled_at IS NULL"
    ).fetchone() == (0,)
    db_conn.commit()
    assert dispatch(db_conn, api.clock.now() + timedelta(days=2)) == []
    assert api.get(order["href"]).json()["state"] == "cancelled"


def test_cancel_after_first_grant_refused(api, db_conn):
    from tests.test_api_order_cancel import CANCEL, cancel_body

    off, acc = shop(api, db_conn, numbers=False)
    order = purchase(api, off, acc, numbers=False)
    dispatch(db_conn, api.clock.now())
    db_conn.commit()
    assert api.post(CANCEL, cancel_body(order)).status_code == 409


def test_pending_purchase_crosses_midnight_with_accepted_money(api, db_conn):
    off, acc = shop(api, db_conn, funds="24", numbers=False)
    order = purchase(api, off, acc, numbers=False)
    cmd_id = db_conn.execute("SELECT id FROM prepaid_command").fetchone()[0]
    db_conn.commit()
    later = api.clock.now() + timedelta(days=45)
    api.clock.set(later)
    assert dispatch(db_conn, later) == [cmd_id]
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "completed"
    grants = db_conn.execute(
        "SELECT lower(period), amount, tax_amount FROM funded_interval"
    ).fetchall()
    assert grants == [(later.date(), D(10), D(2))] * 2


def test_accepted_tax_exemption_is_used_and_retained_in_order_prices(api, db_conn):
    off, acc = shop(api, db_conn, funds="20", numbers=False)
    certificate = {
        "@type": "TaxExemptionCertificate",
        "certificateNumber": "prepaid-exemption",
        "taxDefinition": [{"@type": "TaxDefinition", "taxType": "VAT"}],
    }
    assert api.patch(acc["href"], {"taxExemption": [certificate]}).status_code == 200
    order = purchase(api, off, acc, numbers=False)
    assert order["state"] == "inProgress"
    assert (
        order["productOrderItem"][0]["itemTotalPrice"][0]["price"]["taxIncludedAmount"]["value"]
        == 10
    )
    assert api.patch(acc["href"], {"taxExemption": []}).status_code == 200
    cmd_id = db_conn.execute("SELECT id FROM prepaid_command").fetchone()[0]
    assert str(cmd_id) == order["id"]
    db_conn.commit()
    dispatch(db_conn, api.clock.now())
    db_conn.commit()
    final = api.get(order["href"]).json()
    assert final["state"] == "completed"
    assert final["orderTotalPrice"][0]["price"]["taxIncludedAmount"]["value"] == 20
    assert db_conn.execute("SELECT DISTINCT tax_amount FROM funded_interval").fetchall() == [
        (D(0),)
    ]


def test_concurrent_purchases_cannot_both_spend_the_same_funds(api, db_conn, migrated_db_url):
    off, acc = shop(api, db_conn, funds="12", numbers=False)
    request = OrderRequest(UUID(acc["id"]), (ItemRequest("1", "add", UUID(off["id"])),))
    results = {}
    errors = []

    def second_purchase():
        try:
            with psycopg.connect(migrated_db_url) as conn:
                results["second"] = place_order(
                    conn, request, now=api.clock.now(), today=api.clock.today()
                )
        except Exception as exc:
            errors.append(exc)

    with psycopg.connect(migrated_db_url) as first:
        with first.transaction():
            results["first"] = place_order(
                first, request, now=api.clock.now(), today=api.clock.today()
            )
            second = threading.Thread(target=second_purchase)
            second.start()
            second.join(timeout=1)
            assert second.is_alive(), "the second purchase must wait for the account lock"
        second.join(timeout=10)
        assert not second.is_alive()
    assert errors == []
    assert api.get(f"{ORDER}/{results['first']}").json()["state"] == "inProgress"
    assert api.get(f"{ORDER}/{results['second']}").json()["state"] == "rejected"
    assert db_conn.execute("SELECT count(*) FROM prepaid_command").fetchone() == (1,)


@pytest.mark.parametrize("stage", ["bill", "completion"])
def test_posting_failure_keeps_order_pending_and_hold_intact(api, db_conn, monkeypatch, stage):
    off, acc = shop(api, db_conn)
    order = purchase(api, off, acc)
    cmd_id = db_conn.execute("SELECT id FROM prepaid_command").fetchone()[0]
    assert worker_db.acquire_ownership(db_conn)
    module, attr = (
        (prepaid.presentation, "snapshot_bill") if stage == "bill" else (commands, "complete")
    )
    original = getattr(module, attr)

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected purchase crash")

    monkeypatch.setattr(module, attr, crash)
    with pytest.raises(RuntimeError, match="purchase crash"):
        prepaid.post_interval(db_conn, cmd_id, now=api.clock.now(), tz=TZ)
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "inProgress"
    assert db_conn.execute("SELECT DISTINCT status FROM product").fetchall() == [("pendingActive",)]
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert db_conn.execute(
        "SELECT count(*) FROM funding_hold WHERE released_at IS NULL"
    ).fetchone() == (1,)
    monkeypatch.setattr(module, attr, original)
    prepaid.post_interval(db_conn, cmd_id, now=api.clock.now(), tz=TZ)
    db_conn.commit()
    assert api.get(order["href"]).json()["state"] == "completed"


def test_future_start_selects_authorization_price_and_tax(api, db_conn):
    from psycopg.types.range import Range

    from app.billing import entitlement
    from app.repository import catalog

    off, acc = shop(api, db_conn, funds="0", numbers=False)
    due = api.clock.now() + timedelta(days=2)
    response = api.post(
        ORDER, apidata.order_body(acc, apidata.add_item(off), requestedStartDate=due.isoformat())
    )
    assert response.status_code == 201, response.text
    old = db_conn.execute(
        "SELECT id FROM product_offering_price WHERE product_offering_id = %s", (off["id"],)
    ).fetchone()[0]
    boundary = due - timedelta(days=1)
    db_conn.execute(
        "UPDATE product_offering_price SET valid_for = tstzrange(lower(valid_for), %s, '[)')"
        " WHERE id = %s",
        (boundary, old),
    )
    current = catalog.create_product_offering_price(
        db_conn,
        product_offering_id=UUID(off["id"]),
        price_type="recurring",
        amount=D(7),
        currency="USD",
        valid_for=Range(boundary, None, "[)"),
        recurring_period="monthly",
        details={"tax": [{"taxCategory": "VAT", "taxRate": 10}]},
    )
    db_conn.commit()
    dispatch(db_conn, due)
    product = prepaid.fetch_one(db_conn, "SELECT * FROM product")
    account = prepaid.fetch_one(db_conn, "SELECT * FROM billing_account")
    assert entitlement.view(db_conn, product, account, due.astimezone(TZ).date())["needs_topup"][
        "required"
    ] == D("7.70")
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (0,)
    # A credit received after the shortfall wakes the command on the same day.
    payments.insert_payment(
        db_conn,
        billing_account_id=UUID(acc["id"]),
        amount=D("7.70"),
        currency="USD",
        received_at=due + timedelta(minutes=1),
        payment_method="cash",
    )
    db_conn.commit()
    dispatch(db_conn, due + timedelta(minutes=2))
    grant = db_conn.execute(
        "SELECT amount, tax_amount, quote->>'price_id' FROM funded_interval"
    ).fetchone()
    assert grant == (D(7), D("0.70"), str(current["id"]))


@pytest.mark.parametrize("future", [False, True])
def test_cancel_failure_rolls_back_claim_product_and_hold(api, db_conn, monkeypatch, future):
    from tests.test_api_order_cancel import CANCEL, cancel_body

    off, acc = shop(api, db_conn, funds="12")
    item = apidata.add_item(off)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number(api)["id"])}
    body = apidata.order_body(acc, item)
    if future:
        body["requestedStartDate"] = (api.clock.now() + timedelta(days=1)).isoformat()
    order = api.post(ORDER, body).json()
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("cancel crash")

    monkeypatch.setattr(commands, "complete", crash)
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 500
    assert api.get(order["href"]).json()["state"] == "inProgress"
    assert commands.get(db_conn, UUID(order["id"]))["state"] == "pending"
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("pendingActive",)
    assert db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE cancelled_at IS NULL"
    ).fetchone() == (1,)
    assert funding.load(db_conn, UUID(acc["id"])).spendable == (D(12) if future else D(0))
