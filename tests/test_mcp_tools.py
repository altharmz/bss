"""The MCP connector, slice 3 (docs/mcp.md): tools generated from app.openapi() and the
registry in bss_mcp/operations.py, and the drift check that keeps them equal.

- the tool set == the API operations minus docs/progress/scope.yaml's api exclusions;
- every write names the request model its handler parses (read from the handler source);
- every GET tool answers against the real app, and every write tool reaches it."""

import ast
import importlib
import inspect
import json
import pkgutil
import textwrap
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml

import app.api
import app.internal
from app.main import app as bss_app
from bss_mcp import tools
from bss_mcp.operations import EXCLUDED, OPERATIONS, Op
from bss_mcp.tools import DriftError, generate
from tests import apidata, docs_collect
from tests.apidata import CATALOG
from tests.test_api_bill_settlement import account_with_bills, bills, pay
from tests.test_billing_db import at, run_row
from tests.timeline import iso, midnight

SCOPE = Path("docs/progress/scope.yaml")


def generated() -> dict[str, tools.Tool]:
    return anyio.run(generate, bss_app)


def api_operations() -> dict[str, tuple[str, str]]:
    return {
        op["operationId"]: (method.upper(), path)
        for path, ops in bss_app.openapi()["paths"].items()
        for method, op in ops.items()
    }


def scope_exclusions() -> set[tuple[str, str]]:
    scope = yaml.safe_load(SCOPE.read_text())
    return {(e["method"], e["path"]) for e in scope["excluded"] if e.get("area") == "api"}


# --- the drift check ---


def test_the_tools_are_the_api_operations_minus_the_scope_exclusions():
    excluded = scope_exclusions()
    assert EXCLUDED == excluded  # the runtime image's copy (it has no docs/)
    expected = {name for name, key in api_operations().items() if key not in excluded}
    assert set(generated()) == expected
    assert len(expected) == len(api_operations()) - len(excluded)


def _handlers() -> dict[str, Any]:
    modules = [
        importlib.import_module(m.name)
        for package in (app.api, app.internal)
        for m in pkgutil.iter_modules(package.__path__, package.__name__ + ".")
    ] + [importlib.import_module("app.status"), importlib.import_module("app.main")]
    found: dict[str, Any] = {}
    for module in modules:
        for name, value in vars(module).items():
            if inspect.isfunction(value) and value.__module__ == module.__name__:
                found.setdefault(name, value)
    return found


