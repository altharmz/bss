"""Catalog navigation, atomic membership changes and hierarchy invariants."""

import threading
from uuid import UUID

import psycopg
import pytest

from app.api.classification import CategoryPatch, save
from tests.test_api_catalog import BASE, NIL, make_offering
from tests.timeline import START, iso

CATEGORY = f"{BASE}/category"
CATALOG = f"{BASE}/productCatalog"


def category(api, **over):
    response = api.post(CATEGORY, {"name": "Internet", **over})
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()


def test_catalog_category_offering_navigation(api):
    offering = make_offering(api)
    root = category(api)
    child = category(
        api, name="Fiber", parent={"id": root["id"]}, productOffering=[{"id": offering["id"]}]
    )
    response = api.post(
        CATALOG,
        {
            "@type": "ProductCatalog",
            "name": "Home",
            "catalogType": "Product",
            "category": [{"id": root["id"]}],
            "validFor": {"startDateTime": iso(START)},
            "version": "1",
        },
    )
    assert response.status_code == 201, response.text
    catalog = response.json()
    assert response.headers["location"] == catalog["href"]
    assert api.get(catalog["href"]).json() == catalog
    assert catalog["category"][0]["id"] == root["id"]
    assert api.get(root["href"]).json()["subCategory"][0]["id"] == child["id"]
    assert child["parent"]["id"] == root["id"] and child["isRoot"] is False
    assert child["productOffering"][0]["href"] == offering["href"]
    assert api.get(offering["href"]).json()["category"][0]["id"] == child["id"]
    assert api.patch(child["href"], {"productOffering": []}).status_code == 200
    assert api.get(offering["href"]).json()["category"] == []
    assert api.patch(catalog["href"], {"category": []}).json()["category"] == []


def test_hierarchy_cycles_and_catalog_roots(api):
    root = category(api)
    child = category(api, parent={"id": root["id"]})
    for parent in (root, child):
        response = api.patch(root["href"], {"parent": {"id": parent["id"]}})
        assert response.status_code == 409
        assert "cycle" in response.json()["message"]
    assert api.post(CATALOG, {"name": "Bad", "category": [{"id": child["id"]}]}).status_code == 409
    catalog = api.post(CATALOG, {"name": "Good", "category": [{"id": root["id"]}]}).json()
    other = category(api)
    assert api.patch(root["href"], {"parent": {"id": other["id"]}}).status_code == 409
    assert api.patch(catalog["href"], {"category": []}).status_code == 200
    assert api.patch(root["href"], {"parent": {"id": other["id"]}}).status_code == 200
    assert api.patch(root["href"], {"parent": None, "isRoot": True}).json()["isRoot"] is True
    assert api.get(other["href"]).json()["subCategory"] == []
    assert api.patch(child["href"], {"isRoot": True}).status_code == 422


@pytest.mark.parametrize(
    "path,typ,links",
    [(CATEGORY, "Category", "productOffering"), (CATALOG, "ProductCatalog", "category")],
)
def test_organization_validation_fields_and_lifecycle(api, path, typ, links):
    assert api.post(path, {"name": "x", "@type": "Wrong"}).status_code == 400
    assert api.post(path, {"name": "x", links: [{"id": NIL}]}).status_code == 422
    assert api.get(path).json() == []
    response = api.post(f"{path}?fields=name", {"name": "x"})
    assert response.status_code == 201
    assert set(response.json()) == {"id", "href", "@type", "name"}
    href = response.json()["href"]
    assert api.patch(href, {"lifecycleStatus": "Active", "description": "hello"}).status_code == 200
    before = api.get(href).json()
    assert api.patch(href, {"name": "lost", links: [{"id": NIL}]}).status_code == 422
    assert api.get(href).json() == before
    assert api.patch(href, {"lifecycleStatus": "In Design"}).status_code == 409
    assert api.patch(href, {"lifecycleStatus": "Retired"}).status_code == 200
    assert api.patch(href, {"lifecycleStatus": "Active"}).status_code == 409
    for body, status in [
        ({"name": None}, 422),
        ({links: None}, 422),
        ({"lastUpdate": "x"}, 422),  # the server's: repeated, never set
        ({"unknown": 1}, 400),
    ]:
        assert api.patch(href, body).status_code == status
    assert api.patch(href, {"description": None, "validFor": None}).status_code == 200
    assert "description" not in api.get(href).json()
    assert api.get(f"{path}/{NIL}").status_code == 404
    assert api.patch(f"{path}/{NIL}", {"name": "x"}).status_code == 404
    assert api.get(f"{path}/invalid").status_code == 404
    assert api.delete(f"{path}/{NIL}").status_code == 404
    assert api.delete(f"{path}/invalid").status_code == 404
    assert api.delete(path).status_code == 405
    assert api.get(f"{path}?unknown=1").status_code == 400
    assert api.post(f"{path}?unknown=1", {"name": "x"}).status_code == 400
    assert api.get(f"{path}?name=x&fields=name&limit=1").headers["X-Total-Count"] == "1"
    assert api.get(f"{path}?offset=1").json() == []
    assert api.get(f"{path}?fields=unknown").status_code == 400


