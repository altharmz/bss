"""TMF620 importJob and exportJob: the catalog as a document in a data: URL.

Export produces the document; import applies it under POST's rules, keeping ids, all or
nothing. A failed import is stored (status Failed, errorLog) with nothing written.
"""

import base64
import json
from datetime import timedelta

import pytest

from app.api import catalog_jobs
from tests import apidata, timeline
from tests.builders.catalog.api_catalog import (
    BASE,
    NIL,
    OFFERING,
    PRICE,
    SPEC,
    make_offering,
    make_price,
    make_spec,
    price_refs,
)

CATEGORY = f"{BASE}/category"
CATALOG = f"{BASE}/productCatalog"
EXPORT = f"{BASE}/exportJob"
IMPORT = f"{BASE}/importJob"
PATHS = (SPEC, PRICE, OFFERING, CATEGORY, CATALOG)


def build_catalog(api, name="Home") -> dict:
    """Spec, priced offering, root and child category, catalog; plus a spare price."""
    spec = make_spec(api)
    price = make_price(api)
    offering = make_offering(
        api,
        spec,
        productOfferingPrice=price_refs(price),
        externalIdentifier=apidata.code_identifier("FIBER100"),
    )
    root = api.post(CATEGORY, {"name": "Internet"}).json()
    child = api.post(
        CATEGORY,
        {
            "name": "Fiber",
            "parent": {"id": root["id"]},
            "productOffering": [{"id": offering["id"]}],
        },
    ).json()
    home = api.post(CATALOG, {"name": name, "category": [{"id": root["id"]}]}).json()
    return {"spec": spec, "price": price, "offering": offering, "root": root, "child": child,
            "catalog": home}  # fmt: skip


def export(api, **body) -> dict:
    response = api.post(EXPORT, {"@type": "ExportJob", **body})
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()


def document(job: dict) -> dict:
    prefix = "data:application/json;base64,"
    assert job["url"].startswith(prefix)
    return json.loads(base64.b64decode(job["url"][len(prefix) :]))


def import_(api, url: str, status=201) -> dict:
    response = api.post(IMPORT, {"@type": "ImportJob", "url": url})
    assert response.status_code == status, response.text
    return response.json()


def snapshot(api) -> dict:
    """Every catalog entry as GET returns it, without lastUpdate."""
    return {
        path: sorted(
            ({k: v for k, v in e.items() if k != "lastUpdate"} for e in api.get(path).json()),
            key=lambda e: e["id"],
        )
        for path in PATHS
    }


def delete_everything(api, built: dict) -> None:
    # dependents first; the offering takes its category membership with it
    for key in ("catalog", "price", "offering", "child", "root", "spec"):
        assert api.delete(built[key]["href"]).status_code == 204, key
    for path in PATHS:
        assert api.get(path).json() == []


def test_export_delete_import_restores_the_catalog(api):
    built = build_catalog(api)
    before = snapshot(api)
    job = export(api)
    assert job["status"] == "Succeeded", job.get("errorLog")
    assert job["@type"] == "ExportJob"
    assert job["contentType"] == "application/json" and job["path"] == BASE
    assert job["creationDate"] == job["completionDate"] and "errorLog" not in job
    assert api.get(job["href"]).json() == job
    doc = document(job)
    assert doc["@type"] == "BssCatalogDocument" and doc["version"] == 1
    assert [e["id"] for e in doc["productOffering"]] == [built["offering"]["id"]]
    delete_everything(api, built)
    imported = import_(api, job["url"])
    assert imported["status"] == "Succeeded", imported.get("errorLog")
    assert imported["url"] == job["url"]
    assert snapshot(api) == before
    # the same document again changes nothing and succeeds
    again = import_(api, job["url"])
    assert again["status"] == "Succeeded", again.get("errorLog")
    assert snapshot(api) == before
    assert len(api.get(IMPORT).json()) == 2


def test_export_of_one_product_catalog(api):
    built = build_catalog(api)
    other = make_offering(api, name="Elsewhere")
    api.post(CATALOG, {"name": "Other"})
    job = export(
        api, query=f"productCatalog.id={built['catalog']['id']}", url="data:application/json"
    )
    assert job["query"] == f"productCatalog.id={built['catalog']['id']}"
    doc = document(job)
    assert [e["id"] for e in doc["productCatalog"]] == [built["catalog"]["id"]]
    assert {e["id"] for e in doc["category"]} == {built["root"]["id"], built["child"]["id"]}
    assert [e["id"] for e in doc["productOffering"]] == [built["offering"]["id"]]
    assert other["id"] not in json.dumps(doc)
    assert [e["id"] for e in doc["productOfferingPrice"]] == [built["price"]["id"]]
    assert [e["id"] for e in doc["productSpecification"]] == [built["spec"]["id"]]


def test_a_conflicting_entry_fails_the_whole_import(api, db_conn):
    built = build_catalog(api)
    job = export(api)
    assert api.delete(built["catalog"]["href"]).status_code == 204
    assert api.patch(built["spec"]["href"], {"name": "Renamed"}).status_code == 200
    before = snapshot(api)
    failed = import_(api, job["url"])
    assert failed["status"] == "Failed"
    assert failed["errorLog"] == (
        f"productSpecification {built['spec']['id']} already exists with different content (name)"
    )
    # nothing written: the catalog, missing before, is still missing
    assert snapshot(api) == before
    assert api.get(failed["href"]).json() == failed


