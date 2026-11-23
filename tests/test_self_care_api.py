"""The self-care API for a signed-in customer (#376): reads scoped to the session's customer,
card top-ups, saved cards, auto top-up and add-on packs through the operator's services.
docs/decisions/self-care.md "The self-care API". Every response is checked against
spec/self-care/bss-self-care.oas.yaml by the CareClient."""

# ruff: noqa: F811
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.integrations.network import adapter as network
from tests import apidata
from tests.builders.prepaid_plans import (  # noqa: F401 - fixtures
    add_on_item,
    balance,
    build,
    charges,
    line,
    stubs,
)
from tests.builders.prepaid_renewal_producer import tick
from tests.builders.self_care import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
    care,
    customer_of,
    enabled,
    invite,
    log_sender,
    new_care,
    settle,
    sign_in,
    signed_in,
)
from tests.builders.settlement_db import make_payment

EMAIL = "ada@example.org"


def other_customer(api, care_other, db_conn, email="bob@example.org"):
    """Another customer with a prepaid account, signed in on `care_other`."""
    acc = apidata.account(api, name="Bob's")
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (acc["id"],)
    )
    db_conn.commit()
    signed_in(api, care_other, db_conn, acc, email)
    return acc


def audit(db_conn, action):
    rows = db_conn.execute(
        "SELECT operator, role, verification, outcome, reason, customer_id, product_id"
        " FROM account_audit WHERE action = %s ORDER BY seq",
        (action,),
    ).fetchall()
    db_conn.commit()
    return rows


def save_card(care, token="tok_visa", **over):
    response = care.post(BASE + "/paymentMethod", {"token": token, **over})
    assert response.status_code == 201, response.text
    return response.json()


def top_up(care, card, value=25, key=None):
    headers = {} if key is None else {"Idempotency-Key": key}
    return care.post(
        BASE + "/topUp",
        {"amount": {"unit": "USD", "value": value}, "paymentMethodId": card["id"]},
        headers=headers,
    )


# --- reads ---


def test_a_customer_reads_their_account_lines_usage_history_and_offerings(
    api, care, db_conn, build
):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    signed_in(api, care, db_conn, world.acc)

    (account,) = care.get(BASE + "/account").json()
    assert account["id"] == world.acc["id"] and account["ratingType"] == "prepaid"
    assert D(str(account["balance"]["spendable"]["value"])) == balance(db_conn, world.acc)
    assert account["autoTopUp"]["enabled"] is False

    (found,) = care.get(BASE + "/line").json()
    assert found["id"] == bought.product["id"] and found["plan"]["name"] == "Plan 10"
    assert found["status"] == "active" and found["paidThrough"] == bought.due.isoformat()
    assert found["renewalMode"] == "balance" and found["addOns"] == []

    usage = care.get(f"{BASE}/line/{found['id']}/usage")
    assert usage.status_code == 200, usage.text
    assert usage.json()["period"]["endDate"] == bought.due.isoformat()

    history = care.get(BASE + "/history").json()
    assert history and history[0]["balanceAfter"] == account["balance"]["spendable"]
    assert care.get(BASE + "/history?limit=1").headers["X-Result-Count"] == "1"

    (pack,) = care.get(BASE + "/offering?kind=addOn").json()
    assert pack["id"] == world.addon["id"]
    assert pack["firstCharge"]["amount"] == {"unit": "USD", "value": 4.8}
    plans = care.get(BASE + "/offering?kind=plan").json()
    assert [p["id"] for p in plans] == [world.plan["id"]]
    assert care.get(BASE + "/offering?kind=payAsYouGo").status_code == 400
    # a postpaid account of the customer has balances and quotes of another kind
    owner = api.get(f"{apidata.CUSTOMER}/{customer_of(db_conn, world.acc)}").json()
    postpaid = apidata.account(api, owner, name="Bills")
    accounts = {a["id"]: a for a in care.get(BASE + "/account").json()}
    assert accounts[postpaid["id"]]["balance"] is None
    assert accounts[postpaid["id"]]["autoTopUp"] is None
    sold = care.get(f"{BASE}/offering?kind=plan&billingAccountId={postpaid['id']}")
    assert sold.status_code == 409


