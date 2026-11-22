"""Multi-line prepaid discounts (issue #196): line-count tiers on a plan offering, applied at
every interval quote. docs/decisions/prepaid.md, "Multi-line discounts"."""
# ruff: noqa: F811

import base64
import json
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from tests import apidata, timeline
from tests.builders.prepaid_plans import (  # noqa: F401 - fixtures
    ACCOUNTS,
    RATES,
    at,
    balance,
    build,
    offering,
    price,
    stubs,
)
from tests.builders.prepaid_renewal_producer import tick
from tests.builders.prepaid_terminations import credits, terminate
from tests.builders.tax_engine import CA, set_address

pytestmark = pytest.mark.capability("worker.multiLineDiscounts")

LINES = {"units": "lines"}


def tier(api, lines, value, period="monthly", **over):
    body = {
        "@type": "ProductOfferingPrice",
        "name": f"{lines}+ lines",
        "lifecycleStatus": "Active",
        "priceType": "discount",
        "price": {"unit": "USD", "value": value},
        "validFor": {"startDateTime": timeline.iso(max(timeline.START, api.clock.now()))},
        "unitOfMeasure": {**LINES, "amount": lines},
        "recurringChargePeriodType": period,
        **over,
    }
    return api.post(f"{apidata.CATALOG}/productOfferingPrice", body)


def tiered_plan(api, tiers=((2, 5), (3, 10))):
    """Plan 30 a month (+20% VAT) with the tiers (lines, amount off)."""
    made = [tier(api, n, value) for n, value in tiers]
    assert all(r.status_code == 201 for r in made), [r.text for r in made]
    return offering(
        api,
        "Plan 30",
        [price(api, "recurring", 30, "Monthly", "monthly", 1), *[r.json() for r in made]],
    )


@pytest.fixture
def world(api, db_conn, build):
    plan = tiered_plan(api)  # before build moves the clock past the prices' start
    made = build("1000", fee=False)
    made.plan = plan
    return made


def buy_lines(api, db_conn, world, count):
    """`count` plan lines bought one order after another; their products."""
    out = []
    for _ in range(count):
        placed = apidata.order(api, world.acc, apidata.add_item(world.plan))
        assert placed["state"] == "inProgress", placed
        tick(db_conn, api.clock.now())
        done = api.get(placed["href"]).json()
        assert done["state"] == "completed", done
        out.append(apidata.product_of(api, done))
    return out


def recurring(db_conn, product_id=None, kind=None):
    """(amount, tax, discount) of each recurring ABR, oldest first."""
    db_conn.commit()
    rows = db_conn.execute(
        "SELECT a.amount, a.tax_amount, a.discount_amount, a.discount_price_id, f.command_kind"
        " FROM applied_customer_billing_rate a JOIN funded_interval f ON f.applied_rate_id = a.id"
        " WHERE a.charge_type = 'recurring' AND (%s::uuid IS NULL OR a.product_id = %s)"
        " ORDER BY a.applied_at, a.id",
        (product_id, product_id),
    ).fetchall()
    db_conn.commit()
    return [r[:3] for r in rows if kind is None or r[4] == kind]


NET = {1: D("30.00"), 2: D("25.00"), 3: D("20.00"), 4: D("20.00")}


# --- purchase and renewal ---


def test_each_purchase_counts_the_live_lines_and_itself(api, db_conn, world):
    buy_lines(api, db_conn, world, 4)
    # one clock instant for all four: compare in amount order
    assert sorted(recurring(db_conn), reverse=True) == [
        (D("30.00"), D("6.00"), D("0.00")),
        (D("25.00"), D("5.00"), D("5.00")),
        (D("20.00"), D("4.00"), D("10.00")),
        (D("20.00"), D("4.00"), D("10.00")),  # no tier for 4: the largest N <= 4 is 3
    ]
    # the grant is the net, so a termination credit refunds the net
    db_conn.commit()
    amounts = [
        r[0] for r in db_conn.execute("SELECT amount FROM funded_interval ORDER BY amount DESC")
    ]
    db_conn.commit()
    assert amounts == [D("30.00"), D("25.00"), D("20.00"), D("20.00")]
    assert balance(db_conn, world.acc) == D("1000") - D("36") - D("30") - D("24") - D("24")


