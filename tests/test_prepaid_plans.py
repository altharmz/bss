"""Prepaid plans (issue #187): one-time fees, renewal from the balance or the card, add-on
packs, pay-as-you-go offerings and the offering kind in the catalog."""
# ruff: noqa: F811

from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.integrations.network import adapter as network
from app.integrations.payment import adapter as gateway
from tests import apidata, timeline
from tests.apidata import CATALOG
from tests.builders.network_provisioning import PROFILE
from tests.builders.payment_gateway import commands_of as card_commands
from tests.builders.payment_gateway import rule, save
from tests.builders.prepaid_plans import (  # noqa: F401 - fixtures
    ACCOUNTS,
    RATES,
    add_on_item,
    at,
    balance,
    build,
    buy,
    charges,
    kind_characteristic,
    line,
    offering,
    price,
    stubs,
)
from tests.builders.prepaid_renewal_producer import tick
from tests.builders.settlement_db import make_payment

pytestmark = pytest.mark.capability("worker.prepaidPlans")


def errors(order):
    return [e["code"] for e in order.get("productOrderErrorMessage", [])]


# --- plan purchase and one-time fees ---


def test_buying_a_plan_charges_price_taxes_and_fees_from_the_balance(api, db_conn, build):
    world = build("30")
    bought = buy(api, db_conn, world)
    # 10 + 20% = 12 for the month, 5 + 20% = 6 for the activation fee
    assert balance(db_conn, world.acc) == D("12")
    assert charges(db_conn) == [
        ("recurring", D("10.00"), D("2.00"), None),
        ("oneTime", D("5.00"), D("1.00"), "Activation fee"),
    ]
    ((bill_amount,),) = db_conn.execute("SELECT amount_due FROM customer_bill").fetchall()
    assert bill_amount == D("18.00")
    # the grant is the recurring charge alone
    assert db_conn.execute("SELECT amount, tax_amount FROM funded_interval").fetchone() == (
        D("10.00"),
        D("2.00"),
    )
    assert bought.due > timeline.TODAY


def test_a_plan_the_balance_cannot_pay_with_its_fee_is_rejected(api, db_conn, build):
    world = build("17.99")  # the month alone (12) would be paid; with the fee (18) it is not
    placed = apidata.order(api, world.acc, apidata.add_item(world.plan))
    assert placed["state"] == "rejected" and errors(placed) == ["insufficientFunds"]
    assert balance(db_conn, world.acc) == D("17.99")
    assert db_conn.execute("SELECT count(*) FROM prepaid_command").fetchone() == (0,)


def test_one_time_fees_are_charges_on_the_bill_the_ledger_and_tmf678(api, db_conn, build):
    world = build("30")
    bought = buy(api, db_conn, world)
    rates = api.get(f"{RATES}?product.id={bought.product['id']}").json()
    fee = next(r for r in rates if r["name"] == "Activation fee")
    assert fee["appliedBillingRateType"] == "appliedBillingCharge"
    assert fee["taxExcludedAmount"]["value"] == D("5.00")
    assert fee["taxIncludedAmount"]["value"] == D("6.00")
    assert fee["isBilled"] is True and fee["product"]["id"] == bought.product["id"]
    recurring = next(r for r in rates if r["name"] == "Plan 10")
    assert fee["bill"]["id"] == recurring["bill"]["id"]  # one bill for the purchase
    listed = api.get(f"{ACCOUNTS}/{world.acc['id']}/charges").json()
    assert {c["productOffering"]["name"] for c in listed if c["kind"] == "charge"} == {
        "Plan 10",
        "Activation fee",
    }
    ledger = api.get(f"{ACCOUNTS}/{world.acc['id']}/transactions").json()
    assert "Activation fee" in [t.get("note") or t.get("description") for t in ledger]


def test_a_fee_is_not_charged_again_at_the_renewal(api, db_conn, build):
    world = build("60")
    bought = buy(api, db_conn, world)
    api.clock.set(at(bought.due))
    tick(db_conn, api.clock.now())
    assert balance(db_conn, world.acc) == D("60") - D("18") - D("12")
    assert [c[0] for c in charges(db_conn)].count("oneTime") == 1
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)


