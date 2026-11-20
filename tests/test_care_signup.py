"""Online signup (#377): email code, identity check, first payment, eSIM activation.
docs/decisions/self-care.md "Online signup". Every response is checked against the self-care
and internal specs by the clients."""

# ruff: noqa: F811
import re
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.config import settings
from app.integrations.identity import adapter as identity
from app.integrations.network import adapter as network
from app.integrations.notify import adapter as sender
from tests import timeline
from tests.test_prepaid_plans import (  # noqa: F401 - fixtures
    PROFILE,
    balance,
    offering,
    price,
    stubs,
)
from tests.test_prepaid_renewal_producer import tick
from tests.test_self_care import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
    care,
    enabled,
    log_sender,
    new_care,
    sign_in,
)

SIGNUP = BASE + "/signup"
INTERNAL = "/internal/v1/careSignup"
EMAIL = "new.customer@example.org"
ADDRESS = {"street1": "1 Main St", "city": "Albany", "stateOrProvince": "NY", "postCode": "12207"}
PERSON = {"givenName": "Grace", "familyName": "Hopper", "birthDate": "1990-12-09"}


@pytest.fixture(autouse=True)
def identity_stub():
    identity().reset()
    yield identity()
    identity().reset()


@pytest.fixture
def shop(api, db_conn, stubs):
    """A plan of 10 a month (+20% tax) that needs a number and a SIM, eSIM stock and a pool."""
    plan = offering(api, "Plan 10", [price(api, "recurring", 10, "Monthly", "monthly", 1)],
                    kind="plan", profile=PROFILE)  # fmt: skip
    spec = api.get(plan["productSpecification"]["href"]).json()
    response = api.patch(
        spec["href"],
        {
            "resourceSpecification": [
                {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                {"id": "imsi", "@type": "ResourceSpecificationRef"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    api.clock.set(timeline.START + timedelta(hours=9))
    return plan


def stock(api, esims=2, numbers=2):
    if esims:
        assert (
            api.post("/internal/v1/sim/esim-generate", {"batch": "E1", "count": esims}).status_code
            == 201
        )
    if numbers:
        assert (
            api.post("/internal/v1/number/pool", {"areaCode": "212", "count": numbers}).status_code
            == 201
        )


def last_code():
    return re.search(
        r"code to confirm this email for your new line is (\d{6})\.", sender().sent[-1].body
    ).group(1)


def start(care, plan, email=EMAIL):
    response = care.post(SIGNUP, {"email": email, "offeringId": plan["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def verified(care, plan, email=EMAIL):
    signup = start(care, plan, email)
    response = care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": last_code()})
    assert response.status_code == 200, response.text
    return response.json()


def details(care, signup, **over):
    return care.put(
        f"{SIGNUP}/{signup['id']}/details", {**PERSON, "serviceAddress": ADDRESS, **over}
    )


def wake(api, db_conn, times=1):
    for _ in range(times):
        tick(db_conn, api.clock.now())
        api.clock.set(api.clock.now() + timedelta(minutes=1))


def state(care, signup):
    response = care.get(f"{SIGNUP}/{signup['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def passed(api, care, db_conn, plan, email=EMAIL, **person):
    signup = verified(care, plan, email)
    assert details(care, signup, **person).status_code == 200
    wake(api, db_conn, 2)  # sent, then the verdict
    return state(care, signup)


def pay(care, signup, token="tok_visa"):
    return care.post(f"{SIGNUP}/{signup['id']}/payment", {"token": token, "password": PASSWORD})


def count(db_conn, table):
    (n,) = db_conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 - test tables
    db_conn.commit()
    return n


# --- the happy path ---


@pytest.mark.capability("worker.careSignup")
def test_a_new_customer_goes_from_email_to_an_active_esim_line(api, care, db_conn, shop):
    stock(api)
    signup = start(care, shop)
    assert signup["state"] == "started" and signup["emailVerified"] is False
    assert signup["offering"] == {"id": shop["id"], "name": "Plan 10"}
    logged = db_conn.execute(
        "SELECT content, customer_id FROM notification_message"
        " WHERE event_type = 'care_signup_code'"
    ).fetchone()
    db_conn.commit()
    assert "******" in logged[0] and last_code() not in logged[0] and logged[1] is None

    body = care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": last_code()}).json()
    assert body["emailVerified"] is True and body["state"] == "started"
    pending = details(care, signup).json()
    assert pending["state"] == "identityPending" and pending["identity"]["state"] == "pending"
    assert pending["serviceAddress"] == {**ADDRESS, "country": "US"}
    wake(api, db_conn)
    assert state(care, signup)["identity"]["state"] == "sent"
    wake(api, db_conn)
    ok = state(care, signup)
    assert ok["state"] == "identityPassed" and ok["identity"] == {"state": "passed", "reason": None}

    paying = pay(care, signup)
    assert paying.status_code == 202, paying.text
    paying = paying.json()
    assert paying["state"] == "identityPassed" and paying["payment"]["state"] == "pending"
    amount = D(str(paying["payment"]["amount"]["value"]))
    assert amount > 0 and paying["customer"] and paying["billingAccount"]
    account_id = paying["billingAccount"]["id"]
    account = db_conn.execute(
        "SELECT rating_type, service_address, state FROM billing_account WHERE id = %s",
        (account_id,),
    ).fetchone()
    db_conn.commit()
    assert account == ("prepaid", {**ADDRESS, "country": "US"}, "Active")

    wake(api, db_conn)  # the charge goes to the gateway
    wake(api, db_conn)  # confirmed: paid, and the order is placed
    activating = state(care, signup)
    assert activating["state"] == "activating" and activating["payment"]["state"] == "confirmed"
    assert activating["order"]["state"] == "inProgress"
    wake(api, db_conn)  # the network confirms the activation
    first = state(care, signup)
    assert first["state"] == "active" and first["order"]["state"] == "completed"
    assert first["line"]["number"].startswith("1212")
    assert first["esim"]["activationCode"].startswith("LPA:1$")
    assert first["esim"]["qrPayload"] == first["esim"]["activationCode"]
    assert state(care, signup)["esim"] is None  # once
    (mail,) = [m for m in sender().sent if m.subject == "Your eSIM is ready"]
    assert mail.to == EMAIL and first["esim"]["activationCode"] in mail.body
    logged = db_conn.execute(
        "SELECT content FROM notification_message WHERE event_type = 'care_esim_ready'"
    ).fetchone()
    db_conn.commit()
    assert first["esim"]["activationCode"] not in logged[0]
    ((kind, request),) = [v for v in network().received.values() if v[0] == "activate"]
    assert request["simType"] == "esim"
    # the top-up paid exactly the first charge, which the order charged
    assert balance(db_conn, {"id": account_id}) == 0
    # the customer signs in with the password and sees the line
    customer = new_care(api)
    assert sign_in(customer, EMAIL).status_code == 200
    (line,) = customer.get(BASE + "/line").json()
    assert line["id"] == first["line"]["id"] and line["status"] == "active"
    # the operator's list
    (row,) = api.get(f"{INTERNAL}?state=active").json()
    assert row["id"] == signup["id"] and row["esim"] is None and row["activationShown"] is True
    assert amount == D(str(row["payment"]["amount"]["value"]))


def test_the_worker_running_again_creates_nothing_twice(api, care, db_conn, shop):
    stock(api)
    signup = passed(api, care, db_conn, shop)
    assert pay(care, signup).status_code == 202
    assert pay(care, signup).status_code == 202  # a repeat while the charge is in flight
    wake(api, db_conn, 6)
    assert state(care, signup)["state"] == "active"
    assert count(db_conn, "payment_command") == 1
    assert count(db_conn, "product_order") == 1
    assert count(db_conn, "identity_check") == 1
    assert count(db_conn, "customer") == 1 and count(db_conn, "customer_login") == 1
    assert len(identity().checks) == 1
    assert pay(care, signup).status_code == 202  # an active signup answers with its state


# --- the steps' rules ---


def test_codes_details_and_replays(api, care, db_conn, shop):
    signup = start(care, shop)
    wrong = care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": "000000"})
    assert wrong.status_code == 401 and wrong.json()["code"] == "codeInvalid"
    early = details(care, signup)
    assert early.status_code == 409 and early.json()["code"] == "emailNotVerified"
    code = last_code()
    assert care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": code}).status_code == 200
    assert care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": code}).status_code == 200
    assert details(care, signup).status_code == 200
    assert details(care, signup).status_code == 200  # the same details again
    other = details(care, signup, givenName="Ada")
    assert other.status_code == 409 and other.json()["code"] == "signupState"
    assert count(db_conn, "identity_check") == 1
    assert details(care, signup, birthDate="2999-01-01").status_code == 422
    bad = details(care, signup, serviceAddress={**ADDRESS, "stateOrProvince": "XX"})
    assert bad.status_code == 422 and "stateOrProvince" in bad.json()["message"]
    early_pay = pay(care, signup)
    assert early_pay.status_code == 409 and early_pay.json()["code"] == "signupState"
    unknown = care.post(SIGNUP, {"email": "x@example.org", "offeringId": signup["id"]})
    assert unknown.status_code == 422 and unknown.json()["code"] == "offeringUnavailable"
    assert care.get(f"{SIGNUP}/{signup['id'][:-1]}0").status_code == 404
    assert care.post(SIGNUP, {"email": EMAIL, "offeringId": shop["id"], "x": 1}).status_code == 400


def test_five_wrong_codes_spend_the_code(api, care, shop):
    signup = start(care, shop)
    code = last_code()
    for _ in range(5):
        care.post(
            f"{SIGNUP}/{signup['id']}/verify", {"code": "999999" if code != "999999" else "000000"}
        )
    assert care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": code}).status_code == 401


def test_an_email_with_a_login_cannot_sign_up_again(api, care, db_conn, shop):
    stock(api)
    signup = passed(api, care, db_conn, shop)
    pay(care, signup)
    again = start(care, shop)
    taken = care.post(f"{SIGNUP}/{again['id']}/verify", {"code": last_code()})
    assert taken.status_code == 409 and taken.json()["code"] == "emailInUse"


def test_signups_need_the_feature_and_the_csrf_header(api, care, shop, monkeypatch):
    raw = care.client
    assert raw.post(SIGNUP, json={"email": EMAIL, "offeringId": shop["id"]},
                    headers={"X-Bss-Request": ""}).status_code == 403  # fmt: skip
    monkeypatch.setattr(settings, "self_care_enabled", False)
    assert care.post(SIGNUP, {"email": EMAIL, "offeringId": shop["id"]}).status_code == 404


# --- failures ---


def test_a_failed_identity_check_creates_nothing(api, care, db_conn, shop):
    signup = passed(api, care, db_conn, shop, familyName="Declined")
    assert signup["state"] == "identityFailed"
    assert signup["identity"]["reason"].startswith("stub:")
    assert count(db_conn, "party") == 0 and count(db_conn, "billing_account") == 0
    refused = pay(care, signup)
    assert refused.status_code == 409


def test_a_check_in_review_waits_for_the_operator(api, care, db_conn, shop):
    stock(api)
    first = passed(api, care, db_conn, shop, familyName="Review")
    second = passed(api, care, db_conn, shop, email="two@example.org", familyName="Review")
    assert first["state"] == second["state"] == "identityReview"
    assert {r["id"] for r in api.get(f"{INTERNAL}?state=identityReview").json()} == {
        first["id"],
        second["id"],
    }
    approved = api.post(f"{INTERNAL}/{first['id']}/decision", {"decision": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "identityPassed"
    assert approved.json()["decidedBy"] == "i.petrova"
    rejected = api.post(
        f"{INTERNAL}/{second['id']}/decision", {"decision": "reject", "reason": "documents forged"}
    ).json()
    assert (
        rejected["state"] == "identityFailed"
        and rejected["identity"]["reason"] == "documents forged"
    )
    again = api.post(f"{INTERNAL}/{second['id']}/decision", {"decision": "approve"})
    assert again.status_code == 409 and again.json()["code"] == "signupState"
    assert api.post(f"{INTERNAL}/{second['id']}/decision", {"decision": "maybe"}).status_code == 422
    assert pay(care, first).status_code == 202
    wake(api, db_conn, 3)
    assert state(care, first)["state"] == "active"


def test_an_unreachable_identity_service_goes_to_an_operator(api, care, db_conn, shop, monkeypatch):
    monkeypatch.setattr(settings, "identity_max_attempts", 2)
    signup = verified(care, shop)
    assert details(care, signup, familyName="Unavailable").status_code == 200
    wake(api, db_conn)
    row = api.get(f"{INTERNAL}/{signup['id']}").json()
    assert row["identityCheck"]["state"] == "pending" and row["identityCheck"]["attempts"] == 1
    api.clock.set(api.clock.now() + timedelta(minutes=2))  # past the backoff
    wake(api, db_conn)
    row = api.get(f"{INTERNAL}/{signup['id']}").json()
    assert row["state"] == "identityReview" and row["identityCheck"]["state"] == "exhausted"
    assert "could not be done" in row["identity"]["reason"]


def test_a_declined_card_can_pay_again(api, care, db_conn, shop):
    stock(api)
    signup = passed(api, care, db_conn, shop)
    assert pay(care, signup, "tok_decline").status_code == 202
    wake(api, db_conn, 2)
    declined = state(care, signup)
    assert declined["state"] == "identityPassed"
    assert declined["payment"]["state"] == "declined" and declined["payment"]["declineReason"]
    assert pay(care, signup, "tok_mastercard").status_code == 202
    wake(api, db_conn, 3)
    assert state(care, signup)["state"] == "active"
    assert count(db_conn, "customer") == 1 and count(db_conn, "billing_account") == 1
    assert count(db_conn, "payment_method") == 2 and count(db_conn, "payment_command") == 2


def test_a_rejected_token_is_refused_at_once(api, care, db_conn, shop):
    signup = passed(api, care, db_conn, shop)
    refused = pay(care, signup, "tok_unknown")
    assert refused.status_code == 422 and refused.json()["code"] == "cardRejected"
    card_number = {"token": "tok_visa", "password": PASSWORD, "cardNumber": "4242"}
    assert care.post(f"{SIGNUP}/{signup['id']}/payment", card_number).status_code == 400
    weak = care.post(f"{SIGNUP}/{signup['id']}/payment", {"token": "tok_visa", "password": "short"})
    assert weak.status_code == 422
    assert count(db_conn, "customer") == 0


@pytest.mark.parametrize(
    ("esims", "numbers", "code"), [(2, 0, "numberUnavailable"), (0, 2, "simUnavailable")]
)
def test_a_refused_order_fails_the_signup_and_keeps_the_balance(
    api, care, db_conn, shop, esims, numbers, code
):
    stock(api, esims=esims, numbers=numbers)
    signup = passed(api, care, db_conn, shop)
    paid = pay(care, signup).json()
    amount = D(str(paid["payment"]["amount"]["value"]))
    wake(api, db_conn, 3)
    failed = state(care, signup)
    assert failed["state"] == "failed" and failed["failure"]["code"] == code
    assert failed["esim"] is None
    assert balance(db_conn, failed["billingAccount"]) == amount
    (row,) = api.get(f"{INTERNAL}?state=failed").json()
    assert row["failure"]["code"] == code


def test_a_signup_expires_after_a_day_without_progress(api, care, db_conn, shop):
    signup = start(care, shop)
    code = last_code()
    stale = verified(care, shop, "late@example.org")
    api.clock.set(api.clock.now() + timedelta(hours=24, minutes=1))
    expired = care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": code})
    assert expired.status_code == 409 and expired.json()["code"] == "signupExpired"
    assert state(care, signup)["state"] == "expired"
    wake(api, db_conn)  # the worker expires the other one
    row = api.get(f"{INTERNAL}/{stale['id']}").json()
    assert row["state"] == "expired" and row["expiresAt"] is None


# --- abuse limits ---


def test_three_signups_a_day_per_email_and_ten_an_hour_per_address(api, care, shop):
    for _ in range(3):
        start(care, shop)
    limited = care.post(SIGNUP, {"email": EMAIL.upper(), "offeringId": shop["id"]})
    assert limited.status_code == 429 and limited.headers["Retry-After"]
    for n in range(7):
        start(care, shop, f"n{n}@example.org")
    assert (
        care.post(SIGNUP, {"email": "late@example.org", "offeringId": shop["id"]}).status_code
        == 429
    )
    api.clock.set(api.clock.now() + timedelta(hours=1, minutes=1))
    start(care, shop, "late@example.org")
    assert care.post(SIGNUP, {"email": EMAIL, "offeringId": shop["id"]}).status_code == 429
    api.clock.set(api.clock.now() + timedelta(days=1))
    start(care, shop)


def test_the_operator_list_pages_and_filters(api, care, shop):
    first = start(care, shop, "a@example.org")
    start(care, shop, "b@example.org")
    page = api.get(f"{INTERNAL}?limit=1")
    assert (
        page.status_code == 200 and len(page.json()) == 1 and 'rel="next"' in page.headers["Link"]
    )
    (found,) = api.get(f"{INTERNAL}?email=A@example.org").json()
    assert found["id"] == first["id"] and found["clientAddress"]
    assert api.get(f"{INTERNAL}?state=nope").status_code == 400
    assert api.get(f"{INTERNAL}/{UUID(int=1)}").status_code == 404


def test_the_stub_identity_adapter(identity_stub):
    from app.integrations.identity.interface import Person

    person = Person("Grace", "Hopper", date(1990, 12, 9))
    assert identity_stub.verify("k", person, {}) is None
    assert identity_stub.verify("k", person, {}).verdict == "pass"
    assert len(identity_stub.checks) == 1 and identity_stub.attempts == ["k", "k"]
