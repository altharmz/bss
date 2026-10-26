"""Slice 23: GET /internal/v1/eventSubscription, the operators' list of live hubs."""

from fastapi.testclient import TestClient

from app.events.types import BASES
from app.main import app
from tests.test_api_hub import CALLBACK, hub

URL = "/internal/v1/eventSubscription"


def test_live_hubs_newest_first_with_types_and_names(api, db_conn):
    old = hub(api, "TMF676", query="eventType=PaymentCreateEvent,RefundCreateEvent")
    new = hub(api, "TMF622")
    db_conn.execute(
        "UPDATE event_subscription SET subscribed_at = subscribed_at - interval '1 hour'"
        " WHERE id = %s",
        (old["id"],),
    )
    db_conn.commit()
    listed = api.get(URL)
    assert listed.status_code == 200, listed.text
    assert listed.headers["X-Total-Count"] == "2"
    body = listed.json()
    assert [h["id"] for h in body] == [new["id"], old["id"]]
    by_id = {h["id"]: h for h in body}
    assert by_id[old["id"]] == {
        "id": old["id"],
        "api": {"id": "TMF676", "name": "Payment Management"},
        "hub": f"{BASES['TMF676']}/hub/{old['id']}",
        "callback": CALLBACK,
        "query": "eventType=PaymentCreateEvent,RefundCreateEvent",
        "eventTypes": ["PaymentCreateEvent", "RefundCreateEvent"],
        "subscribedAt": by_id[old["id"]]["subscribedAt"],
        "lastDelivery": None,
        "failures": 0,
    }
    assert by_id[new["id"]]["query"] is None and by_id[new["id"]]["eventTypes"] == []


def test_filter_paging_and_deleted_hubs_are_excluded(api):
    pay = hub(api, "TMF676")
    gone = hub(api, "TMF622")
    other = hub(api, "TMF622")
    assert api.delete(f"{BASES['TMF622']}/hub/{gone['id']}").status_code == 204
    assert {h["id"] for h in api.get(URL).json()} == {pay["id"], other["id"]}
    only = api.get(f"{URL}?api=TMF622")
    assert [h["id"] for h in only.json()] == [other["id"]]
    assert only.headers["X-Total-Count"] == "1"
    page = api.get(f"{URL}?limit=1&offset=1")
    assert len(page.json()) == 1 and page.headers["X-Total-Count"] == "2"
    assert api.get(f"{URL}?api=TMF999").status_code == 400
    assert api.get(f"{URL}?api=TMF622&api=TMF676").status_code == 400
    assert api.get(f"{URL}?nope=1").status_code == 400


def test_the_stored_delivery_summary(api, db_conn):
    created = hub(api, "TMF676")
    event = db_conn.execute(
        "INSERT INTO event (api, event_type, resource, resource_id, occurred_at, payload)"
        " VALUES ('TMF676', 'PaymentCreateEvent', 'payment', gen_random_uuid(), now(), '{}')"
        " RETURNING id"
    ).fetchone()[0]
    db_conn.execute(
        "INSERT INTO event_delivery (event_id, subscription_id, state, attempts,"
        " last_attempt_at, last_error, finished_at) VALUES (%s, %s, 'failed', 5, now(),"
        " 'boom', now())",
        (event, created["id"]),
    )
    db_conn.commit()
    item = api.get(URL).json()[0]
    assert item["failures"] == 1
    assert item["lastDelivery"]["state"] == "failed"


def test_the_list_needs_a_session(api):
    with TestClient(app) as raw:
        assert raw.get(URL).status_code == 401