def test_a_future_dated_purchase_pays_its_fee_when_it_starts(api, db_conn, build):
    world = build("30")
    day = timeline.START.date() + timedelta(days=3)
    placed = apidata.order(
        api, world.acc, apidata.add_item(world.plan), requestedStartDate=at(day).isoformat()
    )
    assert placed["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    assert charges(db_conn) == []
    tick(db_conn, at(day))
    assert api.get(placed["href"]).json()["state"] == "completed"
    assert [(c[0], c[3]) for c in charges(db_conn)] == [
        ("recurring", None),
        ("oneTime", "Activation fee"),
    ]
    assert balance(db_conn, world.acc) == D("12")


def test_a_plan_of_a_fixed_number_of_days(api, db_conn, build):
    world = build("30", fee=False, period=("daily", 30))
    bought = buy(api, db_conn, world)
    assert bought.due == timeline.START.date() + timedelta(days=30)


# --- renewal ---


def test_renewal_from_the_balance_at_period_end(api, db_conn, build):
    world = build("30", fee=False)
    bought = buy(api, db_conn, world)
    assert balance(db_conn, world.acc) == D("18")
    tick(db_conn, at(bought.due) - timedelta(days=1))
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    api.clock.set(at(bought.due))
    tick(db_conn, api.clock.now())
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert balance(db_conn, world.acc) == D("6")
    view = next(
        p
        for p in api.get(f"{ACCOUNTS}/{world.acc['id']}/products").json()
        if p["id"] == bought.product["id"]
    )
    assert view["status"] == "active" and view["nextChargeDate"] > bought.due.isoformat()
    assert view["fundingSource"] == "balance" and view["offeringKind"] == "plan"
    assert [h["kind"] for h in view["renewalHistory"]] == ["renewal", "purchase"]
    assert [h["fundedBy"] for h in view["renewalHistory"]] == ["balance", "balance"]
    assert card_commands(db_conn) == []


def test_renewal_from_the_card_when_the_balance_is_short_and_auto_pay_is_on(api, db_conn, build):
    world = build("13", fee=False)  # 12 for the month, 1 left
    save(api, world.acc)
    assert rule(api, world.acc, threshold=1, amount=50).status_code == 200
    bought = buy(api, db_conn, world)
    assert balance(db_conn, world.acc) == D("1")
    start = at(bought.due)
    tick(db_conn, start)  # the renewal is produced and found short by 11
    assert card_commands(db_conn) == []
    tick(db_conn, start + timedelta(minutes=1))  # the card is charged
    assert card_commands(db_conn) == [("renewal", "sent")]
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    api.clock.set(start + timedelta(minutes=2))
    tick(db_conn, api.clock.now())  # the gateway confirmed: payment recorded, renewal posted
    assert ("renewal", "confirmed") in card_commands(db_conn)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    shortfall = [c for c in gateway().charges.values() if c[1] != D("50")]
    assert [(c[1], c[2]) for c in shortfall] == [(D("11.00"), "USD")]  # exactly the shortfall
    view = next(
        p
        for p in api.get(f"{ACCOUNTS}/{world.acc['id']}/products").json()
        if p["id"] == bought.product["id"]
    )
    assert view["fundingSource"] == "balanceThenCard"
    assert view["renewalHistory"][0]["fundedBy"] == "card"
    tick(db_conn, start + timedelta(minutes=3))
    # the card paid the renewal once (the balance, now empty, also starts a normal auto top-up)
    assert [p for p, _ in card_commands(db_conn)].count("renewal") == 1


def test_a_declined_card_leaves_the_renewal_pending_once_per_day(api, db_conn, build):
    world = build("13", fee=False)
    save(api, world.acc, "tok_decline")
    rule(api, world.acc, threshold=1, amount=50)
    bought = buy(api, db_conn, world)
    api.clock.set(at(bought.due))
    for _ in range(5):
        tick(db_conn, api.clock.now())
    assert card_commands(db_conn) == [("renewal", "declined")]
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    api.clock.set(at(bought.due + timedelta(days=1)))
    for _ in range(5):
        tick(db_conn, api.clock.now())
    assert card_commands(db_conn) == [("renewal", "declined"), ("renewal", "declined")]


def test_renewal_failure_with_no_card_hands_over_to_the_lifecycle_state(api, db_conn, build):
    world = build("13", fee=False)
    rule(api, world.acc, threshold=1, amount=50)  # auto-pay on, but no card is saved
    bought = buy(api, db_conn, world)
    api.clock.set(at(bought.due))
    for _ in range(3):
        tick(db_conn, api.clock.now())
    assert card_commands(db_conn) == []
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    (state, cause) = db_conn.execute(
        "SELECT p.status, c.state FROM product p, prepaid_command c WHERE c.kind = 'renewal'"
    ).fetchone()
    assert (state, cause) == ("suspended", "pending")  # funding-suspended, the renewal waits
    view = next(
        p
        for p in api.get(f"{ACCOUNTS}/{world.acc['id']}/products").json()
        if p["id"] == bought.product["id"]
    )
    assert view["suspensionCause"] == "funding" and view["needs_topup"]["shortfall"] == 11
    # without auto-pay a saved card is not charged either
    save(api, world.acc)
    rule(api, world.acc, threshold=1, amount=50, enabled=False)
    tick(db_conn, api.clock.now() + timedelta(days=1))
    assert card_commands(db_conn) == []


# --- add-on packs ---


def test_an_add_on_charges_once_sends_the_increased_allowance_and_ends_with_the_period(
    api, db_conn, build
):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    before = balance(db_conn, world.acc)
    placed = apidata.order(api, world.acc, add_on_item(world, bought.product))
    assert placed["state"] == "inProgress"
    assert balance(db_conn, world.acc) == before - D("4.80")  # held: 4 + 20% tax
    tick(db_conn, api.clock.now())
    done = api.get(placed["href"]).json()
    assert done["state"] == "completed", done
    assert balance(db_conn, world.acc) == before - D("4.80")  # charged, once
    assert [c[0] for c in charges(db_conn)].count("oneTime") == 1
    ((kind, request),) = network().received.values()
    assert kind == "change_profile"
    assert request["profile"] == PROFILE | {
        "dataAllowanceMb": 7000,
        "dataCapMb": 7000,
        "international": 1,
    }
    addon = apidata.product_of(api, done)
    row = db_conn.execute(
        "SELECT parent_product_id, grantless, status, termination_date FROM product WHERE id = %s",
        (addon["id"],),
    ).fetchone()
    assert (str(row[0]), row[1], row[2], row[3]) == (
        bought.product["id"],
        True,
        "active",
        bought.due,
    )
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)  # no grant
    # the plan renews, the add-on ends with the period and is never renewed
    api.clock.set(at(bought.due))
    tick(db_conn, api.clock.now())
    assert db_conn.execute(
        "SELECT status FROM product WHERE id = %s", (addon["id"],)
    ).fetchone() == ("terminated",)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (2,)
    assert db_conn.execute(
        "SELECT count(*) FROM prepaid_command WHERE kind = 'renewal'"
    ).fetchone() == (1,)


