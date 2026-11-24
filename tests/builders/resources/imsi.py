"""Builders other test modules share, from tests/resources/test_imsi.py (#423)."""


def all_refs(*uids):
    return [{"id": u, "@type": "ResourceRef", "@referredType": "LogicalResource"} for u in uids]
