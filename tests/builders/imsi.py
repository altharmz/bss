"""Shared builders of tests/test_imsi.py, used by other test modules (#423)."""


def all_refs(*uids):
    return [{"id": u, "@type": "ResourceRef", "@referredType": "LogicalResource"} for u in uids]
