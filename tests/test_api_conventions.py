"""TMF630 conventions shared by every API, exercised through the catalog (TMF620):
Money, TimePeriod, lists, merge patch and error bodies."""

import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api import tmfjson
from app.repository import catalog
from tests import contract, timeline
from tests.apiclient import decimals
from tests.timeline import iso, z

BASE = "/tmf-api/productCatalogManagement/v5"
SPEC = f"{BASE}/productSpecification"
PRICE = f"{BASE}/productOfferingPrice"
START = iso(timeline.START)
END = timeline.START + timedelta(days=31)  # a month later, in BILLING_TZ


def spec_body(**over):
    return {"@type": "ProductSpecification", "name": "Fiber", "lifecycleStatus": "Active", **over}


def price_json(value: str, **over) -> str:
    """A price body as text, so the amount literal reaches the server exactly as written."""
    extra = "".join(f', "{k}": {v}' for k, v in over.items())
    return (
        '{"@type": "ProductOfferingPrice", "name": "Monthly", "lifecycleStatus": "Active",'
        ' "priceType": "recurring", "recurringChargePeriodType": "monthly",'
        f' "validFor": {{"startDateTime": "{START}"}},'
        f' "price": {{"unit": "RUB", "value": {value}}}{extra}}}'
    )


def stored_amounts(db_conn) -> list[Decimal]:
    return [r[0] for r in db_conn.execute("SELECT amount FROM product_offering_price").fetchall()]


# --- Money ---


@pytest.mark.parametrize("literal", ["1234567890.12", "0.10", "19.99"])
def test_money_goes_out_as_a_json_number_and_parses_back_exactly(api, literal):
    created = api.post_raw(PRICE, price_json(literal))
    assert created.status_code == 201
    # a bare number token with every digit, not a string and not a float rendering
    assert f'"value":{literal}}}' in created.text
    assert decimals(created)["price"]["value"] == Decimal(literal)
    fetched = api.get(created.headers["Location"])
    assert decimals(fetched)["price"] == {"unit": "RUB", "value": Decimal(literal)}


def test_incoming_amount_is_stored_exactly(api, db_conn):
    assert api.post_raw(PRICE, price_json("19.99")).status_code == 201
    assert stored_amounts(db_conn) == [Decimal("19.99")]


def test_incoming_amount_never_passes_through_float(api, db_conn):
    # As a float this literal is exactly 19.99 and would be accepted. Parsed as Decimal it
    # has 21 decimals and must be rejected - so a 422 proves no float was involved.
    literal = "19.990000000000000000001"
    assert float(literal) == 19.99
    response = api.post_raw(PRICE, price_json(literal))
    assert response.status_code == 422
    assert "at most 2 decimal places" in response.json()["message"]
    assert stored_amounts(db_conn) == []


def test_fastapi_own_body_parsing_goes_through_float():
    """Why handlers parse the body themselves (app/api/deps.py): FastAPI's built-in body
    handling turns a JSON number into a float before Pydantic builds the Decimal."""

    class Probe(BaseModel):
        value: Decimal

    probe = FastAPI()
    seen = []

    @probe.post("/probe")
    def _probe(body: Probe) -> dict:
        seen.append(body.value)
        return {}

    literal = "19.990000000000000000001"
    TestClient(probe).post(
        "/probe", content=f'{{"value": {literal}}}', headers={"Content-Type": "application/json"}
    )
    # the 21 decimals are gone: the value was a float before it became a Decimal
    assert seen == [Decimal("19.99")]
    assert seen != [Decimal(literal)]


@pytest.mark.parametrize("literal", ["19.999", "0.001", "1e-3"])
def test_more_than_two_decimals_is_rejected_not_rounded(api, db_conn, literal):
    response = api.post_raw(PRICE, price_json(literal))
    assert response.status_code == 422
    assert stored_amounts(db_conn) == []


@pytest.mark.parametrize("literal", ['"19.99"', "true", "null", "12345678901.00", "-1.00"])
def test_amounts_that_are_not_valid_money_are_rejected(api, db_conn, literal):
    assert api.post_raw(PRICE, price_json(literal)).status_code == 422
    assert stored_amounts(db_conn) == []


