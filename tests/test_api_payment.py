"""TMF676 Payment (v4): recording received payments."""

from decimal import Decimal

import pytest

from app.api import payment as payment_api
from tests import apidata
from tests.apiclient import decimals, total
from tests.apidata import NIL, PAYMENT
from tests.timeline import NOW, z


def stored(db_conn) -> list[tuple]:
    return db_conn.execute(
        "SELECT amount, currency, payment_method, status, received_at FROM payment"
    ).fetchall()


def test_payment_is_recorded_as_done(api, db_conn):
    acc = apidata.account(api)
    created = api.post_raw(
        PAYMENT,
        f'{{"@type": "Payment", "account": {{"id": "{acc["id"]}"}}, "totalAmount": {{"unit": "USD",'
        ' "value": 1500.50}, "paymentMethod": {"name": "bankTransfer"}}',
    )
    assert created.status_code == 201
    body = created.json()
    assert created.headers["Location"] == body["href"] == f"{PAYMENT}/{body['id']}"
    assert '"totalAmount":{"unit":"USD","value":1500.50}' in created.text
    assert body["status"] == "done"
    assert body["paymentDate"] == body["statusDate"] == z(NOW)  # the clock
    assert body["account"] == {
        "id": acc["id"],
        "href": acc["href"],
        "name": acc["name"],
        "@referredType": "BillingAccount",
    }
    assert body["paymentMethod"] == {"name": "bankTransfer"}  # as sent, no @type invented
    ((amount, currency, method, status, received_at),) = stored(db_conn)
    assert (amount, currency, method, status) == (Decimal("1500.50"), "USD", "bankTransfer", "done")
    assert received_at == api.clock.now()
    assert api.get(body["href"]).json() == body


def usd(value) -> dict:
    return {"unit": "USD", "value": value}


def test_amount_and_tax_amount_split_the_total(api, db_conn):
    """TMF676: amount is net of taxes, totalAmount includes them. Either part gives the
    other; both must add up; none sent is a payment with no tax."""
    acc = apidata.account(api)
    for extra in (
        {"amount": usd(1400)},
        {"taxAmount": usd(100)},
        {"amount": usd(1400), "taxAmount": usd(100)},
    ):
        created = api.post(PAYMENT, apidata.payment_body(acc, **extra))
        assert created.status_code == 201, extra
        body = created.json()
        assert (body["amount"], body["taxAmount"], body["totalAmount"]) == (
            usd(1400),
            usd(100),
            usd(1500),
        )
        assert api.get(f"{body['href']}?fields=taxAmount").json()["taxAmount"] == usd(100)
    plain = api.post(PAYMENT, apidata.payment_body(acc)).json()
    assert (plain["amount"], plain["taxAmount"]) == (usd(1500), usd(0))
    assert (
        sorted(db_conn.execute("SELECT amount, tax_amount FROM payment").fetchall())
        == [(Decimal("1500.00"), Decimal("0.00"))] + [(Decimal("1500.00"), Decimal("100.00"))] * 3
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"amount": usd(1400), "taxAmount": usd(50)},  # does not add up
        {"taxAmount": {"unit": "EUR", "value": 100}},
        {"amount": {"unit": "EUR", "value": 1400}},
        {"taxAmount": usd(1600)},  # more than the total
        {"taxAmount": usd(-1)},
        {"amount": usd(1600)},  # a negative tax
    ],
)
def test_a_tax_split_that_does_not_fit_the_total_is_refused(api, db_conn, extra):
    acc = apidata.account(api)
    assert api.post(PAYMENT, apidata.payment_body(acc, **extra)).status_code == 422
    assert stored(db_conn) == []