@pytest.mark.parametrize("lines", [1, 2, 3, 4])
def test_renewals_charge_the_net_and_tax_of_the_line_count(api, db_conn, world, lines):
    buy_lines(api, db_conn, world, lines)
    due = db_conn.execute("SELECT max(upper(period)) FROM funded_interval").fetchone()[0]
    db_conn.commit()
    before = balance(db_conn, world.acc)
    api.clock.set(at(due))
    tick(db_conn, api.clock.now())
    renewals = recurring(db_conn, kind="renewal")
    net = NET[lines]
    assert renewals == [(net, net * D("0.2"), D("30.00") - net)] * lines
    assert balance(db_conn, world.acc) == before - lines * net * D("1.2")


def test_a_new_line_does_not_reprice_a_running_period(api, db_conn, world):
    (first,) = buy_lines(api, db_conn, world, 1)
    api.clock.set(timeline.START + timedelta(days=10, hours=9))
    (second,) = buy_lines(api, db_conn, world, 1)
    assert recurring(db_conn, first["id"]) == [(D("30.00"), D("6.00"), D("0.00"))]
    assert recurring(db_conn, second["id"]) == [(D("25.00"), D("5.00"), D("5.00"))]
    due = db_conn.execute(
        "SELECT upper(period) FROM funded_interval WHERE product_id = %s", (first["id"],)
    ).fetchone()[0]
    db_conn.commit()
    api.clock.set(at(due))
    tick(db_conn, api.clock.now())
    # its next period is discounted: two live lines on the renewal date
    assert recurring(db_conn, first["id"])[-1] == (D("25.00"), D("5.00"), D("5.00"))


def test_an_ended_line_stops_counting_at_the_next_renewal(api, db_conn, world):
    first, second = buy_lines(api, db_conn, world, 2)
    api.clock.set(timeline.START + timedelta(days=5, hours=10))
    terminate(api, world.acc, second)
    tick(db_conn, api.clock.now() + timedelta(minutes=1))
    # the credit refunds the second line's net (25 + 5), never the full price
    ((_, net, tax, start, end, *_),) = credits(db_conn)
    days, period = (end - start).days, (end - timeline.START.date()).days
    assert -net <= D("25.00") * days / period + D("0.01") and -net < D("25.00")
    due = db_conn.execute(
        "SELECT upper(period) FROM funded_interval WHERE product_id = %s", (first["id"],)
    ).fetchone()[0]
    db_conn.commit()
    api.clock.set(at(due))
    tick(db_conn, api.clock.now())
    assert recurring(db_conn, first["id"])[-1] == (D("30.00"), D("6.00"), D("0.00"))


def test_the_hold_equals_the_posted_charge(api, db_conn, world):
    buy_lines(api, db_conn, world, 1)
    placed = apidata.order(api, world.acc, apidata.add_item(world.plan))
    db_conn.commit()
    (held,) = db_conn.execute(
        "SELECT h.amount FROM funding_hold h JOIN funding_command_hold c ON c.hold_id = h.id"
        " WHERE c.command_id = %s",
        (UUID(placed["id"]),),
    ).fetchone()
    db_conn.commit()
    assert held == D("30.00")  # 25 + 20% VAT
    tick(db_conn, api.clock.now())
    (due,) = db_conn.execute(
        "SELECT amount_due FROM customer_bill ORDER BY bill_date DESC, created_at DESC LIMIT 1"
    ).fetchone()
    db_conn.commit()
    assert due == held