def test_equal_value_with_trailing_zeros_is_accepted(api, db_conn):
    assert api.post_raw(PRICE, price_json("19.9900")).status_code == 201
    assert api.post_raw(PRICE, price_json("20")).status_code == 201
    assert stored_amounts(db_conn) == [Decimal("19.99"), Decimal("20.00")]


def test_the_encoder_refuses_floats():
    with pytest.raises(TypeError, match="float"):
        tmfjson.dumps({"value": 19.99})
    assert tmfjson.dumps({"value": Decimal("0.10")}) == '{"value":0.10}'


# --- TimePeriod ---


def test_missing_end_means_open_ended(api, db_conn):
    body = api.post_raw(PRICE, price_json("1.00")).json()
    assert body["validFor"] == {"startDateTime": z(timeline.START)}
    lower, upper, bounds = db_conn.execute(
        "SELECT lower(valid_for), upper(valid_for), lower_inc(valid_for) AND NOT"
        " upper_inc(valid_for) FROM product_offering_price"
    ).fetchone()
    assert upper is None and bounds is True and lower == timeline.START


def test_period_with_end_is_stored_half_open(api, db_conn):
    text = price_json("1.00").replace(
        f'{{"startDateTime": "{START}"}}',
        f'{{"startDateTime": "{START}", "endDateTime": "{iso(END)}"}}',
    )
    body = api.post_raw(PRICE, text).json()
    assert body["validFor"]["endDateTime"] == z(END)
    assert db_conn.execute(
        "SELECT valid_for @> %s::timestamptz, valid_for @> %s::timestamptz"
        " FROM product_offering_price",
        (END - timedelta(seconds=1), END),
    ).fetchone() == (True, False)


@pytest.mark.parametrize(
    "period",
    [
        "{}",
        f'{{"endDateTime": "{iso(END)}"}}',
        f'{{"startDateTime": "{iso(END)}", "endDateTime": "{iso(END)}"}}',
        f'{{"startDateTime": "{iso(END)[:19]}"}}',  # no offset
    ],
)
def test_invalid_periods_are_rejected(api, period):
    text = price_json("1.00").replace(f'{{"startDateTime": "{START}"}}', period)
    assert api.post_raw(PRICE, text).status_code == 422


# --- lists ---


def _three_specs(api):
    for name in ("A", "B", "C"):
        status = "Retired" if name == "C" else "Active"
        assert api.post(SPEC, spec_body(name=name, lifecycleStatus=status)).status_code == 201


def test_list_defaults_and_count_headers(api):
    _three_specs(api)
    response = api.get(SPEC)
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["A", "B", "C"]
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Result-Count"] == "3"


def test_partial_page_is_200_with_both_counts(api):
    _three_specs(api)
    response = api.get(f"{SPEC}?offset=1&limit=1")
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["B"]
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Result-Count"] == "1"
    beyond = api.get(f"{SPEC}?offset=10")
    assert beyond.json() == [] and beyond.headers["X-Total-Count"] == "3"


def test_default_and_maximum_limit(api, db_conn):
    db_conn.execute(
        "INSERT INTO product_specification (name, lifecycle_status)"
        " SELECT 'S' || n, 'Active' FROM generate_series(1, 120) n"
    )
    db_conn.commit()
    assert len(api.get(SPEC).json()) == 20
    assert len(api.get(f"{SPEC}?limit=100").json()) == 100
    for bad in ("limit=101", "limit=0", "limit=x", "offset=-1"):
        assert api.get(f"{SPEC}?{bad}").status_code == 400, bad


def test_equality_filters(api):
    _three_specs(api)
    assert [i["name"] for i in api.get(f"{SPEC}?lifecycleStatus=Retired").json()] == ["C"]
    assert [i["name"] for i in api.get(f"{SPEC}?name=B&lifecycleStatus=Active").json()] == ["B"]
    filtered = api.get(f"{SPEC}?lifecycleStatus=Active&limit=1")
    assert filtered.headers["X-Total-Count"] == "2" and filtered.headers["X-Result-Count"] == "1"
    # not an enum member: matches nothing, and must not surface as a database error
    assert api.get(f"{SPEC}?lifecycleStatus=nope").json() == []


