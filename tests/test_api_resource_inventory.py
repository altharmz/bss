"""TMF639 inventory: schema-checked bodies, retained history and transactional events."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.config import settings
from tests.apiclient import total
from tests.factories import world
from tests.test_events import stored, subscribe
from tests.test_number_persistence import assignment
from tests.timeline import TODAY, midnight

BASE = "/tmf-api/resourceInventory/v5"
URL = BASE + "/resource"


def number(api, value="12025550123", **extra):
    response = api.post(
        URL, {"@type": "LogicalResource", "name": "Number", "value": value, **extra}
    )
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()


def test_number_crud_and_immutable_identity(api):
    uid = str(uuid4())
    created = number(
        api,
        id=uid,
        description="Stock",
        **{
            "@baseType": "Resource",
            "@schemaLocation": "https://example.test/number.json",
            "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"},
        },
    )
    assert created["id"] == uid and created["usageState"] == "idle"
    assert created["allocationStatus"] == "available"
    assert created["resourceSpecification"] == {"id": "msisdn", "@type": "ResourceSpecificationRef"}
    assert created["@baseType"] == "Resource"
    assert api.get(created["href"]).json() == created
    edited = api.patch(
        created["href"],
        {
            "name": "Reserved stock",
            "description": None,
            "administrativeState": "locked",
            "value": created["value"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert "description" not in edited.json()
    assert edited.json()["administrativeState"] == "locked"
    for body, status in [
        ({"value": "12025550124"}, 409),
        ({"resourceSpecification": {"id": "other", "@type": "ResourceSpecificationRef"}}, 422),
        (
            {
                "resourceSpecification": {
                    "id": "msisdn",
                    "@type": "ResourceSpecificationRef",
                    "zzProbe": True,
                }
            },
            400,
        ),
        ({"resourceSpecification": {"id": "msisdn", "href": "/fake"}}, 422),
        ({"usageState": "busy"}, 422),
        ({"allocationStatus": "allocated"}, 422),
        ({"creationDate": "2020-01-01T00:00:00Z"}, 422),
        ({"administrativeState": "shuttingDown"}, 422),
        ({"name": None}, 422),
        ({"value": None}, 422),
        ({"administrativeState": None}, 422),
        ({"@type": "PhysicalResource"}, 400),
        ({"id": str(uuid4())}, 400),
        ({"zzProbe": True}, 400),
    ]:
        result = api.patch(created["href"], body)
        assert result.status_code == status, result.text
    assert (
        api.patch(
            created["href"],
            {
                key: created[key]
                for key in (
                    "creationDate",
                    "usageState",
                    "allocationStatus",
                    "resourceSpecification",
                )
            },
        ).status_code
        == 200
    )
    assert api.delete(created["href"]).status_code == 204
    assert api.get(created["href"]).status_code == 404
    assert api.patch(created["href"], {}).status_code == 404
    assert api.delete(created["href"]).status_code == 404


@pytest.mark.parametrize(
    "value",
    [
        "+12025550123",
        "120 25550123",
        "012345678",
        "1234567",
        "1" * 16,
        "１２３４５６７８",
        "12025550123\n",
        12025550123,
    ],
)
def test_invalid_number_values_are_not_normalized(api, value):
    assert api.post(URL, {"name": "Bad", "value": value}).status_code == 422
    assert api.get(URL).json() == []


def test_duplicate_and_failed_creation_leave_no_event(api, db_conn):
    subscribe(api, "TMF639")
    created = number(api)
    assert api.post(URL, {"name": "Duplicate", "value": created["value"]}).status_code == 409
    assert (
        api.post(
            URL, {"id": created["id"], "name": "Duplicate", "value": "12025550124"}
        ).status_code
        == 409
    )
    assert (
        api.post(
            URL, {"name": "Bad state", "value": "12025550124", "usageState": "busy"}
        ).status_code
        == 422
    )
    assert len(api.get(URL).json()) == 1
    assert [kind for kind, _ in stored(db_conn, "TMF639")] == ["ResourceCreateEvent"]


def test_descriptive_documents_validate_at_depth_and_round_trip(api):
    documents = {
        "note": [{"@type": "Note", "text": "Ported", "date": "2026-01-01T03:00:00+03:00"}],
        "resourceCharacteristic": [
            {"@type": "StringCharacteristic", "name": "Region", "value": "US"}
        ],
        "externalIdentifier": [
            {
                "@type": "ExternalIdentifier",
                "owner": "import",
                "externalIdentifierType": "legacy",
                "id": "n1",
            }
        ],
        "validFor": {"startDateTime": "2026-01-01T00:00:00Z"},
    }
    created = number(api, **documents)
    assert created["note"][0]["date"] == "2026-01-01T00:00:00Z"
    assert created["resourceCharacteristic"] == documents["resourceCharacteristic"]
    for body, expected in [
        ({"note": [{"@type": "Note", "zzProbe": 1}]}, 400),
        ({"note": [{"@type": "Note", "date": "2026-01-01"}]}, 422),
        (
            {
                "resourceCharacteristic": [
                    {"@type": "StringCharacteristic", "name": "Region", "value": 5}
                ]
            },
            422,
        ),
    ]:
        response = api.patch(created["href"], body)
        assert response.status_code == expected, response.text
    response = api.patch(created["href"], {"note": None, "@baseType": None})
    assert response.status_code == 200 and "note" not in response.json()


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("application/json", {"description": "Edited"}),
        ("application/merge-patch+json", {"description": "Edited"}),
        ("application/json-patch+json", [{"op": "add", "path": "/description", "value": "Edited"}]),
        (
            "application/json-patch-query+json",
            [{"op": "add", "path": "/description", "value": "Edited"}],
        ),
    ],
)
def test_all_declared_patch_formats(api, content_type, body):
    created = number(api)
    response = api.patch(created["href"], body, content_type=content_type)
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Edited"


def test_fields_filters_and_pagination(api):
    first = number(api)
    second = number(api, "12025550124", administrativeState="locked")
    third = number(api, "12025550125")
    ordered = sorted([first, second, third], key=lambda row: row["id"])
    for field, value, expected in [
        ("value", first["value"], [first]),
        ("id", second["id"], [second]),
        ("administrativeState", "locked", [second]),
        ("usageState", "idle", ordered),
        ("allocationStatus", "available", ordered),
        ("resourceSpecification.id", "msisdn", ordered),
        ("administrativeState", "unknown", []),
    ]:
        result = api.get(URL + f"?{field}={value}")
        assert result.status_code == 200, result.text
        assert result.json() == expected
        assert int(total(result, api)) == len(expected)
    response = api.get(URL + "?offset=1&limit=1&fields=value")
    assert response.json() == [{key: ordered[1][key] for key in ("id", "href", "@type", "value")}]
    assert total(response, api) == "3"
    assert response.headers["X-Result-Count"] == "1"
    assert set(api.get(first["href"] + "?fields=value").json()) == {"id", "href", "@type", "value"}
    for query in (
        "fields=zzProbe",
        "limit=101",
        "zzProbe=1",
        "offset=-1",
        "fields=value&fields=name",
    ):
        assert api.get(URL + "?" + query).status_code == 400
    assert api.delete(first["href"] + "?fields=value").status_code == 400


def test_effective_dates_quarantine_future_claims_and_retained_history(api, db_conn, monkeypatch):
    created = number(api)
    w = world(db_conn)
    start, release = TODAY - timedelta(days=2), TODAY
    uid = assignment(db_conn, created["id"], w.product, start=start, end=release)
    db_conn.commit()
    monkeypatch.setattr(settings, "msisdn_quarantine_days", 2)
    assert api.get(created["href"]).json()["allocationStatus"] == "quarantined"
    api.clock.set(midnight(TODAY + timedelta(days=2)))
    assert api.get(created["href"]).json()["allocationStatus"] == "available"
    api.clock.set(midnight(start))
    current = api.get(created["href"]).json()
    assert current["usageState"] == "busy" and current["allocationStatus"] == "allocated"
    locked = api.patch(created["href"], {"administrativeState": "locked"}).json()
    assert locked["usageState"] == "busy"
    api.clock.set(midnight(start - timedelta(days=1)))
    assert api.get(created["href"]).json()["allocationStatus"] == "reserved"
    for state, date in [
        ("reserved", start - timedelta(days=1)),
        ("allocated", start),
        ("quarantined", release),
        ("available", release + timedelta(days=2)),
    ]:
        api.clock.set(midnight(date))
        response = api.get(URL + f"?allocationStatus={state}&limit=1")
        assert total(response, api) == "1", response.text
        assert response.json()[0] == api.get(created["href"]).json()
    assert api.delete(created["href"]).status_code == 409
    db_conn.execute(
        "UPDATE number_assignment SET cancelled_at = %s,"
        " cancelled_by_item_id = assigned_by_item_id WHERE id = %s",
        (api.clock.now(), uid),
    )
    db_conn.commit()
    api.clock.set(midnight(start))
    assert api.get(created["href"]).json()["allocationStatus"] == "available"
    assert api.delete(created["href"]).status_code == 409  # cancelled audit also retained


def test_outbox_changes_and_noops(api, db_conn):
    subscribe(api, "TMF639")
    created = number(api)
    for body in (
        {"administrativeState": "locked"},
        {"description": "Edited"},
        {"administrativeState": "unlocked", "name": "Ready"},
        {"name": "Ready"},
    ):
        assert api.patch(created["href"], body).status_code == 200
    assert api.delete(created["href"]).status_code == 204
    events = stored(db_conn, "TMF639")
    assert [kind for kind, _ in events] == [
        "ResourceCreateEvent",
        "ResourceStateChangeEvent",
        "ResourceAttributeValueChangeEvent",
        "ResourceStateChangeEvent",
        "ResourceAttributeValueChangeEvent",
        "ResourceDeleteEvent",
    ]
    assert events[-1][1]["event"]["resource"]["name"] == "Ready"
