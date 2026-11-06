"""TMF hubs: POST/DELETE /hub in every API, GET /hub/{id} in TMF629 and TMF639.

A hub is a subscription: a callback and optionally the event types it wants. Outside
dev and test the callback must be https and lead only to public addresses
(app/events/callbacks.py) - the service shares its host with a VPN server.
"""

import glob
import json
import os

import pytest
import yaml

from app.config import settings
from app.events import callbacks
from app.events.types import BASES, EVENT_TYPES
from tests import apidata

APIS = sorted(BASES)
CALLBACK = "http://listener.test/events"


def hub(api, name: str, **body) -> dict:
    response = api.post(f"{BASES[name]}/hub", {"callback": CALLBACK, **body})
    assert response.status_code == 201, response.text
    return response.json()


NO_HUB = ("TMF635",)  # its only events are the export job's: no /hub (docs/decisions/usage.md)


def spec_events() -> dict[str, set[str]]:
    found = {}
    for path in glob.glob("spec/TMF*"):
        name = os.path.basename(path)[:6]
        if name in NO_HUB:
            continue
        with open(path) as f:
            spec = json.load(f) if path.endswith(".json") else yaml.safe_load(f)
        found[name] = {
            p.rsplit("/", 1)[1][0].upper() + p.rsplit("/", 1)[1][1:]
            for p in spec["paths"]
            if p.startswith("/listener/")
        }
    return found


def test_the_event_types_are_the_specs_listener_events():
    # P4c: TMF654's hub subscribes to all twelve names before the actions emit them.
    assert {api: set(types) for api, types in EVENT_TYPES.items()} == spec_events()
    assert len(EVENT_TYPES["TMF654"]) == 12


@pytest.mark.parametrize("name", APIS)
def test_create_and_delete_a_hub(api, name):
    base = BASES[name]
    created = api.post(
        f"{base}/hub", {"callback": CALLBACK, "query": "eventType=" + min(EVENT_TYPES[name])}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["callback"] == CALLBACK and body["query"].startswith("eventType=")
    if name in ("TMF676", "TMF654", "TMF670"):  # v4 EventSubscription: no href, no @type
        assert set(body) == {"id", "callback", "query"}
    else:
        assert body["href"] == f"{base}/hub/{body['id']}" == created.headers["location"]
        assert body["@type"] == "Hub"
    href = f"{base}/hub/{body['id']}"
    expected_get = 200 if name in {"TMF629", "TMF639"} else 405
    assert api.request("GET", href).status_code == expected_get
    assert api.request("GET", f"{base}/hub").status_code == 405
    assert api.delete(f"{href}?x=1").status_code == 400
    assert api.delete(href).status_code == 204
    assert api.delete(href).status_code == 404  # deleted
    assert api.delete(f"{base}/hub/{apidata.NIL}").status_code == 404
    assert api.delete(f"{base}/hub/invalid").status_code == 404


def test_a_hub_belongs_to_its_api(api, db_conn):
    ordering = hub(api, "TMF622")
    for other in ("TMF620", "TMF629"):
        assert api.delete(f"{BASES[other]}/hub/{ordering['id']}").status_code == 404
    assert api.request("GET", f"{BASES['TMF629']}/hub/{ordering['id']}").status_code == 404
    customer = hub(api, "TMF629")
    fetched = api.get(customer["href"])
    assert fetched.json() == customer
    assert api.get(f"{customer['href']}?fields=query").json() == {
        "id": customer["id"],
        "href": customer["href"],
        "@type": "Hub",
    }
    assert api.delete(customer["href"]).status_code == 204
    assert api.request("GET", customer["href"]).status_code == 404
    row = db_conn.execute(
        "SELECT api, deleted_at IS NOT NULL FROM event_subscription WHERE id = %s",
        (customer["id"],),
    ).fetchone()
    assert row == ("TMF629", True)  # kept, marked deleted


@pytest.mark.parametrize(
    "query",
    [
        "eventType=ProductOrderCreateEvent",  # another API's event
        "eventType=CustomerCreateEvent,CustomerCreateEvent",
        "eventType=",
        "type=CustomerCreateEvent",
        "eventType=CustomerCreateEvent&id=1",
        "CustomerCreateEvent",
    ],
)
def test_only_an_event_type_query_of_the_apis_own_events(api, query):
    response = api.post(f"{BASES['TMF629']}/hub", {"callback": CALLBACK, "query": query})
    assert response.status_code == 400, response.text
    assert "query" in response.json()["message"]


def test_hub_body_validation(api, db_conn):
    base = BASES["TMF632"]
    assert api.post(f"{base}/hub", {}).status_code == 422
    assert api.post(f"{base}/hub", {"callback": ""}).status_code == 422
    assert api.post(f"{base}/hub", {"callback": CALLBACK, "id": "x"}).status_code == 400
    assert api.post(f"{base}/hub", {"callback": CALLBACK, "secret": "x"}).status_code == 400
    assert api.post(f"{base}/hub", {"callback": CALLBACK, "@type": "Individual"}).status_code == 400
    assert api.post(f"{base}/hub", {"callback": "ftp://listener.test/x"}).status_code == 422
    two = hub(api, "TMF632", query="eventType=IndividualCreateEvent,OrganizationDeleteEvent")
    stored = db_conn.execute(
        "SELECT event_types FROM event_subscription WHERE id = %s", (two["id"],)
    ).fetchone()[0]
    assert stored == ["IndividualCreateEvent", "OrganizationDeleteEvent"]
    assert db_conn.execute("SELECT count(*) FROM event_subscription").fetchone()[0] == 1


def public(*addresses):
    return lambda host, port: list(addresses)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.18.0.5",  # a Docker network
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",  # noqa: S104 - an address to refuse, nothing binds to it
        "::1",
        "::ffff:127.0.0.1",
        "fd00::1",
        "fe80::1%eth0",
        "224.0.0.1",
    ],
)
def test_outside_dev_a_callback_must_lead_to_public_addresses_only(address):
    with pytest.raises(callbacks.CallbackRefused, match="not a public address"):
        callbacks.check(
            "https://listener.example/x",
            resolver=public("93.184.216.34", address),
            allow_private=False,
        )


