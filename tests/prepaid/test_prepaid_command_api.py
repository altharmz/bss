"""P3i: the internal prepaid command operations and their MCP tools."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests.apiclient import decimals, total
from tests.builders.prepaid.prepaid_command_api import failed
from tests.builders.prepaid.prepaid_dispatch import dispatch
from tests.builders.prepaid.prepaid_orders import purchase, shop
from tests.builders.prepaid.prepaid_posting import prepared
from tests.builders.prepaid.prepaid_renewal import renewal
from tests.factories import NOW

BASE = "/internal/v1/prepaid/command"


def test_a_completed_order_command_with_its_links_and_filters(api, db_conn):
    off, acc = shop(api, db_conn)
    order = purchase(api, off, acc)
    cmd_id = db_conn.execute("SELECT id FROM prepaid_command").fetchone()[0]
    pending = api.get(f"{BASE}/{cmd_id}").json()
    assert (pending["state"], pending["attempts"], pending["billingRun"]) == ("pending", 0, None)
    assert pending["productOrder"]["id"] == order["id"]
    assert worker_db.acquire_ownership(db_conn)
    db_conn.commit()
    assert dispatch(db_conn, api.clock.now()) == [cmd_id]
    db_conn.commit()

    body = api.get(f"{BASE}/{cmd_id}").json()
    run_id, bills = db_conn.execute(
        "SELECT r.id, array_agg(b.id::text ORDER BY b.bill_date, b.id) FROM billing_run r"
        " JOIN customer_bill b ON b.billing_run_id = r.id WHERE r.command_id = %s GROUP BY r.id",
        (cmd_id,),
    ).fetchone()
    products = sorted(i["product"]["id"] for i in api.get(order["href"]).json()["productOrderItem"])
    assert body["id"] == str(cmd_id) and body["kind"] == "order"
    assert (body["state"], body["outcome"], body["reason"], body["error"]) == (
        "completed",
        "funded",
        None,
        None,
    )
    assert body["billingAccount"]["id"] == acc["id"]
    assert body["attempts"] == 1 and body["nextRetryAt"] is None
    assert body["needs_topup"] is None
    assert body["productOrder"] == {"id": order["id"], "href": order["href"]}
    assert [p["id"] for p in body["products"]] == products and len(products) == 2
    assert body["billingRun"]["id"] == str(run_id)
    assert [b["id"] for b in body["bills"]] == bills and len(bills) == 2

    listed = api.get(f"{BASE}?billingAccount.id={acc['id']}")
    assert listed.status_code == 200 and total(listed, api) == "1"
    assert listed.json() == [body]
    assert api.get(f"{BASE}?state=failedTechnical").json() == []
    empty = api.get(f"{BASE}?billingAccount.id={uuid4()}")
    assert empty.json() == [] and total(empty, api) == "0"


def test_paging_newest_first_and_query_errors(api, db_conn):
    first = prepared(db_conn)[2]
    second = commands.enqueue(
        db_conn,
        uuid4(),
        first["billing_account_id"],
        kind="order",
        due_date=NOW.date(),
        request={"order_id": str(uuid4()), "products": []},
        now=NOW + timedelta(seconds=1),
    )
    db_conn.commit()
    page = api.get(f"{BASE}?limit=1")
    assert total(page, api) == "2" and page.headers["X-Result-Count"] == "1"
    assert [c["id"] for c in page.json()] == [str(second["id"])]
    assert [c["id"] for c in api.get(f"{BASE}?offset=1").json()] == [str(first["id"])]
    for query in (
        "state=running",
        "billingAccount.id=nope",
        "state=pending&state=completed",
        "kind=order",
    ):
        assert api.get(f"{BASE}?{query}").status_code == 400, query
    assert api.get(f"{BASE}/{uuid4()}").status_code == 404
    assert api.get(f"{BASE}/not-a-uuid").status_code == 404
    assert api.get(f"{BASE}/{first['id']}?fields=id").status_code == 400


def test_a_shortfall_shows_needs_top_up_and_the_renewed_product(api, db_conn):
    _, cmd, _, now = renewal(db_conn, amount="20")
    db_conn.commit()
    assert dispatch(db_conn, now) == [cmd["id"]]
    db_conn.commit()
    body = decimals(api.get(f"{BASE}/{cmd['id']}"))
    product = db_conn.execute(
        "SELECT product_id FROM funded_interval WHERE id = %s",
        (cmd["request"]["previous_interval_id"],),
    ).fetchone()[0]
    reason = commands.needs_topup(db_conn, cmd["id"])
    assert (body["kind"], body["state"], body["attempts"]) == ("renewal", "pending", 0)
    assert body["productOrder"] is None and body["billingRun"] is None and body["bills"] == []
    assert [p["id"] for p in body["products"]] == [str(product)]
    assert body["needs_topup"]["required"] == reason["required"]
    assert body["needs_topup"]["available"] == reason["available"]
    assert body["needs_topup"]["shortfall"] == reason["required"] - reason["available"]
    assert body["needs_topup"]["shortfall"] > D(0)


def test_retry_returns_a_failed_command_to_pending_for_the_worker(api, db_conn, monkeypatch):
    _, prod, cmd = failed(db_conn, monkeypatch)
    before = api.get(f"{BASE}/{cmd['id']}").json()
    assert (before["state"], before["error"], before["attempts"]) == (
        "failedTechnical",
        "after completion",
        1,
    )
    assert before["nextRetryAt"] is not None
    # Within the one-minute backoff the worker leaves it alone ...
    assert dispatch(db_conn, NOW + timedelta(seconds=10)) == []
    api.clock.set(NOW + timedelta(seconds=10))
    response = api.post(f"{BASE}/{cmd['id']}/retry", None)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert (queued["id"], queued["state"], queued["attempts"]) == (str(cmd["id"]), "pending", 1)
    assert queued["error"] is None and queued["nextRetryAt"] is None
    stored = commands.get(db_conn, cmd["id"])
    assert stored["request"] == cmd["request"] and stored["created_at"] == cmd["created_at"]
    assert db_conn.execute(
        "SELECT count(*) FROM funding_command_hold c JOIN funding_hold h ON h.id = c.hold_id"
        " WHERE c.command_id = %s AND h.released_at IS NULL",
        (cmd["id"],),
    ).fetchone() == (1,)
    again = api.post(f"{BASE}/{cmd['id']}/retry", None)
    assert again.status_code == 409 and "failedTechnical" in again.json()["message"]
    # ... after the manual retry it posts at once, with the same identity.
    assert dispatch(db_conn, NOW + timedelta(seconds=10)) == [cmd["id"]]
    db_conn.commit()
    done = api.get(f"{BASE}/{cmd['id']}").json()
    assert (done["state"], done["outcome"], done["attempts"]) == ("completed", "funded", 2)
    assert [p["id"] for p in done["products"]] == [str(prod["id"])]
    assert api.post(f"{BASE}/{cmd['id']}/retry", None).status_code == 409
    assert api.post(f"{BASE}/{uuid4()}/retry", None).status_code == 404
    assert api.post(f"{BASE}/{cmd['id']}/retry?force=1", None).status_code == 400


def test_retry_before_recorded_history_is_a_conflict(api, db_conn, monkeypatch):
    _, _, cmd = failed(db_conn, monkeypatch)
    api.clock.set(NOW - timedelta(seconds=1))
    response = api.post(f"{BASE}/{cmd['id']}/retry", None)
    assert response.status_code == 409
    assert commands.get(db_conn, cmd["id"])["state"] == "failedTechnical"


def test_mcp_reads_with_read_scope_and_retries_with_write_scope(api, db_conn, mcp, monkeypatch):
    _, _, cmd = failed(db_conn, monkeypatch)
    api.clock.set(NOW + timedelta(seconds=10))
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    listed = mcp.tools(reader)
    assert {"list_prepaid_commands", "get_prepaid_command"} <= set(listed)
    assert "retry_prepaid_command" not in listed
    error, answer = mcp.result(reader, "list_prepaid_commands", {"state": "failedTechnical"})
    assert not error and [c["id"] for c in answer["body"]] == [str(cmd["id"])]
    error, answer = mcp.result(writer, "retry_prepaid_command", {"command_id": str(cmd["id"])})
    assert not error and answer["status"] == 202 and answer["body"]["state"] == "pending"
    error, answer = mcp.result(reader, "get_prepaid_command", {"command_id": str(cmd["id"])})
    assert not error and answer["body"]["state"] == "pending"
    assert UUID(answer["body"]["id"]) == cmd["id"]