def test_one_order_of_two_lines_counts_both(api, db_conn, world):
    placed = apidata.order(
        api, world.acc, apidata.add_item(world.plan, "1"), apidata.add_item(world.plan, "2")
    )
    assert placed["state"] == "inProgress", placed
    tick(db_conn, api.clock.now())
    assert recurring(db_conn) == [(D("25.00"), D("5.00"), D("5.00"))] * 2


def test_the_tax_engine_taxes_the_net(api, db_conn, world):
    assert set_address(api, world.acc, CA).status_code == 200
    buy_lines(api, db_conn, world, 2)
    db_conn.commit()
    lines = db_conn.execute(
        "SELECT applied_tax FROM applied_customer_billing_rate WHERE charge_type = 'recurring'"
        " AND discount_amount > 0"
    ).fetchone()[0]
    db_conn.commit()
    taxes = {t["taxCategory"]: D(str(t["taxAmount"])) for t in lines}
    # 25.00 x 7.25% = 1.8125 -> 1.81, federal USF 3% of 25.00 = 0.75
    assert taxes["sales_tax"] == D("1.81") and taxes["federal_usf"] == D("0.75")


# --- what shows the discount ---


def test_bill_document_tmf678_and_first_charge_show_the_discount(api, db_conn, world):
    (first,) = buy_lines(api, db_conn, world, 1)
    start = timeline.START.date()
    preview = api.get(
        f"{ACCOUNTS}/{world.acc['id']}/firstCharge?productOffering.id={world.plan['id']}"
        f"&startDate={start}"
    ).json()
    # the preview counts the line being bought: 2 lines
    assert preview["discount"]["amount"]["value"] == D("5.00")
    assert preview["price"]["value"] == D("30.00") and preview["required"]["value"] == D("30.00")
    (second,) = buy_lines(api, db_conn, world, 1)
    (rate,) = api.get(f"{RATES}?product.id={second['id']}").json()
    assert rate["taxExcludedAmount"]["value"] == D("25.00")
    assert "multi-line discount −$5.00" in rate["description"]
    bill = api.get(f"/tmf-api/customerBillManagement/v5/customerBill/{rate['bill']['id']}")
    document = db_conn.execute(
        "SELECT bill_document FROM customer_bill WHERE id = %s", (rate["bill"]["id"],)
    ).fetchone()[0]
    db_conn.commit()
    assert bill.status_code == 200
    names = [c["name"] for c in json.loads(base64.b64decode(document))["charges"]]
    assert names == ["Plan 30 (multi-line discount −$5.00)"]
    # an offering-change preview of a live line does not count it twice
    same = api.get(
        f"{ACCOUNTS}/{world.acc['id']}/firstCharge?productOffering.id={world.plan['id']}"
        f"&startDate={start}&product.id={first['id']}"
    )
    if same.status_code == 200:
        assert same.json()["discount"]["amount"]["value"] == D("5.00")


def test_renewal_history_carries_the_discount(api, db_conn, world):
    """The card's renewal history (issue 380): each funded interval with its tier, null
    without one; `amount` is the net with tax."""
    first, second = buy_lines(api, db_conn, world, 2)
    views = {p["id"]: p for p in api.get(f"{ACCOUNTS}/{world.acc['id']}/products").json()}
    (alone,) = views[first["id"]]["renewalHistory"]
    assert alone["discount"] is None
    assert alone["amount"]["value"] == D("36.00")
    (paired,) = views[second["id"]]["renewalHistory"]
    tier_id = recurring_tier(db_conn, second["id"])
    assert paired["discount"] == {
        "amount": {"unit": "USD", "value": D("5.00")},
        "productOfferingPrice": {"id": str(tier_id)},
    }
    assert paired["amount"]["value"] == D("30.00")


def recurring_tier(db_conn, product_id):
    tier_id = db_conn.execute(
        "SELECT discount_price_id FROM applied_customer_billing_rate"
        " WHERE product_id = %s AND charge_type = 'recurring'",
        (product_id,),
    ).fetchone()[0]
    db_conn.commit()
    return tier_id


# --- catalog ---