def test_another_customers_ids_answer_404(api, care, db_conn, build):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    signed_in(api, care, db_conn, world.acc)
    card = save_card(care)
    command = top_up(care, card).json()

    theirs = new_care(api)
    other = other_customer(api, theirs, db_conn)
    assert [a["id"] for a in theirs.get(BASE + "/account").json()] == [other["id"]]
    assert theirs.get(BASE + "/line").json() == []
    assert theirs.get(BASE + "/paymentMethod").json() == []
    for path in (
        f"/line/{bought.product['id']}/usage",
        f"/history?billingAccountId={world.acc['id']}",
        f"/offering?kind=addOn&billingAccountId={world.acc['id']}",
        f"/topUp/{command['id']}",
    ):
        assert theirs.get(BASE + path).status_code == 404, path
    assert theirs.delete(f"{BASE}/paymentMethod/{card['id']}").status_code == 404
    assert top_up(theirs, card).status_code == 404
    bought_by_them = theirs.post(
        BASE + "/addOn", {"lineId": bought.product["id"], "offeringId": world.addon["id"]}
    )
    assert bought_by_them.status_code == 404
    auto = theirs.put(BASE + "/autoTopUp", {"enabled": False, "billingAccountId": world.acc["id"]})
    assert auto.status_code == 404
    # the owner still sees it all
    assert care.get(f"{BASE}/topUp/{command['id']}").status_code == 200


# --- cards and top-ups ---


def test_a_card_top_up_credits_the_balance_when_the_gateway_confirms(api, care, db_conn, build):
    world = build("0", fee=False)
    login = signed_in(api, care, db_conn, world.acc)
    card = save_card(care)
    assert card["brand"] == "visa" and card["lastFourDigits"] == "4242" and card["isDefault"]
    assert "token" not in card and care.get(BASE + "/paymentMethod").json() == [card]

    first = top_up(care, card, key="topup-1")
    assert first.status_code == 202, first.text
    assert first.json()["state"] == "pending"
    again = top_up(care, card, key="topup-1")  # a replayed key: the first answer, no charge
    assert again.status_code == 202 and again.json() == first.json()
    assert top_up(care, card, value=30, key="topup-1").status_code == 409
    assert balance(db_conn, world.acc) == 0

    settle(db_conn, api)
    done = care.get(f"{BASE}/topUp/{first.json()['id']}").json()
    assert done["state"] == "confirmed" and done["confirmedAt"]
    assert balance(db_conn, world.acc) == D("25")
    (count,) = db_conn.execute("SELECT count(*) FROM payment_command").fetchone()
    db_conn.commit()
    assert count == 1

    customer_id = customer_of(db_conn, world.acc)
    for action in ("care_card_saved", "care_top_up"):
        assert audit(db_conn, action) == [
            (f"customer:{login}", "customer", "session", "done", None, customer_id, None)
        ]


def test_a_declined_card_tops_up_nothing(api, care, db_conn, build):
    world = build("0", fee=False)
    signed_in(api, care, db_conn, world.acc)
    card = save_card(care, "tok_decline")
    command = top_up(care, card).json()
    settle(db_conn, api)
    done = care.get(f"{BASE}/topUp/{command['id']}").json()
    assert done["state"] == "declined" and "declined" in done["declineReason"]
    assert balance(db_conn, world.acc) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"token": "tok_visa", "cardNumber": "4242424242424242"},
        {"token": "tok_visa", "cvv": "123"},
        {"token": "4242 4242 4242 4242"},
        {"token": "tok_visa", "brand": "visa"},
    ],
)
def test_no_card_number_and_no_card_details_are_accepted(api, care, db_conn, build, body):
    world = build("0", fee=False)
    signed_in(api, care, db_conn, world.acc)
    response = care.post(BASE + "/paymentMethod", body)
    assert response.status_code == 400, response.text
    top = care.post(
        BASE + "/topUp",
        {"amount": {"unit": "USD", "value": 5}, "paymentMethodId": "x", "cardNumber": "4242"},
    )
    assert top.status_code == 400
    assert db_conn.execute("SELECT count(*) FROM payment_method").fetchone() == (0,)


def test_an_unknown_token_is_refused_and_a_card_can_be_removed(api, care, db_conn, build):
    world = build("0", fee=False)
    signed_in(api, care, db_conn, world.acc)
    refused = care.post(BASE + "/paymentMethod", {"token": "tok_made_up"})
    assert refused.status_code == 422 and refused.json()["code"] == "cardRejected"
    card = save_card(care, "tok_mastercard", name="Work card")
    assert card["name"] == "Work card" and card["brand"] == "mastercard"
    assert care.post(BASE + "/paymentMethod", {"token": "tok_mastercard"}).status_code == 409
    assert care.delete(f"{BASE}/paymentMethod/{card['id']}").status_code == 204
    assert care.get(BASE + "/paymentMethod").json() == []
    assert care.delete(f"{BASE}/paymentMethod/{card['id']}").status_code == 404
    assert len(audit(db_conn, "care_card_removed")) == 1


