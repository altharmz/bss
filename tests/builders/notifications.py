"""Shared builders of tests/test_notifications.py, used by other test modules (#423)."""

import json
from datetime import datetime, time

from app.notify import events as notify_events
from tests import timeline

MSG = "/tmf-api/communicationManagement/v4/communicationMessage"

PHONE = "12125550123"


def customer_of(db_conn, acc):
    row = db_conn.execute(
        "SELECT customer_id FROM billing_account WHERE id = %s", (acc["id"],)
    ).fetchone()
    db_conn.commit()
    return row[0]


def contact(db_conn, acc, *, phone=PHONE, email="ada@example.org"):
    media = []
    if email:
        media.append({"@type": "EmailContactMedium", "emailAddress": email})
    if phone:
        media.append({"@type": "PhoneContactMedium", "phoneNumber": phone})
    db_conn.execute(
        "UPDATE customer SET details = details || %s::jsonb WHERE id = %s",
        (json.dumps({"contactMedium": media}), customer_of(db_conn, acc)),
    )
    db_conn.commit()


def messages(api, **filters):
    query = "&".join(f"{k}={v}" for k, v in filters.items())
    response = api.get(f"{MSG}?limit=100" + (f"&{query}" if query else ""))
    assert response.status_code == 200, response.text
    return response.json()


def trait(message, name):
    return next((c["value"] for c in message["characteristic"] if c["name"] == name), None)


def of_event(api, event, **filters):
    return [m for m in messages(api, **filters) if trait(m, "event") == event]


def fetched(db_conn, now, name):
    """The keys detector `name` fetches at `now`: none of a told event (#340)."""
    run = dict(notify_events.detectors(now, timeline.TZ))[name]
    keys = [event.key for event in run(db_conn)]
    db_conn.commit()
    return keys


def quiet(day, hour):
    return datetime.combine(day, time(hour), timeline.TZ)
