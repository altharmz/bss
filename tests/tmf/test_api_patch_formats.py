"""PATCH in the four formats every v5 spec lists (docs/decisions/tmf-api.md, "PATCH
formats"): application/json and application/merge-patch+json are merge patches; JSON
Patch (RFC 6902) and TMF630's JSON Patch Query are applied to the current resource and
then go through the same rules as a merge patch."""

from datetime import timedelta

import pytest

from tests import apidata, timeline

JSON_PATCH = "application/json-patch+json"
QUERY = "application/json-patch-query+json"
FINANCIAL = "/tmf-api/accountManagement/v5/financialAccount"


@pytest.fixture
def account(api):
    org = apidata.organization(api)
    owner = {"role": "owner", "partyOrPartyRole": {"id": org["id"]}}
    response = api.post(FINANCIAL, {"name": "Main", "relatedParty": [owner]})
    assert response.status_code == 201
    return response.json()


def patch(api, href, ops, content_type=JSON_PATCH):
    return api.patch(href, ops, content_type=content_type)


def test_application_json_is_a_merge_patch(api, account):
    response = api.patch(account["href"], {"description": "x"}, content_type="application/json")
    assert response.status_code == 200 and response.json()["description"] == "x"
    response = api.patch(account["href"], {"description": None}, content_type="application/json")
    assert "description" not in response.json()


def test_json_patch_add_replace_remove_copy_move(api, account):
    href = account["href"]
    added = patch(api, href, [{"op": "add", "path": "/description", "value": "d"}])
    assert added.status_code == 200 and added.json()["description"] == "d"
    copied = patch(api, href, [{"op": "copy", "from": "/name", "path": "/accountType"}])
    assert copied.json()["accountType"] == "Main"
    moved = patch(api, href, [{"op": "move", "from": "/accountType", "path": "/description"}])
    assert moved.json()["description"] == "Main" and "accountType" not in moved.json()
    replaced = patch(
        api,
        href,
        [
            {"op": "replace", "path": "/name", "value": "Renamed"},
            {"op": "remove", "path": "/description"},
        ],
    )
    assert replaced.json()["name"] == "Renamed" and "description" not in replaced.json()
    assert api.get(href).json() == replaced.json()


def test_a_failed_test_operation_changes_nothing(api, account):
    href = account["href"]
    ops = [
        {"op": "test", "path": "/name", "value": "Other"},
        {"op": "replace", "path": "/name", "value": "X"},
    ]
    response = patch(api, href, ops)
    assert response.status_code == 409 and "the value differs" in response.json()["message"]
    ops[0]["value"] = "Main"
    assert patch(api, href, ops).json()["name"] == "X"


@pytest.mark.parametrize(
    ("ops", "status"),
    [
        ({"op": "replace"}, 400),  # not an array
        (["replace"], 400),  # an operation that is not an object
        ([{"op": "remove", "path": 5}], 400),  # a path that is not a string
        ([{"op": "upsert", "path": "/name"}], 400),
        ([{"op": "replace", "path": "name", "value": "x"}], 400),  # not a pointer
        ([{"op": "add", "path": "/name"}], 400),  # no value
        ([{"op": "copy", "path": "/name"}], 400),  # no from
        ([{"op": "remove", "path": "/description"}], 422),  # nothing there
        ([{"op": "test", "path": "/description", "value": "x"}], 422),  # nothing to test
        ([{"op": "add", "path": "/name/x", "value": 1}], 422),  # the parent is a string
        ([{"op": "test", "path": "/relatedParty", "value": []}], 409),  # another array
        ([{"op": "replace", "path": "/creditLimit/value", "value": 1}], 422),
        ([{"op": "add", "path": "/relatedParty/5", "value": {}}], 422),  # out of range
        ([{"op": "move", "from": "/relatedParty", "path": "/relatedParty/0/x"}], 422),
        ([{"op": "replace", "path": "", "value": []}], 422),  # no longer an object
        # the same rules as a merge patch: read-only, server-set, unknown, invalid
        ([{"op": "replace", "path": "/id", "value": apidata.NIL}], 400),
        ([{"op": "replace", "path": "/lastUpdate", "value": "2020-01-01T00:00:00Z"}], 422),
        ([{"op": "add", "path": "/colour", "value": "red"}], 400),
        ([{"op": "replace", "path": "/state", "value": "Open"}], 422),
    ],
)
def test_bad_json_patches_change_nothing(api, account, ops, status):
    response = patch(api, account["href"], ops)
    assert response.status_code == status, response.text
    assert api.get(account["href"]).json() == account


def test_test_operations_compare_objects_and_arrays_as_json(api, account):
    href, party = account["href"], account["relatedParty"]
    ops = [
        {"op": "test", "path": "/relatedParty", "value": party},
        {"op": "test", "path": "/relatedParty/0", "value": party[0]},
        {"op": "replace", "path": "/name", "value": "Checked"},
    ]
    response = patch(api, href, ops)
    assert response.status_code == 200, response.text
    assert api.get(href).json()["name"] == "Checked"
    other = {**party[0], "role": "payer"}
    ops[1]["value"], ops[2]["value"] = other, "Not saved"
    response = patch(api, href, ops)
    assert response.status_code == 409 and "the value differs" in response.json()["message"]
    assert api.get(href).json()["name"] == "Checked"