def test_auto_top_up_is_set_and_the_line_renews_from_the_card(api, care, db_conn, build):
    world = build("60", fee=False)
    line(api, db_conn, world)
    signed_in(api, care, db_conn, world.acc)
    card = save_card(care)
    wrong = care.put(
        BASE + "/autoTopUp",
        {
            "enabled": True,
            "threshold": {"unit": "USD", "value": 20},
            "amount": {"unit": "USD", "value": 10},
        },
    )
    assert wrong.status_code == 422
    rule = care.put(
        BASE + "/autoTopUp",
        {
            "enabled": True,
            "threshold": {"unit": "USD", "value": 10},
            "amount": {"unit": "USD", "value": 20},
        },
    )
    assert rule.status_code == 200, rule.text
    assert rule.json() == {
        "enabled": True,
        "threshold": {"unit": "USD", "value": 10},
        "amount": {"unit": "USD", "value": 20},
        "defaultPaymentMethod": {"id": card["id"]},
    }
    assert care.get(BASE + "/account").json()[0]["autoTopUp"] == rule.json()
    assert care.get(BASE + "/line").json()[0]["renewalMode"] == "balanceThenCard"
    off = care.put(BASE + "/autoTopUp", {"enabled": False})
    assert off.json()["enabled"] is False and off.json()["threshold"]["value"] == 10
    assert len(audit(db_conn, "care_auto_top_up")) == 2


def test_writes_are_limited_to_thirty_a_minute_per_session(api, care, db_conn, build):
    world = build("0", fee=False)
    signed_in(api, care, db_conn, world.acc)
    for _ in range(30):
        assert care.put(BASE + "/autoTopUp", {"enabled": False}).status_code == 200
    limited = care.put(BASE + "/autoTopUp", {"enabled": False})
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
    assert care.get(BASE + "/account").status_code == 200  # reads are not counted
    api.clock.set(api.clock.now() + timedelta(minutes=1, seconds=1))
    assert care.put(BASE + "/autoTopUp", {"enabled": False}).status_code == 200


# --- add-ons ---


def test_an_add_on_is_a_tmf622_order_with_its_fee_and_network_change(api, care, db_conn, build):
    world = build("60", fee=False)
    bought = line(api, db_conn, world)
    login = signed_in(api, care, db_conn, world.acc)
    before = balance(db_conn, world.acc)
    body = {"lineId": bought.product["id"], "offeringId": world.addon["id"]}
    placed = care.post(BASE + "/addOn", body, headers={"Idempotency-Key": "pack-1"})
    assert placed.status_code == 201, placed.text
    assert placed.json()["state"] == "inProgress"
    replay = care.post(BASE + "/addOn", body, headers={"Idempotency-Key": "pack-1"})
    assert replay.status_code == 201 and replay.json() == placed.json()
    assert balance(db_conn, world.acc) == before - D("4.80")  # held once

    tick(db_conn, api.clock.now())
    order = api.get(f"/tmf-api/productOrderingManagement/v5/productOrder/{placed.json()['id']}")
    assert order.json()["state"] == "completed", order.text
    ((kind, _),) = network().received.values()
    assert kind == "change_profile"
    assert [c[0] for c in charges(db_conn)].count("oneTime") == 1
    assert balance(db_conn, world.acc) == before - D("4.80")
    (found,) = care.get(BASE + "/line").json()
    assert [a["name"] for a in found["addOns"]] == ["Extra 2 GB"]
    assert audit(db_conn, "care_add_on") == [
        (
            f"customer:{login}",
            "customer",
            "session",
            "done",
            None,
            customer_of(db_conn, world.acc),
            UUID(bought.product["id"]),
        )
    ]


def test_a_refused_add_on_answers_its_tmf622_code(api, care, db_conn, build):
    world = build("12", fee=False)
    bought = line(api, db_conn, world)  # the plan takes the 12; nothing is left for a pack
    signed_in(api, care, db_conn, world.acc)
    body = {"lineId": bought.product["id"], "offeringId": world.addon["id"]}
    short = care.post(BASE + "/addOn", body, headers={"Idempotency-Key": "pack-1"})
    assert short.status_code == 422, short.text
    assert short.json()["code"] == "insufficientFunds"
    (refused,) = audit(db_conn, "care_add_on")
    assert refused[3:5] == ("refused", "insufficientFunds")
    # a refusal is not kept for the key: once the money is there, the same key buys it
    make_payment(db_conn, dict(world.acc, currency="USD"), "10")
    db_conn.commit()
    placed = care.post(BASE + "/addOn", body, headers={"Idempotency-Key": "pack-1"})
    assert placed.status_code == 201, placed.text
    # a plan is not an add-on: no such add-on offering
    plan = care.post(
        BASE + "/addOn", {"lineId": bought.product["id"], "offeringId": world.plan["id"]}
    )
    assert plan.status_code == 404