def _parsed_models(handler: Any) -> list[str]:
    """The model names a handler passes to parse(Model, body)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    return [
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "parse"
        and node.args
        and isinstance(node.args[0], ast.Name)
    ]


def test_every_write_names_the_model_its_handler_parses():
    handlers = _handlers()
    for name, (method, _) in api_operations().items():
        if (method, _) in EXCLUDED:
            continue
        op, handler = OPERATIONS[name], handlers[name]
        parsed = _parsed_models(handler)
        if method in ("POST", "PATCH"):
            expected = [] if op.no_body else [op.body.__name__]
            assert parsed == expected, f"{name}: handler parses {parsed}, registry {expected}"
            if op.body is not None:  # the very class, not a namesake
                assert vars(inspect.getmodule(handler))[parsed[0]] is op.body, name
        else:
            assert op.body is None and not op.no_body and parsed == [], name


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda ops: ops.pop("get_payment"), "no entry for \\['get_payment'\\]"),
        (lambda ops: ops.update(gone=Op("x")), "no operation for \\['gone'\\]"),
        (lambda ops: ops.update(create_payment=Op("x")), "create_payment: a POST with body None"),
        (lambda ops: ops.update(get_payment=Op("x", body=Op)), "get_payment: a GET with body"),
    ],
)
def test_generation_refuses_a_registry_that_drifted(monkeypatch, change, message):
    registry = dict(OPERATIONS)
    change(registry)
    monkeypatch.setattr(tools, "OPERATIONS", registry)
    with pytest.raises(DriftError, match=message):
        generated()


def test_the_list_filters_are_the_handlers_own():
    probed = docs_collect.list_filters(
        [{"method": m, "path": p} for m, p in api_operations().values()]
    )
    for tool in generated().values():
        if "filters" in tool.query:
            assert sorted(tool.query["filters"]["properties"]) == sorted(probed[tool.path])
    assert sorted(generated()["list_payments"].query["filters"]["properties"]) == [
        "account.id",
        "status",
    ]


def test_names_annotations_and_schemas():
    for tool in generated().values():
        text = json.dumps(tool.input_schema)
        assert '"number"' not in text, f"{tool.name}: a JSON number in the schema"
        assert "$ref" not in text and "$defs" not in text, tool.name
        assert len(tool.name) <= 64 and tool.description, tool.name
        assert tool.read_only == (tool.method == "GET")
    price_change = generated()["price_change"].input_schema["properties"]["body"]
    assert price_change["properties"]["amount"]["format"] == "decimal"
    assert "decimal strings" in generated()["create_payment"].description
    assert "merge patch" in generated()["patch_bill"].description
    assert "Filters: account.id, status." in generated()["list_payments"].description


def test_the_server_lists_every_generated_tool_with_its_hints(mcp):
    listed = mcp.tools(mcp.token("writer", "write"))
    assert set(listed) == set(generated())
    for name, tool in listed.items():
        read_only = generated()[name].read_only
        assert tool["annotations"]["readOnlyHint"] is read_only, name
        assert tool["annotations"]["destructiveHint"] is (not read_only), name
    reader = mcp.tools(mcp.token("reader", "read"))
    assert set(reader) == {n for n, t in generated().items() if t.read_only}


# --- every tool against the real app ---


def money_as_strings(value: Any) -> Any:
    """A test body as a tool takes it: amounts as decimal strings."""
    if isinstance(value, dict):
        out = {k: money_as_strings(v) for k, v in value.items()}
        if set(out) >= {"unit", "value"} and not isinstance(out["value"], str):
            out["value"] = str(out["value"])
        return out
    if isinstance(value, list):
        return [money_as_strings(v) for v in value]
    return value


@pytest.fixture
def world(api, db_conn) -> dict[str, Any]:
    """One of everything, written through the API (and a billing run)."""
    offering = apidata.sellable_offering(api, value=300)
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [bill] = bills(api, account)
    payment = pay(api, account, 100, date(2026, 12, 2)).json()
    api.clock.set(at(date(2026, 12, 3)))

    def first(path: str) -> dict:
        return api.get(f"{path}?limit=1").json()[0]

    category = api.post(f"{CATALOG}/category", {"name": "Connectivity"}).json()
    catalog = api.post(
        f"{CATALOG}/productCatalog", {"name": "Residential", "category": [{"id": category["id"]}]}
    ).json()
    fmt = api.post("/tmf-api/accountManagement/v5/billFormat", {"name": "JSON summary"}).json()
    media = api.post(
        "/tmf-api/accountManagement/v5/billPresentationMedia", {"name": "Portal"}
    ).json()
    demand = api.post(
        "/tmf-api/customerBillManagement/v5/customerBillOnDemand",
        {"billingAccount": {"id": account["id"]}},
    ).json()
    refund_account = apidata.account(api)
    pay(api, refund_account, 100, date(2026, 12, 3))
    refund = api.post(
        "/tmf-api/paymentManagement/v4/refund",
        {
            "account": {"id": refund_account["id"]},
            "totalAmount": {"unit": "RUB", "value": 10},
            "paymentMethod": {"@type": "Cash"},
        },
    ).json()
    organization = apidata.organization(api)
    financial = api.post(
        "/tmf-api/accountManagement/v5/financialAccount",
        {
            "name": "Group",
            "relatedParty": [{"role": "owner", "partyOrPartyRole": {"id": organization["id"]}}],
        },
    ).json()
    owned = {
        "name": "Partner",
        "relatedParty": [{"role": "owner", "partyOrPartyRole": {"id": organization["id"]}}],
    }
    party_account = api.post("/tmf-api/accountManagement/v5/partyAccount", owned).json()
    settlement = api.post("/tmf-api/accountManagement/v5/settlementAccount", owned).json()
    return {
        "financial": financial,
        "party_account": party_account,
        "settlement": settlement,
        "format": fmt,
        "media": media,
        "demand": demand,
        "refund": refund,
        "category": category,
        "catalog": catalog,
        "offering": offering,
        "account": account,
        "bill": bill,
        "payment": payment,
        "organization": organization,
        "individual": first(f"{apidata.PARTY}/individual"),
        "customer": first(apidata.CUSTOMER),
        "spec": first(f"{CATALOG}/productSpecification"),
        "price": first(f"{CATALOG}/productOfferingPrice"),
        "product": first(apidata.PRODUCT),
        "rate": first(apidata.RATE),
        "order": first(apidata.ORDER),
        "run": run_row(db_conn, date(2026, 12, 1)),
    }


def get_arguments(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    acc, offering = world["account"]["id"], world["offering"]["id"]
    by_id = {
        "get_bill_format": {"format_id": world["format"]["id"]},
        "get_bill_media": {"media_id": world["media"]["id"]},
        "get_bill_demand": {"demand_id": world["demand"]["id"]},
        "get_refund": {"refund_id": world["refund"]["id"]},
        "get_category": {"category_id": world["category"]["id"]},
        "get_catalog": {"catalog_id": world["catalog"]["id"]},
        "get_specification": {"spec_id": world["spec"]["id"]},
        "get_price": {"price_id": world["price"]["id"]},
        "get_offering": {"offering_id": offering, "fields": "name"},
        "get_individual": {"party_id": world["individual"]["id"]},
        "get_organization": {"party_id": world["organization"]["id"]},
        "get_customer": {"customer_id": world["customer"]["id"]},
        "get_account": {"account_id": acc},
        "get_financial_account": {"account_id": world["financial"]["id"]},
        "get_party_account": {"account_id": world["party_account"]["id"]},
        "get_settlement_account": {"account_id": world["settlement"]["id"]},
        "get_cycle_specification": {"specification_id": "monthly"},
        "get_payment": {"payment_id": world["payment"]["id"]},
        "get_product": {"product_id": world["product"]["id"]},
        "get_bill": {"bill_id": world["bill"]["id"]},
        "get_bill_cycle": {"cycle_id": str(world["run"]["id"])},
        "get_rate": {"rate_id": world["rate"]["id"]},
        "get_order": {"order_id": world["order"]["id"]},
        "first_charge": {
            "account_id": acc,
            "productOffering.id": offering,
            "startDate": "2026-12-10",
        },
        "open_products": {"account_id": acc},
        "account_settlement": {"account_id": acc},
        "charges": {"account_id": acc, "limit": 5},
        "transactions": {"account_id": acc, "offset": 0},
        "get_run": {"run_id": str(world["run"]["id"])},
        "run_accounts": {"run_id": str(world["run"]["id"]), "status": "ok"},
        "bill_detail": {"bill_id": world["bill"]["id"]},
        "list_payments": {"filters": {"account.id": acc}, "limit": 1},
        "list_bills": {"filters": {"billingAccount.id": acc}},
    }
    return by_id


def test_every_get_tool_answers_against_the_app(mcp, world):
    reader = mcp.token("reader", "read")
    arguments = get_arguments(world)
    called = set()
    for name, tool in generated().items():
        if not tool.read_only:
            continue
        is_error, answer = mcp.result(reader, name, arguments.get(name, {}))
        expected = (200, 503) if name == "billing_status" else (200,)
        assert answer["status"] in expected, (name, answer)
        assert is_error == (answer["status"] >= 400)
        called.add(name)
    assert called == {n for n, t in generated().items() if t.read_only}
    is_error, payments = mcp.result(reader, "list_payments", arguments["list_payments"])
    assert payments["totalCount"] == 1 and payments["body"][0]["id"] == world["payment"]["id"]


def test_every_write_tool_reaches_the_app(api, mcp, world):
    """At least one successful write per resource - in fact every write tool."""
    writer = mcp.token("writer", "write")
    called: set[str] = set()
    start = iso(midnight(date(2027, 1, 1)))  # after the world's clock (2026-12-03)

    def call(name: str, arguments: dict[str, Any], expected=(200, 201)) -> dict[str, Any]:
        is_error, answer = mcp.result(writer, name, arguments)
        assert isinstance(answer, dict) and answer["status"] in expected, (name, answer)
        called.add(name)
        return answer.get("body", {})

    spec = call(
        "create_specification",
        {"body": {"@type": "ProductSpecification", "name": "Mobile", "lifecycleStatus": "Active"}},
    )
    call("patch_specification", {"spec_id": spec["id"], "body": {"description": "via MCP"}})
    category = call("create_category", {"body": {"name": "MCP category"}})
    call("patch_category", {"category_id": category["id"], "body": {"description": "via MCP"}})
    catalog = call(
        "create_catalog", {"body": {"name": "MCP catalog", "category": [{"id": category["id"]}]}}
    )
    call("patch_catalog", {"catalog_id": catalog["id"], "body": {"description": "via MCP"}})
    price = call(
        "create_price",
        {
            "body": {
                "@type": "ProductOfferingPrice",
                "name": "Monthly",
                "lifecycleStatus": "Active",
                "priceType": "recurring",
                "recurringChargePeriodType": "monthly",
                "price": {"unit": "RUB", "value": "450.50"},
                "validFor": {"startDateTime": start},
            }
        },
    )
    assert price["price"]["value"] == 450.5
    call("patch_price", {"price_id": price["id"], "body": {"name": "Monthly fee"}})
    offering = call(
        "create_offering",
        {
            "body": {
                "@type": "ProductOffering",
                "name": "Mobile S",
                "lifecycleStatus": "Active",
                "validFor": {"startDateTime": start},
                "productSpecification": {"id": spec["id"]},
                "productOfferingPrice": [{"id": price["id"]}],
            }
        },
    )
    call("patch_offering", {"offering_id": offering["id"], "body": {"description": "via MCP"}})
    person = call("create_individual", {"body": {"givenName": "Ada", "familyName": "Lovelace"}})
    call("patch_individual", {"party_id": person["id"], "body": {"familyName": "King"}})
    org = call("create_organization", {"body": {"name": "Analytical Engines"}})
    call("patch_organization", {"party_id": org["id"], "body": {"name": "AE Ltd"}})
    customer = call("create_customer", {"body": apidata.customer_body(person)})
    call("patch_customer", {"customer_id": customer["id"], "body": {"status": "Active"}})
    financial = call(
        "create_financial_account",
        {
            "body": {
                "name": "Group",
                "relatedParty": [{"role": "owner", "partyOrPartyRole": {"id": person["id"]}}],
            }
        },
    )
    call("patch_financial_account", {"account_id": financial["id"], "body": {"state": "Active"}})
    for resource in ("party", "settlement"):
        created = call(
            f"create_{resource}_account",
            {
                "body": {
                    "name": "Partner",
                    "relatedParty": [{"role": "owner", "partyOrPartyRole": {"id": person["id"]}}],
                    "financialAccount": {"id": financial["id"]},
                }
            },
        )
        call(
            f"patch_{resource}_account",
            {"account_id": created["id"], "body": {"financialAccount": None}},
        )
    account = call("create_account", {"body": apidata.account_body(customer)})
    call(
        "patch_account",
        {
            "account_id": account["id"],
            "body": {"name": "Main", "financialAccount": {"id": financial["id"]}},
        },
    )
    fmt = call("create_bill_format", {"body": {"name": "JSON detailed"}})
    call("patch_bill_format", {"format_id": fmt["id"], "body": {"description": "Detailed JSON"}})
    media = call("create_bill_media", {"body": {"name": "Download"}})
    call("patch_bill_media", {"media_id": media["id"], "body": {"description": "Downloadable"}})
    call("create_bill_demand", {"body": {"billingAccount": {"id": account["id"]}}})
    call(
        "create_order",
        {"body": apidata.order_body(account, apidata.add_item(offering))},
    )
    call(
        "create_payment",
        {"body": money_as_strings(apidata.payment_body(account, "120.00"))},
    )
    call(
        "create_refund",
        {
            "body": {
                "account": {"id": account["id"]},
                "totalAmount": {"unit": "RUB", "value": "20.00"},
                "paymentMethod": {"@type": "Cash"},
            }
        },
    )
    call("patch_bill", {"bill_id": world["bill"]["id"], "body": {"state": "onHold"}})
    call(
        "price_change",
        {
            "offering_id": offering["id"],
            "body": {"amount": "499.99", "currency": "RUB", "validFrom": "2027-02-01"},
        },
    )
    call("catch_up", {"body": {"upTo": str(date(2026, 12, 2))}}, expected=(200, 202))
    # the run is not failed: the app refuses, but the tool reached it
    call("retry", {"run_id": str(world["run"]["id"])}, expected=(409,))

    assert called == {n for n, t in generated().items() if not t.read_only}
    history = api.get(f"/internal/v1/customerBill/{world['bill']['id']}").json()["history"]
    assert history[-1]["operator"] == "mcp:writer"
