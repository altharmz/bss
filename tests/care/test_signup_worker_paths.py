"""The signup worker's stop, race, failure and email paths (#483), against the real test
database: each ends in the checked signup state and reason, the identity check, the order and
the logged activation email."""

# ruff: noqa: F811
import logging
from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.clock import FixedClock
from app.integrations.identity import adapter as identity
from app.integrations.network import adapter as network
from app.integrations.notify import NotifyError
from app.integrations.notify import adapter as sender
from app.ordering.process import OrderRefused
from app.worker import signup as worker
from tests.builders.care.care_signup import (  # noqa: F401
    identity_stub,
    last_code,
    shop,
    stock,
    wake,
)
from tests.builders.care.self_care import (  # noqa: F401 - fixtures
    BASE,
    PASSWORD,
    care,
    enabled,
    log_sender,
)
from tests.builders.prepaid.prepaid_plans import balance, stubs  # noqa: F401 - fixtures
from tests.builders.prepaid.prepaid_posting import TZ

SIGNUP = BASE + "/signup"
INTERNAL = "/internal/v1/careSignup"
ADDRESS = {"street1": "1 Main St", "city": "Albany", "stateOrProvince": "NY", "postCode": "12207"}
PERSON = {"givenName": "Grace", "familyName": "Hopper", "birthDate": "1990-12-09"}


def pending(care, plan, email="new.customer@example.org"):
    """A verified signup with its details given: its identity check is pending."""
    response = care.post(SIGNUP, {"email": email, "offeringId": plan["id"]})
    assert response.status_code == 201, response.text
    signup = response.json()
    verified = care.post(f"{SIGNUP}/{signup['id']}/verify", {"code": last_code()})
    assert verified.status_code == 200, verified.text
    given = care.put(f"{SIGNUP}/{signup['id']}/details", {**PERSON, "serviceAddress": ADDRESS})
    assert given.status_code == 200, given.text
    return signup


def paid(api, care, db_conn, plan):
    """A signup whose first top-up the gateway confirmed: the next wake-up orders the line."""
    signup = pending(care, plan)
    wake(api, db_conn, 2)
    pay = care.post(f"{SIGNUP}/{signup['id']}/payment", {"token": "tok_visa", "password": PASSWORD})
    assert pay.status_code == 202, pay.text
    wake(api, db_conn)  # the charge goes to the gateway
    return signup


def operator_view(api, signup):
    return api.get(f"{INTERNAL}/{signup['id']}").json()


def run(api, db_conn, **kwargs):
    acted = worker.process_signups(db_conn, clock=FixedClock(api.clock.now()), tz=TZ, **kwargs)
    db_conn.commit()
    return acted


def esim_mails(db_conn):
    rows = db_conn.execute(
        "SELECT content, provider_ref, last_error FROM notification_message"
        " WHERE event_type = 'care_esim_ready'"
    ).fetchall()
    db_conn.commit()
    return rows


def test_a_checkpoint_stops_the_pass_and_each_step_gets_one_heartbeat(api, care, db_conn, shop):
    signup = pending(care, shop)
    beats = []
    assert run(api, db_conn, checkpoint=lambda: True, heartbeat=lambda: beats.append(1)) == []
    check = operator_view(api, signup)["identityCheck"]
    assert (check["state"], check["attempts"]) == ("pending", 0) and beats == []
    run(api, db_conn, heartbeat=lambda: beats.append(1))  # sent: no verdict yet
    assert operator_view(api, signup)["identityCheck"]["state"] == "sent" and beats == [1]


