"""Event lists are newest first (#314, decided by the human 2026-11-11): bills, payments,
refunds and each TMF654 balance action kind, like orders (#312). Without a count the UI
pages from the start, so the start must be the newest."""

from datetime import timedelta

from tests import apidata
from tests.builders.api_topup import TOPUP, post, prepaid


def test_top_ups_are_listed_newest_requested_first(api, db_conn):
    _, acc = prepaid(api, db_conn)
    start = api.clock.now()
    for minute, value in enumerate((10, 20, 30)):
        api.clock.set(start + timedelta(minutes=minute))
        assert post(api, acc, value).status_code == 201
    listed = api.get(f"{TOPUP}?partyAccount.id={acc.id}")
    assert [t["amount"]["amount"] for t in listed.json()] == [30, 20, 10]
    page = api.get(f"{TOPUP}?partyAccount.id={acc.id}&offset=1&limit=1")
    assert [t["amount"]["amount"] for t in page.json()] == [20]


def test_payments_are_listed_newest_first(api):
    acc = apidata.account(api)
    first = apidata.payment(api, acc, 100)
    second = apidata.payment(api, acc, 200)
    listed = api.get(f"/tmf-api/payment/v4/payment?account.id={acc['id']}")
    assert [p["id"] for p in listed.json()] == [second["id"], first["id"]]


def test_each_action_kind_reads_its_index_backwards(db_conn):
    db_conn.execute("SET enable_seqscan = off")
    db_conn.execute("SET enable_sort = off")
    query = (
        "SELECT id FROM balance_action WHERE kind = 'topup'"
        " ORDER BY requested_at DESC, id DESC LIMIT 51"
    )
    plan = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query))
    assert "balance_action_kind_requested_idx" in plan, plan
