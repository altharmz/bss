"""Builders other test modules share, from tests/prepaid/test_api_reserve.py (#423)."""

from tests.builders.prepaid.api_topup import BASE

RESERVE = f"{BASE}/reserveBalance"


def body(acc, value, **over):
    return {
        "@type": "ReserveBalance",
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": acc.bucket},
        "partyAccount": {"id": acc.id, "@referredType": "BillingAccount"},
        **over,
    }


def post(api, acc, value, headers=None, **over):
    return api.post(RESERVE, body(acc, value, **over), headers=headers)