def test_an_unexpected_identity_error_is_logged_and_asked_again_with_the_same_key(
    api, care, db_conn, shop, monkeypatch, caplog
):
    signup = pending(care, shop)
    real = identity().verify
    keys = []

    def broken(key, person, address):
        keys.append(key)
        if len(keys) == 1:
            raise RuntimeError()
        return real(key, person, address)

    monkeypatch.setattr(identity(), "verify", broken)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        run(api, db_conn)
    check = operator_view(api, signup)["identityCheck"]
    assert f"identity check {keys[0]}" in caplog.text
    # an error without a message is named by its type; the signup still waits for the check
    assert (check["state"], check["attempts"], check["lastError"]) == ("pending", 1, "RuntimeError")
    assert operator_view(api, signup)["state"] == "identityPending"
    api.clock.set(api.clock.now() + timedelta(minutes=2))  # past the backoff
    wake(api, db_conn, 2)
    assert operator_view(api, signup)["state"] == "identityPassed"
    assert keys[0] == keys[1]


def test_every_step_on_a_signup_past_it_does_nothing(api, care, db_conn, shop):
    stock(api)
    signup = paid(api, care, db_conn, shop)
    wake(api, db_conn, 2)  # ordered, then activated
    done = operator_view(api, signup)
    assert done["state"] == "active"
    uid = UUID(signup["id"])
    (check_id,) = db_conn.execute(
        "SELECT identity_check_id FROM care_signup WHERE id = %s", (uid,)
    ).fetchone()
    orders = db_conn.execute("SELECT count(*) FROM product_order").fetchone()
    db_conn.commit()
    now = api.clock.now()
    with db_conn.transaction():
        assert worker._expire(db_conn, uid, now, TZ) is None
        assert worker._check(db_conn, check_id, now, TZ) is None  # decided already
        assert worker._payment(db_conn, uid, now, TZ) is None
        assert worker._order(db_conn, uid, now, TZ) is None
        assert worker._activation(db_conn, uid, now, TZ) is None
    db_conn.commit()
    assert operator_view(api, signup) == done
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == orders
    assert len(esim_mails(db_conn)) == 1


def test_a_refused_order_fails_the_signup_and_keeps_the_top_up(
    api, care, db_conn, shop, monkeypatch
):
    stock(api)
    signup = paid(api, care, db_conn, shop)

    def refuse(conn, request, **kwargs):
        raise OrderRefused("item 1: the offering is no longer sold")

    monkeypatch.setattr(worker, "place_order", refuse)
    wake(api, db_conn)
    failed = operator_view(api, signup)
    assert failed["state"] == "failed"
    assert failed["failure"] == {
        "code": "orderRefused",
        "reason": "item 1: the offering is no longer sold",
    }
    assert balance(db_conn, failed["billingAccount"]) > D(0)  # the top-up stays as balance
    assert esim_mails(db_conn) == []


def test_an_activation_the_network_refuses_fails_the_signup_with_its_reason(
    api, care, db_conn, shop
):
    stock(api)
    network().refuse_kinds.add("activate")
    signup = paid(api, care, db_conn, shop)
    wake(api, db_conn)  # ordered: activating
    assert operator_view(api, signup)["state"] == "activating"
    wake(api, db_conn, 2)  # refused by the network, then the order fails
    failed = operator_view(api, signup)
    assert failed["failure"] == {"code": "networkRefused", "reason": "networkRefused: stubRefused"}
    assert failed["state"] == "failed" and failed["order"]["state"] == "failed"
    assert esim_mails(db_conn) == []


@pytest.mark.parametrize(
    ("error", "recorded"),
    [
        (NotifyError("mailbox unavailable"), "mailbox unavailable"),
        (NotifyError(), "the sender refused the message"),
        (ConnectionError(), "ConnectionError"),
    ],
)
def test_a_failed_activation_email_is_logged_with_why_and_the_line_stays_active(
    api, care, db_conn, shop, monkeypatch, error, recorded
):
    stock(api)
    signup = paid(api, care, db_conn, shop)
    wake(api, db_conn)  # ordered: activating

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(sender(), "send_email", broken)
    wake(api, db_conn)
    assert operator_view(api, signup)["state"] == "active"
    ((content, ref, last_error),) = esim_mails(db_conn)
    assert ref is None and last_error == recorded and "******" in content
