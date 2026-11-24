"""Builders other test modules share, from tests/tmf/test_events.py (#423)."""

import json
from typing import Any

import psycopg

from app.events.types import BASES
from tests import contract

CALLBACK = "http://listener.test/events"


def subscribe(api, name: str, *types: str) -> dict:
    body: dict[str, Any] = {"callback": CALLBACK}
    if types:
        body["query"] = "eventType=" + ",".join(types)
    response = api.post(f"{BASES[name]}/hub", body)
    assert response.status_code == 201, response.text
    return response.json()


def stored(conn: psycopg.Connection, name: str | None = None) -> list[tuple[str, dict]]:
    """(event type, payload) of the stored events, oldest first; each payload checked
    against the spec."""
    rows = conn.execute(
        "SELECT api, event_type, payload::text FROM event"
        " WHERE %(api)s::text IS NULL OR api = %(api)s"
        " ORDER BY seq",
        {"api": name},
    ).fetchall()
    out = []
    for api_name, event_type, text in rows:
        payload = json.loads(text)
        contract.assert_valid(api_name, event_type, payload)
        assert payload["eventType"] == event_type
        out.append((event_type, payload))
    return out


def types(conn: psycopg.Connection, name: str | None = None) -> list[str]:
    return [event_type for event_type, _ in stored(conn, name)]