def test_payment_method_is_mandatory(api, db_conn):
    acc = apidata.account(api)
    body = apidata.payment_body(acc)
    assert (
        api.post(PAYMENT, {k: v for k, v in body.items() if k != "paymentMethod"}).status_code
        == 422
    )
    assert api.post(PAYMENT, {**body, "paymentMethod": {}}).status_code == 422
    by_type = {**body, "paymentMethod": {"@type": "Cash"}}
    assert api.post(PAYMENT, by_type).json()["paymentMethod"] == {"@type": "Cash"}
    both = {**body, "paymentMethod": {"name": "card", "@type": "CreditCard"}}
    assert api.post(PAYMENT, both).json()["paymentMethod"] == {
        "name": "card",
        "@type": "CreditCard",
    }
    # the rest of a paymentMethod is stored as sent, checked against the spec
    preferred = {**body, "paymentMethod": {"name": "card", "isPreferred": True}}
    assert api.post(PAYMENT, preferred).json()["paymentMethod"] == {
        "name": "card",
        "isPreferred": True,
    }
    unknown = {**body, "paymentMethod": {"name": "card", "zzUnknown": True}}
    assert api.post(PAYMENT, unknown).status_code == 400


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"totalAmount": {"unit": "USD", "value": 0}}, 422),
        ({"totalAmount": {"unit": "USD", "value": -5}}, 422),
        ({"totalAmount": {"unit": "USD", "value": "5"}}, 422),
        ({"account": {"id": NIL}}, 422),
        ({"account": {"href": "/x"}}, 422),
        ({"status": "done"}, 400),
        ({"paymentDate": "2026-01-01T00:00:00Z"}, 400),
        ({"description": 5}, 422),  # a document, checked against the spec
        ({"payer": {"id": "p1", "@referredType": "Individual", "zzUnknown": 1}}, 400),
        ({"id": "p1"}, 400),
    ],
)
def test_invalid_payments_are_rejected(api, db_conn, change, status):
    acc = apidata.account(api)
    assert api.post(PAYMENT, apidata.payment_body(acc, **change)).status_code == status
    assert stored(db_conn) == []


def test_three_decimals_are_rejected(api, db_conn):
    acc = apidata.account(api)
    text = (
        f'{{"account": {{"id": "{acc["id"]}"}}, "totalAmount": {{"unit": "USD", "value": 10.005}},'
        ' "paymentMethod": {"name": "cash"}}'
    )
    assert api.post_raw(PAYMENT, text).status_code == 422
    assert stored(db_conn) == []


def test_currency_must_match_the_account(api, db_conn):
    acc = apidata.account(api)  # USD
    response = api.post(PAYMENT, apidata.payment_body(acc, unit="EUR"))
    assert response.status_code == 422
    assert "does not match the account's currency USD" in response.json()["message"]
    assert stored(db_conn) == []


def test_currency_foreign_key_violation_is_a_409_not_a_500(api, db_conn, monkeypatch):
    """If the check before the insert were ever skipped, the composite FK (account,
    currency) still refuses the row - and that must surface as a TMF 409."""
    acc = apidata.account(api)  # USD
    real = payment_api.parties.get_billing_account

    def claims_eur(conn, account_id, lock=None):
        row = real(conn, account_id, lock)
        return None if row is None else {**row, "currency": "EUR"}

    monkeypatch.setattr(payment_api.parties, "get_billing_account", claims_eur)
    response = api.post(PAYMENT, apidata.payment_body(acc, unit="EUR"))
    assert response.status_code == 409
    assert stored(db_conn) == []


def test_payment_list_filters(api):
    ivan, anna = apidata.account(api), apidata.account(api, name="Second")
    apidata.payment(api, ivan, 100)
    apidata.payment(api, ivan, 200)
    apidata.payment(api, anna, 300)
    listed = api.get(f"{PAYMENT}?account.id={ivan['id']}")
    assert total(listed, api) == "2"
    values = [p["totalAmount"]["value"] for p in decimals(listed)]
    # TEST-CHANGE (#314): payments are listed newest first (decided by the human 2026-11-11)
    assert values == [Decimal("200.00"), Decimal("100.00")]
    assert len(api.get(f"{PAYMENT}?status=done").json()) == 3
    assert api.get(f"{PAYMENT}?account.id=nope").status_code == 400


@pytest.mark.parametrize("method", ["PATCH", "DELETE", "PUT"])
def test_payments_cannot_be_changed(api, method):
    href = apidata.payment(api, apidata.account(api))["href"]
    assert api.request(method, href).status_code == 405
