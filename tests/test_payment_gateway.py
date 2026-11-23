"""Saved cards (TMF670), card top-ups and auto top-up through the payment adapter (#185).

Every TMF670 response is checked against the vendored schema by the `api` client."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.payment import adapter
from app.repository import funding
from tests import apidata
from tests.builders.api_topup import prepaid
from tests.builders.events import subscribe, types
from tests.builders.payment_gateway import (
    ACCOUNTS,
    METHODS,
    card_body,
    commands_of,
    patch_rule,
    rule,
    run,
    save,
    topup,
)
from tests.factories import NOW, prepaid_bucket

COMMANDS = "/internal/v1/payment/command"
TOPUP_API = "/tmf-api/prepayBalanceManagement/v4/topupBalance"


@pytest.fixture(autouse=True)
def stub():
    adapter().reset()
    yield adapter()
    adapter().reset()


def balance(db_conn, acc):
    spendable = funding.load(db_conn, UUID(acc["id"])).spendable
    db_conn.commit()  # funding.load locks the account: release it for the API
    return spendable


# --- TMF670 ---


def test_add_list_default_and_delete_a_card(api, db_conn):
    _, acc = prepaid(api, db_conn)
    first = save(api, acc)
    assert first["isPreferred"] is True and first["lastFourDigits"] == "4242"
    assert "token" not in first and first["account"][0]["id"] == acc["id"]
    second = save(api, acc, "tok_mc", name="Work card", brand="mastercard")
    assert second["isPreferred"] is False
    listed = api.get(f"{METHODS}?account.id={acc['id']}").json()
    assert {m["id"] for m in listed} == {first["id"], second["id"]}
    made = api.patch(f"{METHODS}/{second['id']}", {"isPreferred": True})
    assert made.status_code == 200 and made.json()["isPreferred"] is True
    assert api.get(f"{METHODS}/{first['id']}").json()["isPreferred"] is False
    only = api.get(f"{METHODS}?account.id={acc['id']}&isPreferred=true").json()
    assert [m["id"] for m in only] == [second["id"]]
    assert api.delete(f"{METHODS}/{second['id']}").status_code == 204
    assert api.get(f"{METHODS}/{second['id']}").status_code == 404
    assert [m["id"] for m in api.get(f"{METHODS}?account.id={acc['id']}").json()] == [first["id"]]
    # the default was removed: the account has none, nothing is promoted
    assert api.get(f"{METHODS}/{first['id']}").json()["isPreferred"] is False
    assert api.patch(f"{METHODS}/{first['id']}", {"name": "Renamed"}).json()["name"] == "Renamed"


def test_card_writes_send_tmf670_events(api, db_conn):
    _, acc = prepaid(api, db_conn)
    subscribe(api, "TMF670")
    card = save(api, acc)
    api.patch(f"{METHODS}/{card['id']}", {"name": "Renamed"})
    api.delete(f"{METHODS}/{card['id']}")
    db_conn.commit()
    assert types(db_conn, "TMF670") == [
        "PaymentMethodCreateEvent",
        "PaymentMethodAttributeValueChangeEvent",
        "PaymentMethodDeleteEvent",
    ]


@pytest.mark.parametrize(
    "over",
    [
        {"cardNumber": "4242424242424242"},
        {"cvv": "123"},
        {"pan": "4242424242424242"},
        {"token": "4242 4242 4242 4242"},
        {"token": "4242424242424242"},
    ],
)
def test_a_card_number_is_never_accepted(api, db_conn, over):
    _, acc = prepaid(api, db_conn)
    response = api.post(METHODS, card_body(acc, **over))
    assert response.status_code == 400, response.text
    assert "number" in response.json()["message"]
    assert db_conn.execute("SELECT count(*) FROM payment_method").fetchone() == (0,)


def test_card_write_rules(api, db_conn):
    _, acc = prepaid(api, db_conn)
    assert api.post(METHODS, card_body(acc, **{"@type": "bankCard"})).status_code == 422
    assert api.post(METHODS, card_body(acc, lastFourDigits="42")).status_code == 422
    assert (
        api.post(METHODS, card_body(acc, expirationDate="2020-01-01T00:00:00Z")).status_code == 422
    )
    assert api.post(METHODS, card_body(acc, status="Active")).status_code == 400
    card = save(api, acc)
    assert api.post(METHODS, card_body(acc)).status_code == 409  # the same token twice
    assert api.patch(f"{METHODS}/{card['id']}", {"token": "tok_x"}).status_code == 400
    assert api.patch(f"{METHODS}/{card['id']}", {"brand": "amex"}).status_code == 400


# --- card top-up ---


@pytest.mark.capability("worker.paymentGateway")
def test_a_card_top_up_raises_the_balance_only_after_the_gateway_confirms(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="10")
    card = save(api, acc)
    response = topup(api, acc)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["state"] == "pending" and queued["paymentMethod"]["id"] == card["id"]
    assert balance(db_conn, acc) == D("10")
    run(db_conn, api)  # submitted to the gateway, not yet answered
    assert api.get(queued["href"]).json()["state"] == "sent"
    assert balance(db_conn, acc) == D("10")
    run(db_conn, api)  # the gateway's answer is read on the next wake-up
    done = api.get(queued["href"]).json()
    assert done["state"] == "confirmed" and done["payment"] and done["topup"]
    assert balance(db_conn, acc) == D("35")
    recorded = api.get(done["topup"]["href"]).json()
    assert recorded["status"] == "completed" and recorded["isAutoTopup"] is False
    assert recorded["paymentMethod"]["id"] == card["id"]
    assert api.get(done["payment"]["href"]).json()["totalAmount"]["value"] == D("25")
    run(db_conn, api, 2)
    assert balance(db_conn, acc) == D("35")  # confirmed once
    assert len(adapter().charges) == 1


def test_a_declined_charge_changes_nothing_and_shows_the_reason(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="10")
    save(api, acc, "tok_decline")
    queued = topup(api, acc).json()
    run(db_conn, api, 2)
    declined = api.get(queued["href"]).json()
    assert declined["state"] == "declined" and "card_declined" in declined["declineReason"]
    assert declined["payment"] is None and declined["topup"] is None
    assert balance(db_conn, acc) == D("10")
    assert api.get(f"{TOPUP_API}?partyAccount.id={acc['id']}").json() == []
    assert api.post(f"{queued['href']}/retry", None).status_code == 409  # a decline is final


def test_the_same_idempotency_key_never_charges_twice(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="0")
    save(api, acc)
    first = topup(api, acc, key="same")
    again = topup(api, acc, key="same")
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert topup(api, acc, 30, key="same").status_code == 409
    run(db_conn, api, 3)
    assert balance(db_conn, acc) == D("25")
    assert len(adapter().charges) == 1
    other = topup(api, acc, key="another")  # a different key is a different top-up
    assert other.status_code == 202 and other.json()["id"] != first.json()["id"]
    run(db_conn, api, 2)
    assert balance(db_conn, acc) == D("50")


def test_a_resend_after_a_temporary_error_carries_the_same_key(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="0")
    save(api, acc, "tok_fail")
    queued = topup(api, acc).json()
    for index in range(5):
        run(db_conn, api, minutes=100 * (index + 1))
    exhausted = api.get(queued["href"]).json()
    assert exhausted["state"] == "exhausted" and "unavailable" in exhausted["lastError"]
    assert exhausted["attempts"] == 5 and balance(db_conn, acc) == D("0")
    assert set(adapter().attempts) == {queued["id"]}  # every attempt named the one command
    adapter().recover("tok_fail")
    assert api.post(f"{queued['href']}/retry", None).status_code == 202
    run(db_conn, api, 2, minutes=1000)
    assert api.get(queued["href"]).json()["state"] == "confirmed"
    assert balance(db_conn, acc) == D("25")
    assert len(adapter().charges) == 1
    assert api.post(f"{queued['href']}/retry", None).status_code == 409


def test_card_top_up_refusals(api, db_conn):
    _, acc = prepaid(api, db_conn)
    assert topup(api, acc).status_code == 409  # no card yet
    card = save(api, acc)
    assert topup(api, acc, 0).status_code == 422
    cents = api.post_raw(
        f"{ACCOUNTS}/{acc['id']}/cardTopup",
        '{"amount": {"unit": "USD", "value": 1.005}, "idempotencyKey": "c"}',
    )
    assert cents.status_code == 422  # amounts are never rounded
    bad = topup(api, acc, paymentMethod={"id": "00000000-0000-4000-8000-000000000000"})
    assert bad.status_code == 422
    postpaid = api.post(
        f"{ACCOUNTS}/{UUID(int=1)}/cardTopup",
        {"amount": {"unit": "USD", "value": 1}, "idempotencyKey": "x"},
    )
    assert postpaid.status_code == 404
    other = apidata.account(api)
    db_conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (other["id"],)
    )
    prepaid_bucket(db_conn, other["id"], NOW)
    db_conn.commit()
    foreign = topup(api, other, paymentMethod={"id": card["id"]})
    assert foreign.status_code == 422
    # removing a card while a charge is in progress is refused
    assert topup(api, acc, key="k").status_code == 202
    assert api.delete(f"{METHODS}/{card['id']}").status_code == 409
    run(db_conn, api, 2)
    assert api.delete(f"{METHODS}/{card['id']}").status_code == 204
    assert topup(api, acc, key="after").status_code == 409  # its only card is gone


def test_a_postpaid_account_has_no_card_top_up(api, db_conn):
    acc = apidata.account(api)
    save(api, acc)
    assert topup(api, acc).status_code == 409
    rule = patch_rule(api, acc, {"enabled": False})
    assert rule.status_code == 409


# --- auto top-up ---


@pytest.mark.capability("worker.paymentGateway")
def test_auto_top_up_fires_once_when_the_balance_crosses_the_threshold(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="30")
    save(api, acc)
    assert rule(api, acc).status_code == 200
    run(db_conn, api, 3)
    assert commands_of(db_conn) == []  # above the threshold
    # spend 25: the balance (5) is below the threshold
    adjust = api.post(
        "/tmf-api/prepayBalanceManagement/v4/adjustBalance",
        {
            "@type": "AdjustBalance",
            "amount": {"amount": -25, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": acc.bucket},
        },
    )
    assert adjust.status_code == 201, adjust.text
    assert balance(db_conn, acc) == D("5")
    run(db_conn, api)  # the charge is created and submitted in one wake-up
    assert commands_of(db_conn) == [("auto_topup", "sent")]
    run(db_conn, api)
    assert commands_of(db_conn) == [("auto_topup", "confirmed")]
    assert balance(db_conn, acc) == D("55")
    run(db_conn, api, 3)
    assert commands_of(db_conn) == [("auto_topup", "confirmed")]  # once
    assert len(adapter().charges) == 1
    recorded = api.get(f"{TOPUP_API}?partyAccount.id={acc['id']}").json()
    assert [t["isAutoTopup"] for t in recorded if t["amount"]["amount"] == D("50")] == [True]


def test_only_one_auto_top_up_is_in_flight_and_a_decline_waits(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="5")
    save(api, acc, "tok_decline")
    rule(api, acc)
    run(db_conn, api)
    assert commands_of(db_conn) == [("auto_topup", "sent")]
    assert (
        db_conn.execute(
            "SELECT count(*) FROM payment_command WHERE state IN ('pending', 'sent')"
        ).fetchone()[0]
        == 1
    )
    run(db_conn, api)
    assert commands_of(db_conn) == [("auto_topup", "declined")]
    run(db_conn, api, 3)  # not repeated: the balance and the settings did not change
    assert commands_of(db_conn) == [("auto_topup", "declined")]
    assert balance(db_conn, acc) == D("5")
    # a changed rule tries again
    api.clock.set(api.clock.now() + timedelta(hours=1))
    rule(api, acc, amount=60)
    run(db_conn, api)
    assert [s for _, s in commands_of(db_conn)] == ["declined", "sent"]


def test_auto_top_up_rule_validation(api, db_conn):
    _, acc = prepaid(api, db_conn)
    assert rule(api, acc, amount=5).status_code == 422  # below the threshold
    assert rule(api, acc, threshold=0).status_code == 422
    assert patch_rule(api, acc, {"enabled": True}).status_code == 422
    on = rule(api, acc)
    assert on.status_code == 200 and on.json()["enabled"] is True
    assert on.json()["threshold"]["value"] == D("10")
    off = patch_rule(api, acc, {"enabled": False})
    assert off.json()["enabled"] is False and off.json()["amount"]["value"] == D("50")
    assert api.get(f"{ACCOUNTS}/{acc['id']}/autoTopup").json()["enabled"] is False
    bad = patch_rule(
        api,
        acc,
        {
            "enabled": True,
            "threshold": {"unit": "EUR", "value": 1},
            "amount": {"unit": "EUR", "value": 2},
        },
    )
    assert bad.status_code == 422


def test_no_default_card_means_no_automatic_charge(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="5")
    rule(api, acc)
    run(db_conn, api, 2)
    assert commands_of(db_conn) == []
    save(api, acc)
    run(db_conn, api)
    assert commands_of(db_conn) == [("auto_topup", "sent")]


# --- the adapter ---


def test_the_adapter_is_chosen_by_configuration(monkeypatch):
    assert Settings().payment_adapter == "stub"
    with pytest.raises(ValidationError):
        Settings(payment_adapter="nope")
    from app.config import settings

    with monkeypatch.context() as patched:
        patched.setattr(settings, "payment_adapter", "nope")
        with pytest.raises(ValueError, match="BSS_PAYMENT_ADAPTER"):
            adapter()


def test_the_stub_remembers_the_first_answer_and_refunds():
    stub = adapter()
    stub.charge("k1", "tok_ok", D("5.00"), "USD")
    stub.charge("k1", "tok_decline", D("9.00"), "USD")  # the first answer stands
    assert stub.result("k1").status == "succeeded" and len(stub.charges) == 1
    assert stub.result("nope") is None
    assert stub.refund("r1", "stub_ch_k1", D("5.00")) == stub.refund("r1", "stub_ch_k1", D("5.00"))
    assert len(stub.refunds) == 1


def test_payment_commands_can_be_listed_and_read(api, db_conn):
    _, acc = prepaid(api, db_conn)
    save(api, acc)
    made = topup(api, acc).json()
    listed = api.get(f"{COMMANDS}?billingAccount.id={acc['id']}&state=pending")
    assert [c["id"] for c in listed.json()] == [made["id"]]
    assert api.get(f"{COMMANDS}?state=bogus").status_code == 400
    assert api.get(f"{COMMANDS}/{made['id']}").json()["amount"]["value"] == D("25")
    assert api.get(f"{COMMANDS}/{UUID(int=5)}").status_code == 404
    assert api.post(f"{COMMANDS}/{made['id']}/retry", None).status_code == 409