def test_tier_validation(api, world):
    assert tier(api, 1, 5).status_code == 422
    assert tier(api, 2.5, 5).status_code == 422
    assert tier(api, 2, 5, recurringChargePeriodType=None).status_code in (400, 422)
    no_period = {k: v for k, v in tier_body(2, api).items() if k != "recurringChargePeriodType"}
    assert api.post(f"{apidata.CATALOG}/productOfferingPrice", no_period).status_code == 422
    # a discount without a unit stays a record, with no period
    plain = dict(tier_body(2, api))
    del plain["unitOfMeasure"], plain["recurringChargePeriodType"]
    assert api.post(f"{apidata.CATALOG}/productOfferingPrice", plain).status_code == 201


def tier_body(lines, api=None):
    start = timeline.START if api is None else max(timeline.START, api.clock.now())
    return {
        "@type": "ProductOfferingPrice",
        "name": "tier",
        "lifecycleStatus": "Active",
        "priceType": "discount",
        "price": {"unit": "USD", "value": 5},
        "validFor": {"startDateTime": timeline.iso(start)},
        "unitOfMeasure": {**LINES, "amount": lines},
        "recurringChargePeriodType": "monthly",
    }


def attach(api, offering_, *prices):
    refs = [{"id": p["id"], "@type": "ProductOfferingPriceRef"} for p in prices]
    current = [
        {"id": p["id"], "@type": "ProductOfferingPriceRef"}
        for p in offering_["productOfferingPrice"]
    ]
    return api.patch(
        f"{apidata.CATALOG}/productOffering/{offering_['id']}",
        {"productOfferingPrice": current + refs},
    )


def test_attach_rules(api, world):
    # a discount with another unit on a plan
    gb = dict(tier_body(2, api))
    gb["unitOfMeasure"] = {"amount": 2, "units": "GB"}
    del gb["recurringChargePeriodType"]
    gb_price = api.post(f"{apidata.CATALOG}/productOfferingPrice", gb)
    assert gb_price.status_code == 201, gb_price.text
    assert attach(api, world.plan, gb_price.json()).status_code == 422
    # overlapping tiers (the same N, same currency, overlapping validity)
    again = tier(api, 2, 7)
    assert attach(api, world.plan, again.json()).status_code == 409
    # a lines tier on an add-on
    assert attach(api, world.addon, tier(api, 2, 1).json()).status_code == 422
    # a tier whose period is not the plan's
    weekly = tier(api, 4, 1, period="weekly")
    assert attach(api, world.plan, weekly.json()).status_code == 422
    # another N is fine
    assert attach(api, world.plan, tier(api, 5, 12).json()).status_code == 200


def test_the_tier_unit_cannot_change(api, world):
    tier_id = next(
        p["id"]
        for p in world.plan["productOfferingPrice"]
        if api.get(f"{apidata.CATALOG}/productOfferingPrice/{p['id']}").json()["priceType"]
        == "discount"
    )
    url = f"{apidata.CATALOG}/productOfferingPrice/{tier_id}"
    assert api.patch(url, {"unitOfMeasure": {**LINES, "amount": 4}}).status_code == 409
    assert api.patch(url, {"priceType": "recurring"}).status_code == 422
    body = api.get(url).json()
    assert body["unitOfMeasure"] == {**LINES, "amount": 2}
    assert body["recurringChargePeriodType"] == "monthly"


def test_a_plain_discount_is_still_a_record(api, db_conn, build):
    plain = dict(tier_body(2))
    del plain["unitOfMeasure"], plain["recurringChargePeriodType"]
    record = api.post(f"{apidata.CATALOG}/productOfferingPrice", plain).json()
    plan = offering(api, "Plan 30", [price(api, "recurring", 30, "Monthly", "monthly", 1), record])
    made = build("1000", fee=False)
    made.plan = plan
    buy_lines(api, db_conn, made, 2)
    assert recurring(db_conn) == [(D("30.00"), D("6.00"), D("0.00"))] * 2