def test_duplicate_and_wrong_type_references_are_atomic(api):
    offering = make_offering(api)
    root = category(api)
    for path, field, target in [
        (CATEGORY, "productOffering", offering),
        (CATALOG, "category", root),
    ]:
        assert (
            api.post(path, {"name": "duplicate", field: [{"id": target["id"]}] * 2}).status_code
            == 422
        )
        assert (
            api.post(
                path, {"name": "wrong", field: [{"id": target["id"], "@referredType": "Customer"}]}
            ).status_code
            == 422
        )
    assert api.post(CATEGORY, {"name": "x", "parent": {"id": NIL}}).status_code == 422
    # subCategory is derived from the children: a new category has none
    assert api.post(CATEGORY, {"name": "x", "subCategory": [{"id": NIL}]}).status_code == 422
    assert api.post(CATEGORY, {"name": "x", "isRoot": False}).status_code == 422
    assert len(api.get(CATEGORY).json()) == 1
    assert api.get(CATALOG).json() == []


def test_concurrent_reparenting_cannot_create_a_cycle(api, migrated_db_url):
    first, second = category(api, name="First"), category(api, name="Second")
    result = {}
    with psycopg.connect(migrated_db_url) as conn:
        save(
            conn,
            CategoryPatch.model_validate({"parent": {"id": second["id"]}}),
            True,
            UUID(first["id"]),
        )
        thread = threading.Thread(
            target=lambda: result.update(
                response=api.patch(second["href"], {"parent": {"id": first["id"]}})
            )
        )
        thread.start()
        try:
            thread.join(timeout=1)
            assert thread.is_alive(), "second reparenting must wait for the first transaction"
        finally:
            conn.commit()
            thread.join(timeout=10)
    assert not thread.is_alive()
    assert result["response"].status_code == 409
    assert api.get(second["href"]).json()["isRoot"] is True


def test_membership_replacement_and_filters(api):
    offering, other_offering = make_offering(api), make_offering(api)
    root = category(api, productOffering=[{"id": offering["id"]}])
    other = category(api, name="Other", productOffering=[{"id": offering["id"]}])
    child = category(api, name="Child", parent={"id": root["id"]})
    first = api.post(
        CATALOG,
        {
            "name": "Retail",
            "catalogType": "Product",
            "category": [{"id": root["id"]}, {"id": other["id"]}],
        },
    ).json()
    second = api.post(CATALOG, {"name": "Wholesale", "category": [{"id": other["id"]}]}).json()
    replaced = api.patch(first["href"], {"category": [{"id": other["id"]}]})
    assert replaced.status_code == 200
    assert replaced.json()["lastUpdate"] > first["lastUpdate"]
    assert api.get(second["href"]).json() == second
    assert (
        api.patch(
            other["href"], {"productOffering": [{"id": other_offering["id"]}], "name": "Renamed"}
        ).status_code
        == 200
    )
    assert [c["id"] for c in api.get(offering["href"]).json()["category"]] == [root["id"]]
    assert api.get(first["href"]).json()["category"][0]["name"] == "Renamed"
    assert api.get(other_offering["href"]).json()["category"][0]["name"] == "Renamed"
    assert api.get(f"{CATEGORY}?parent.id={root['id']}").json()[0]["id"] == child["id"]
    assert api.get(f"{CATEGORY}?lifecycleStatus=Retired").json() == []
    assert api.get(f"{CATALOG}?catalogType=Product").json()[0]["id"] == first["id"]
    assert api.get(f"{CATALOG}?catalogType=Other").json() == []
    assert api.patch(offering["href"], {"category": []}).status_code == 400


@pytest.mark.parametrize("path", [CATEGORY, CATALOG])
@pytest.mark.parametrize(
    "period",
    [
        {},
        {"startDateTime": "2026-01-02T00:00:00Z", "endDateTime": "2026-01-01T00:00:00Z"},
        {"startDateTime": "2026-01-01T00:00:00"},
    ],
)
def test_invalid_organization_validity_is_atomic(api, path, period):
    assert api.post(path, {"name": "Invalid", "validFor": period}).status_code == 422
    assert api.get(path).json() == []
