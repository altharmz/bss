"""TMF622 PATCH productOrder: the attributes that describe an order, nothing processing
decided. POST accepts the same ones."""

from datetime import timedelta

import pytest

from tests import apidata, timeline

EXTERNAL = [{"id": "SO-1001", "owner": "Shop", "externalIdentifierType": "SalesOrder"}]


@pytest.fixture
def placed(api):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    return apidata.order(
        api,
        account,
        apidata.add_item(offering),
        description="Fiber for the flat",
        category="residential",
        priority="2",
        externalId=EXTERNAL,
    )


def test_post_stores_the_descriptive_attributes(api, placed):
    assert placed["state"] == "completed"
    assert (placed["description"], placed["category"], placed["priority"]) == (
        "Fiber for the flat",
        "residential",
        "2",
    )
    assert placed["externalId"] == [{**EXTERNAL[0], "@type": "ExternalIdentifier"}]
    assert api.get(placed["href"]).json() == placed


def test_patch_changes_and_removes_them_only(api, placed):
    response = api.patch(placed["href"], {"priority": "0", "category": None})
    assert response.status_code == 200, response.text
    patched = response.json()
    assert patched["priority"] == "0" and "category" not in patched
    assert (
        patched["state"] == "completed"
        and patched["productOrderItem"] == placed["productOrderItem"]
    )
    cleared = api.patch(placed["href"], {"externalId": None, "description": None}).json()
    assert "externalId" not in cleared and "description" not in cleared
    assert api.patch(placed["href"], {}).json() == cleared  # nothing to change


@pytest.mark.parametrize(
    "body,status",
    [
        ({"state": "cancelled"}, 422),  # the server's: repeated, never set
        ({"state": "acknowledged"}, 422),  # only a draft is submitted
        ({"productOrderItem": []}, 422),
        # what the order was placed with: a draft's to change, fixed once processed
        ({"requestedStartDate": "2026-12-01T00:00:00Z"}, 409),
        ({"billingAccount": {"id": apidata.NIL}}, 409),
        ({"note": [{"text": "x"}]}, 422),  # a Note needs its @type (the spec's schema)
        ({"zzUnknown": 1}, 400),
        ({"priority": "5"}, 422),
        ({"priority": 1}, 422),
        ({"description": ""}, 422),
        ({"externalId": [{"owner": "Shop"}]}, 422),
    ],
)
def test_patch_refuses_what_processing_decided_and_bad_values(api, placed, body, status):
    assert api.patch(placed["href"], body).status_code == status
    assert api.get(placed["href"]).json() == placed


def test_patch_of_a_missing_order_and_of_a_rejected_one(api, placed):
    assert api.patch(f"{apidata.ORDER}/{apidata.NIL}", {"priority": "1"}).status_code == 404
    account = apidata.account(api, name="Other")
    rejected = apidata.order(
        api,
        account,
        {
            **apidata.add_item({"id": placed["productOrderItem"][0]["productOffering"]["id"]}),
            "action": "modify",
        },
    )
    assert rejected["state"] == "rejected"
    assert api.patch(rejected["href"], {"description": "kept for the record"}).status_code == 200


def test_mcp_patch_order_needs_the_write_scope(api, mcp, placed):
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    assert "patch_order" not in mcp.tools(reader)
    error, answer = mcp.result(
        writer, "patch_order", {"order_id": placed["id"], "body": {"priority": "4"}}
    )
    assert not error and answer["status"] == 200 and answer["body"]["priority"] == "4"