def test_unknown_query_parameters_are_rejected(api):
    response = api.get(f"{SPEC}?description=x")
    assert response.status_code == 400
    assert "description" in response.json()["message"]
    assert api.get(f"{SPEC}?name=A&name=B").status_code == 400


def test_fields_selects_top_level_attributes(api):
    _three_specs(api)
    items = api.get(f"{SPEC}?fields=name").json()
    assert all(set(item) == {"id", "href", "@type", "name"} for item in items)
    one = api.get(f"{items[0]['href']}?fields=lifecycleStatus").json()
    assert set(one) == {"id", "href", "@type", "lifecycleStatus"}
    assert api.get(f"{SPEC}?fields=name,bogus").status_code == 400
    assert api.get(f"{items[0]['href']}?limit=1").status_code == 400


# --- POST / PATCH ---


def test_post_returns_201_with_location_of_the_new_resource(api):
    created = api.post(SPEC, spec_body(description="FTTH"))
    assert created.status_code == 201
    body = created.json()
    assert created.headers["Location"] == body["href"] == f"{SPEC}/{body['id']}"
    assert body["@type"] == "ProductSpecification"
    assert api.get(body["href"]).json() == body


def test_meta_attributes_are_accepted_unknown_business_attributes_are_not(api):
    accepted = api.post(
        SPEC,
        spec_body(**{"@baseType": "Entity", "@schemaLocation": "https://example.org/s.json"}),
    )
    assert accepted.status_code == 201
    rejected = api.post(SPEC, spec_body(brand="Acme"))
    assert rejected.status_code == 400
    assert "brand: unknown attribute" in rejected.json()["message"]
    assert len(api.get(SPEC).json()) == 1


def test_missing_mandatory_field_is_422_and_malformed_json_is_400(api):
    missing = api.post(SPEC, {"@type": "ProductSpecification", "name": "Fiber"})
    assert missing.status_code == 422
    assert "lifecycleStatus" in missing.json()["message"]
    assert api.post(SPEC, spec_body(lifecycleStatus="Alive")).status_code == 422
    assert api.post_raw(SPEC, '{"name": ').status_code == 400
    assert api.post_raw(SPEC, '{"name": NaN}').status_code == 400
    assert api.post_raw(SPEC, "[1]").status_code == 400
    assert api.post_raw(SPEC, "{}", content_type="text/plain").status_code == 415


def test_server_managed_attributes_are_rejected_on_post(api):
    for name in ("id", "href"):
        response = api.post(SPEC, spec_body(**{name: "x"}))
        assert response.status_code == 400
        assert f"{name} is read-only" in response.json()["message"]
    # the spec's create schema makes clients send lastUpdate; accepted, server-managed
    assert api.post(SPEC, spec_body(lastUpdate="2020-01-01T00:00:00Z")).status_code == 201


def test_patch_is_a_merge_patch(api):
    href = api.post(SPEC, spec_body(description="FTTH")).json()["href"]
    patched = api.patch(href, {"name": "Fiber 2", "description": None})
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "Fiber 2" and "description" not in body
    assert body["lifecycleStatus"] == "Active"  # untouched
    assert api.get(href).json() == body


def test_patch_accepts_only_merge_patch_json(api):
    href = api.post(SPEC, spec_body()).json()["href"]
    for content_type in ("application/json", "application/json-patch+json", "text/plain"):
        response = api.patch(href, {"name": "x"}, content_type=content_type)
        assert response.status_code == 415, content_type
    assert api.get(href).json()["name"] == "Fiber"


def test_patch_rejects_read_only_attributes(api):
    href = api.post(SPEC, spec_body()).json()["href"]
    for body in ({"id": "1"}, {"href": "/x"}, {"lastUpdate": "2020-01-01T00:00:00Z"}):
        response = api.patch(href, body)
        assert response.status_code == 400, body
        assert "read-only" in response.json()["message"]
    assert api.patch(href, {"@type": "Other"}).status_code == 400
    # repeating the unchanged @type is fine: the spec's _MVO schema even requires it
    assert api.patch(href, {"@type": "ProductSpecification", "name": "X"}).status_code == 200
    assert api.patch(href, {"name": None}).status_code == 422
    assert api.patch(href, {"brand": "Acme"}).status_code == 400


