"""Builders other test modules share, from tests/prepaid/test_payment_gateway.py (#423)."""

from datetime import timedelta

from tests.builders.prepaid.prepaid_renewal_producer import tick

METHODS = "/tmf-api/paymentMethod/v4/paymentMethod"

ACCOUNTS = "/internal/v1/billingAccount"


def card_body(acc, token="tok_visa", **over):
    return {
        "@type": "tokenizedCard",
        "name": "Personal Visa",
        "account": [{"id": acc["id"], "@referredType": "BillingAccount"}],
        "brand": "visa",
        "lastFourDigits": "4242",
        "token": token,
        **over,
    }


def save(api, acc, token="tok_visa", **over):
    response = api.post(METHODS, card_body(acc, token, **over))
    assert response.status_code == 201, response.text
    return response.json()


def topup(api, acc, value=25, key="k-1", **over):
    return api.post(
        f"{ACCOUNTS}/{acc['id']}/cardTopup",
        {"amount": {"unit": "USD", "value": value}, "idempotencyKey": key, **over},
    )


def run(db_conn, api, ticks=1, minutes=0):
    for _ in range(ticks):
        tick(db_conn, api.clock.now() + timedelta(minutes=minutes))


def patch_rule(api, acc, body):
    return api.patch(f"{ACCOUNTS}/{acc['id']}/autoTopup", body)


def rule(api, acc, threshold=10, amount=50, enabled=True):
    return patch_rule(
        api,
        acc,
        {
            "enabled": enabled,
            "threshold": {"unit": "USD", "value": threshold},
            "amount": {"unit": "USD", "value": amount},
        },
    )


def commands_of(db_conn):
    db_conn.commit()
    return db_conn.execute(
        "SELECT purpose, state FROM payment_command ORDER BY created_at, id"
    ).fetchall()