def test_import_applies_post_rules_past_prices_are_refused(api):
    built = build_catalog(api)
    job = export(api)
    delete_everything(api, built)
    api.clock.set(timeline.START + timedelta(days=1))  # the price now starts in the past
    failed = import_(api, job["url"])
    assert failed["status"] == "Failed"
    assert failed["errorLog"].startswith(f"productOfferingPrice {built['price']['id']}: ")
    assert "in the past" in failed["errorLog"]
    for path in PATHS:
        assert api.get(path).json() == []


def _data_url(doc) -> str:
    return "data:application/json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()


@pytest.mark.parametrize(
    ("doc", "error"),
    [
        ([], "the document must be a BssCatalogDocument"),
        ({"@type": "BssCatalogDocument", "version": 2}, "unsupported document version"),
        ({"@type": "BssCatalogDocument", "version": 1, "extra": []}, "unknown document"),
        ({"@type": "BssCatalogDocument", "version": 1, "category": {}}, "must be an array"),
        (
            {"@type": "BssCatalogDocument", "version": 1, "category": [{"id": "x"}]},
            "is not a UUID",
        ),
        (
            {
                "@type": "BssCatalogDocument",
                "version": 1,
                "category": [{"id": NIL, "@type": "ProductCatalog"}],
            },
            "@type must be Category",
        ),
        (
            {
                "@type": "BssCatalogDocument",
                "version": 1,
                "category": [{"id": NIL, "@type": "Category", "name": "x", "unknown": 1}],
            },
            "unknown",
        ),
        (
            {
                "@type": "BssCatalogDocument",
                "version": 1,
                "productOffering": [
                    {
                        "id": NIL,
                        "@type": "ProductOffering",
                        "name": "x",
                        "lifecycleStatus": "Active",
                        "validFor": {"startDateTime": "2027-01-01T00:00:00Z"},
                        "productSpecification": {"id": NIL},
                    }
                ],
            },
            "does not exist",
        ),
    ],
)
def test_invalid_documents_fail_the_job(api, doc, error):
    failed = import_(api, _data_url(doc))
    assert failed["status"] == "Failed" and error in failed["errorLog"], failed["errorLog"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/catalog.json",
        "http://localhost:8080/tmf-api/productCatalogManagement/v5/exportJob",
        "file:///etc/passwd",
        "data:text/plain;base64,e30=",
        "data:application/json;base64,not base64!",
        "data:application/json;base64," + base64.b64encode(b"{not json").decode(),
    ],
)
def test_the_server_reads_only_inline_json(api, url):
    assert import_(api, url, status=422)["@type"] == "Error"
    assert api.get(IMPORT).json() == []


def test_request_validation(api):
    for body, status in [
        ({"@type": "ImportJob"}, 422),  # url is required
        ({"@type": "ExportJob", "url": "x"}, 400),  # wrong @type for the resource
        # status is the server's: this import fails, so "Succeeded" is not its value
        ({"@type": "ImportJob", "url": _data_url({}), "status": "Succeeded"}, 422),
        ({"@type": "ImportJob", "url": _data_url({}), "path": "/elsewhere"}, 422),
        ({"@type": "ImportJob", "url": _data_url({}), "contentType": "text/csv"}, 422),
    ]:
        assert api.post(IMPORT, body).status_code == status, body
    for body, status in [
        ({"url": "https://example.org/out.json"}, 422),
        ({"query": "name=Home"}, 422),
        ({"query": f"productCatalog.id={NIL}"}, 422),
        ({"errorLog": "x"}, 422),  # the server's, and a successful export has none
        ({"unknown": 1}, 400),
    ]:
        assert api.post(EXPORT, {"@type": "ExportJob", **body}).status_code == status, body
    assert api.get(EXPORT).json() == []


def test_lists_fields_filters_and_delete(api):
    job = export(api)
    failed = import_(api, _data_url([]))
    assert [j["id"] for j in api.get(f"{IMPORT}?status=Failed").json()] == [failed["id"]]
    assert api.get(f"{IMPORT}?status=Succeeded").json() == []
    assert api.get(f"{EXPORT}?fields=status").json() == [
        {"id": job["id"], "href": job["href"], "@type": "ExportJob", "status": "Succeeded"}
    ]
    # an id of the other kind is not found
    assert api.get(f"{IMPORT}/{job['id']}").status_code == 404
    assert api.delete(f"{IMPORT}/{job['id']}").status_code == 404
    for href in (job["href"], failed["href"]):
        assert api.delete(href).status_code == 204
        assert api.get(href).status_code == 404
        assert api.delete(href).status_code == 404
    assert api.delete(f"{EXPORT}/{NIL}?fields=x").status_code == 400


def test_an_oversized_export_fails(api, monkeypatch):
    build_catalog(api)
    monkeypatch.setattr(catalog_jobs, "MAX_DOCUMENT_BYTES", 100)
    job = export(api)
    assert job["status"] == "Failed" and "url" not in job
    assert job["errorLog"].endswith("at most 100")


def test_mcp_round_trip(api, mcp):
    build_catalog(api)
    reader, writer = mcp.token("reader", "read"), mcp.token("writer", "write")
    error, answer = mcp.result(writer, "create_export_job", {"body": {"@type": "ExportJob"}})
    assert not error and answer["status"] == 201
    url = answer["body"]["url"]
    error, answer = mcp.result(writer, "create_import_job", {"body": {"url": url}})
    assert not error and answer["body"]["status"] == "Succeeded"
    error, answer = mcp.result(reader, "list_import_jobs", {})
    assert not error and len(answer["body"]) == 1
    assert mcp.call(reader, "create_import_job", {"body": {"url": url}}).json()["error"] == (
        "insufficient_scope"
    )
