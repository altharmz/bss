"""Builders other test modules share, from tests/prepaid/test_api_adjust.py (#423)."""

from tests.builders.prepaid.api_topup import BASE

ADJUST = f"{BASE}/adjustBalance"


def body(acc, value, **over):
    return {
        "@type": "AdjustBalance",
        "amount": {"amount": value, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": acc.bucket},
        **over,
    }


def post(api, acc, value, headers=None, **over):
    return api.post(ADJUST, body(acc, value, **over), headers=headers)