def test_json_patch_of_a_missing_resource_is_a_404(api):
    ops = [{"op": "replace", "path": "/name", "value": "x"}]
    assert patch(api, f"{FINANCIAL}/{apidata.NIL}", ops).status_code == 404


@pytest.fixture
def order(api):
    offering = apidata.sellable_offering(api)
    acc = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    external = [
        {"id": "A", "owner": "Shop", "externalIdentifierType": "SalesOrder"},
        {"id": "B", "owner": "Shop", "externalIdentifierType": "SalesOrder"},
        {"id": "C", "owner": "CRM", "externalIdentifierType": "Case"},
    ]
    return apidata.order(api, acc, apidata.add_item(offering), externalId=external)


def test_json_patch_query_changes_the_selected_elements(api, order):
    href = order["href"]
    ops = [{"op": "replace", "path": "/externalId/owner?owner=Shop", "value": "Web shop"}]
    changed = patch(api, href, ops, QUERY)
    assert changed.status_code == 200, changed.text
    assert [e["owner"] for e in changed.json()["externalId"]] == ["Web shop", "Web shop", "CRM"]
    two = [{"op": "remove", "path": "/externalId?owner=Web shop&id=B"}]
    removed = patch(api, href, two, QUERY).json()["externalId"]
    assert [e["id"] for e in removed] == ["A", "C"]
    added = [{"op": "add", "path": "/externalId/owner?id=C", "value": "Support"}]
    assert patch(api, href, added, QUERY).json()["externalId"][1]["owner"] == "Support"
    assert api.get(href).json()["externalId"] == patch(api, href, [], QUERY).json()["externalId"]


def test_json_patch_add_at_an_index_inserts_before_it(api, order):
    new = {"id": "Z", "owner": "Shop", "externalIdentifierType": "SalesOrder"}
    response = patch(api, order["href"], [{"op": "add", "path": "/externalId/0", "value": new}])
    assert response.status_code == 200, response.text
    assert [e["id"] for e in api.get(order["href"]).json()["externalId"]] == ["Z", "A", "B", "C"]


@pytest.mark.parametrize(
    "op",
    [
        # move and copy take plain pointers only
        {"op": "copy", "from": "/externalId/0/owner", "path": "/externalId/owner?id=A"},
        {"op": "copy", "from": "/externalId/owner?id=A", "path": "/description"},
    ],
)
def test_json_patch_query_move_and_copy_reject_a_query(api, order, op):
    response = patch(api, order["href"], [op], QUERY)
    assert response.status_code == 422, response.text
    assert "query" in response.json()["message"]
    assert api.get(order["href"]).json() == order


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/externalId/owner?owner=Nobody", 422),  # selects nothing
        ("/state?x=1", 422),  # the path ends at a string, not an array
        # an object never equals a query value, so nothing is selected
        ("/productOrderItem/action?productOffering=x", 422),
        ("/description?x=1", 422),  # no array on the path
        ("/externalId/owner?owner", 400),  # not attr=value
    ],
)
def test_json_patch_query_errors(api, order, path, status):
    response = patch(api, order["href"], [{"op": "replace", "path": path, "value": "x"}], QUERY)
    assert response.status_code == status, response.text
    assert api.get(order["href"]).json() == order


def test_a_query_is_part_of_the_key_in_plain_json_patch(api, order):
    ops = [{"op": "replace", "path": "/externalId/owner?owner=Shop", "value": "x"}]
    assert patch(api, order["href"], ops).status_code == 422  # no such location


def test_json_patch_query_matches_booleans_as_true_and_false(api):
    """The vendored specs' own example selects contact media by `preferred`."""
    media = [
        {"@type": "EmailContactMedium", "preferred": True, "emailAddress": "a@example.org"},
        {"@type": "EmailContactMedium", "preferred": False, "emailAddress": "b@example.org"},
    ]
    body = {"@type": "Individual", "givenName": "Jane", "familyName": "Doe"}
    person = api.post(f"{apidata.PARTY}/individual", {**body, "contactMedium": media}).json()
    href = person["href"]
    ops = [
        {"op": "test", "path": "/contactMedium/0/preferred", "value": True},
        {"op": "add", "path": "/contactMedium/contactType?preferred=true", "value": "home"},
    ]
    response = patch(api, href, ops, QUERY)
    assert response.status_code == 200, response.text
    stored = api.get(href).json()["contactMedium"]
    assert [m.get("contactType") for m in stored] == ["home", None]
    # a JSON true is never the number 1
    one = [{"op": "test", "path": "/contactMedium/0/preferred", "value": 1}]
    assert patch(api, href, one).status_code == 409


def test_malformed_json_in_a_json_patch_is_a_400(api, account):
    response = api.request(
        "PATCH", account["href"], content=b"[{", headers={"Content-Type": JSON_PATCH}
    )
    assert response.status_code == 400, response.text
    assert response.json()["message"].startswith("malformed JSON: ")
    assert api.get(account["href"]).json() == account