def test_add_on_rules(api, db_conn, build):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    no_parent = apidata.order(api, world.acc, apidata.add_item(world.addon))
    assert errors(no_parent) == ["addOnParentRequired"]
    mixed = apidata.order(
        api, world.acc, add_on_item(world, bought.product), apidata.add_item(world.plan, "2")
    )
    assert errors(mixed) == ["mixedOfferingKinds"]
    other = apidata.account(api, name="Other")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (other["id"],)
    )
    db_conn.commit()
    foreign = apidata.order(api, other, add_on_item(world, bought.product))
    assert errors(foreign) == ["addOnParentUnknown"]
    future = apidata.order(
        api,
        world.acc,
        add_on_item(world, bought.product),
        requestedStartDate=at(timeline.TODAY + timedelta(days=3) + timedelta(days=1)).isoformat(),
    )
    assert "startNotToday" in errors(future)
    # the plan is no longer paid after its period: no add-on for it
    api.clock.set(at(bought.due + timedelta(days=1)))
    late = apidata.order(api, world.acc, add_on_item(world, bought.product))
    assert errors(late) == ["addOnParentNotActive"]


def test_an_add_on_the_balance_cannot_pay_is_rejected_and_a_refusal_releases_the_hold(
    api, db_conn, build
):
    world = build("12", fee=False)
    bought = line(api, db_conn, world)
    short = apidata.order(api, world.acc, add_on_item(world, bought.product))
    assert errors(short) == ["insufficientFunds"]
    make_payment(db_conn, dict(world.acc, currency="USD"), "10")
    db_conn.commit()
    network().refuse_kinds.add("change_profile")
    refused = apidata.order(api, world.acc, add_on_item(world, bought.product))
    assert refused["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    final = api.get(refused["href"]).json()
    assert final["state"] == "failed" and errors(final) == ["networkRefused"]
    assert balance(db_conn, world.acc) == D("10")  # the hold is released, nothing charged
    assert [c[0] for c in charges(db_conn)].count("oneTime") == 0


def test_terminating_the_plan_ends_its_add_ons(api, db_conn, build):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    placed = apidata.order(api, world.acc, add_on_item(world, bought.product))
    tick(db_conn, api.clock.now())
    addon = apidata.product_of(api, api.get(placed["href"]).json())
    apidata.order(api, world.acc, apidata.delete_item(bought.product["id"]))
    tick(db_conn, api.clock.now())
    statuses = dict(db_conn.execute("SELECT id::text, status FROM product").fetchall())
    assert statuses[addon["id"]] == "terminated" and statuses[bought.product["id"]] == "terminated"


# --- pay-as-you-go ---


def test_a_pay_as_you_go_offering_activates_charges_its_fee_and_is_never_renewed(
    api, db_conn, build
):
    world = build("30")
    placed = apidata.order(api, world.acc, apidata.add_item(world.payg))
    assert placed["state"] == "inProgress"
    assert balance(db_conn, world.acc) == D("30") - D("2.40")
    tick(db_conn, api.clock.now())
    done = api.get(placed["href"]).json()
    assert done["state"] == "completed", done
    product = apidata.product_of(api, done)
    assert db_conn.execute("SELECT status, grantless FROM product").fetchone() == ("active", True)
    assert charges(db_conn) == [("oneTime", D("2.00"), D("0.40"), "SIM activation")]
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    api.clock.set(at(timeline.TODAY + timedelta(days=90)))
    tick(db_conn, api.clock.now())
    assert db_conn.execute(
        "SELECT count(*) FROM prepaid_command WHERE kind = 'renewal'"
    ).fetchone() == (0,)
    view = next(
        p
        for p in api.get(f"{ACCOUNTS}/{world.acc['id']}/products").json()
        if p["id"] == product["id"]
    )
    assert view["status"] == "active" and view["offeringKind"] == "payAsYouGo"
    assert view["renewalHistory"] == []
    # suspension and termination work on it
    suspended = apidata.order(
        api,
        world.acc,
        {
            "@type": "ProductOrderItem",
            "id": "1",
            "action": "modify",
            "product": {"id": product["id"], "@type": "ProductRef", "status": "suspended"},
        },
    )
    assert suspended["state"] in {"inProgress", "completed"}, suspended
    tick(db_conn, api.clock.now())
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("suspended",)


# --- the catalog ---


def test_the_offering_kind_is_validated_and_only_a_plan_has_a_recurring_price(api):
    plan = price(api, "recurring", 10, "Monthly", "monthly", 1)
    bad = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Odd",
            "lifecycleStatus": "Active",
            "productOffering": None,
            "productOfferingCharacteristic": [kind_characteristic("bundle")],
        },
    )
    assert bad.status_code == 400
    created = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Pack",
            "lifecycleStatus": "Active",
            "productOfferingCharacteristic": [kind_characteristic("addOn")],
            "productOfferingPrice": [{"id": plan["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )
    assert created.status_code == 422
    plain = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Plain plan",
            "lifecycleStatus": "Active",
            "productOfferingPrice": [{"id": plan["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )
    assert plain.status_code == 201
    switched = api.patch(
        plain.json()["href"], {"productOfferingCharacteristic": [kind_characteristic("payAsYouGo")]}
    )
    assert switched.status_code == 422


# --- the quote before ordering (issue #212) ---


def quote(api, world, offering_):
    return api.get(
        f"{ACCOUNTS}/{world.acc['id']}/firstCharge"
        f"?productOffering.id={offering_['id']}&startDate={api.clock.now().date()}"
    )


def amount(money):
    return D(str(money["value"]))


def test_the_plan_quote_includes_its_fee_in_what_is_held(api, db_conn, build):
    world = build("30")
    response = quote(api, world, world.plan)
    assert response.status_code == 200, response.text
    body = response.json()
    # 10 + 20% for the month, 5 + 20% for the fee
    assert amount(body["required"]) == D("18") and amount(body["shortfall"]) == 0
    [fee] = body["fees"]
    assert fee["name"] == "Activation fee"
    assert [amount(fee[k]) for k in ("amount", "tax", "gross")] == [D("5"), D("1"), D("6")]
    assert amount(body["price"]) == D("10")


def test_an_add_on_quote_is_its_fees_alone(api, db_conn, build):
    world = build("3")
    body = quote(api, world, world.addon).json()
    assert amount(body["required"]) == D("4.8") and amount(body["shortfall"]) == D("1.8")
    assert [f["name"] for f in body["fees"]] == ["Extra 2 GB"]
    assert amount(body["price"]) == 0 and body["billableDays"] == 1


def test_a_non_plan_offering_without_a_fee_has_no_quote(api, db_conn, build):
    world = build("30")
    bare = offering(api, "Bare pack", [], kind="addOn")
    assert quote(api, world, bare).status_code == 422