# --- errors ---


def test_unknown_resources_and_routes_answer_with_a_tmf_error(api):
    missing = api.get(f"{SPEC}/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json() == {
        "@type": "Error",
        "code": "404",
        "reason": "Not Found",
        "message": "productSpecification 00000000-0000-0000-0000-000000000000 not found",
        "status": "404",
    }
    assert api.get(f"{SPEC}/not-a-uuid").status_code == 404
    assert api.patch(f"{SPEC}/not-a-uuid", {"name": "x"}).status_code == 404
    for path in (f"{BASE}/nope", "/tmf-api/nope", "/nope"):
        response = api.get(path)
        assert response.status_code == 404, path
        assert response.json()["@type"] == "Error"


@pytest.mark.parametrize("method", ["DELETE", "PUT"])
def test_unsupported_methods_are_405_with_a_tmf_error(api, method):
    href = api.post(SPEC, spec_body()).json()["href"]
    for path in (SPEC, href):
        response = api.request(method, path)
        assert response.status_code == 405
        assert response.json()["reason"] == "Method Not Allowed"
    assert api.get(href).status_code == 200


def test_unhandled_exception_is_a_tmf_500_without_details(api, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(catalog, "list_product_specifications", boom)
    response = api.get(SPEC)
    assert response.status_code == 500
    assert response.json()["message"] == "internal error"
    assert "secret" not in response.text


def test_unhandled_exception_is_logged_with_its_traceback(api, monkeypatch, caplog):
    """The 500 handler runs in a threadpool thread (Starlette, for a sync handler), where
    sys.exc_info() is empty: log.exception() alone logged `NoneType: None`."""

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(catalog, "list_product_specifications", boom)
    with caplog.at_level(logging.ERROR, logger="app.api"):
        assert api.get(SPEC).status_code == 500
    (record,) = [r for r in caplog.records if r.name == "app.api"]
    assert record.getMessage() == f"unhandled error on GET {SPEC}"
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError
    trace = caplog.text
    assert "Traceback (most recent call last)" in trace
    assert "in boom" in trace and "RuntimeError: secret internal detail" in trace


def test_a_failed_request_leaves_nothing_behind(api, db_conn, monkeypatch):
    """One transaction per request: the offering insert is rolled back when attaching the
    price fails afterwards."""
    spec = api.post(SPEC, spec_body()).json()
    offering = {
        "@type": "ProductOffering",
        "name": "Fiber 100",
        "lifecycleStatus": "Active",
        "validFor": {"startDateTime": START},
        "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
        "productOfferingPrice": [
            {"id": "00000000-0000-0000-0000-000000000000", "@type": "ProductOfferingPriceRef"}
        ],
    }
    assert api.post(f"{BASE}/productOffering", offering).status_code == 422
    assert db_conn.execute("SELECT count(*) FROM product_offering").fetchone() == (0,)


# --- the contract check itself ---


def test_a_broken_body_fails_contract_validation(api):
    good = api.post_raw(PRICE, price_json("19.99")).json()
    assert contract.errors("TMF620", "ProductOfferingPrice", good) == []
    no_type = {k: v for k, v in good.items() if k != "@type"}
    assert any(
        "'@type' is a required property" in e
        for e in contract.errors("TMF620", "ProductOfferingPrice", no_type)
    )
    as_string = {**good, "price": {"unit": "RUB", "value": "19.99"}}
    assert any(
        e.startswith("price/value:")
        for e in contract.errors("TMF620", "ProductOfferingPrice", as_string)
    )


def test_the_client_fails_a_test_when_the_api_breaks_the_contract(api, monkeypatch):
    """The wrapper in tests/apiclient.py is what makes every API test a contract test:
    here the handler is sabotaged to emit Money.value as a string."""
    from app.api import catalog as catalog_api

    monkeypatch.setattr(catalog_api, "money", lambda amount, unit: {"unit": unit, "value": "x"})
    with pytest.raises(AssertionError, match="does not conform to TMF620 ProductOfferingPrice"):
        api.post_raw(PRICE, price_json("19.99"))