def test_outside_dev_a_callback_must_be_https_and_resolve():
    ok = callbacks.check(
        "https://listener.example:8443/x?a=1", resolver=public("93.184.216.34"), allow_private=False
    )
    assert ok == ["93.184.216.34"]
    with pytest.raises(callbacks.CallbackRefused, match="must use https"):
        callbacks.check(
            "http://listener.example/x", resolver=public("93.184.216.34"), allow_private=False
        )
    with pytest.raises(callbacks.CallbackRefused, match="does not resolve"):
        callbacks.check("https://listener.example/x", resolver=public(), allow_private=False)
    for url, problem in [
        ("https://user:pw@listener.example/x", "credentials"),
        ("https://listener.example/x#frag", "fragment"),
        ("https://listener.example:99999/x", "invalid port"),
        ("listener.example/x", "absolute"),
        ("javascript:alert(1)", "absolute"),
    ]:
        with pytest.raises(callbacks.CallbackRefused, match=problem):
            callbacks.check(url, resolver=public("93.184.216.34"), allow_private=False)


def test_staging_refuses_a_local_callback_through_the_api(api, monkeypatch):
    """The VPS runs APP_ENV=staging: only dev and test may call private addresses."""
    monkeypatch.setattr(settings, "app_env", "staging")
    response = api.post(f"{BASES['TMF629']}/hub", {"callback": "http://bss-app:8080/x"})
    assert response.status_code == 422
    assert "https" in response.json()["message"]
    monkeypatch.setattr(callbacks, "resolve", public("172.18.0.3"))
    response = api.post(f"{BASES['TMF629']}/hub", {"callback": "https://bss-app/x"})
    assert response.status_code == 422
    assert "not a public address" in response.json()["message"]
    monkeypatch.setattr(callbacks, "resolve", public("93.184.216.34"))
    assert (
        api.post(f"{BASES['TMF629']}/hub", {"callback": "https://listener.example/x"}).status_code
        == 201
    )


def test_mcp_hub_tools_need_the_write_scope(api, mcp):
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    tools = mcp.tools(reader)
    assert "create_hub_ordering" not in tools and "get_hub_customer" in tools
    response = mcp.call(reader, "create_hub_ordering", {"body": {"callback": CALLBACK}})
    assert response.json()["error"] == "insufficient_scope"
    error, answer = mcp.result(writer, "create_hub_ordering", {"body": {"callback": CALLBACK}})
    assert not error and answer["status"] == 201
    error, answer = mcp.result(writer, "delete_hub_ordering", {"hub_id": answer["body"]["id"]})
    assert not error and answer == {"status": 204}
