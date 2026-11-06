"""The MCP connector, slice 3 (docs/mcp.md): tools generated from app.openapi() and the
registry in bss_mcp/operations.py, and the drift check that keeps them equal.

- the tool set == API operations minus progress exclusions and interactive auth operations;
- every write names the request model its handler parses (read from the handler source);
- every GET tool answers against the real app, and every write tool reaches it."""

import ast
import importlib
import inspect
import json
import pkgutil
import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import anyio
import pytest
import yaml

import app.api
import app.internal
from app.api.hub import HUB_APIS
from app.main import app as bss_app
from app.repository import network_commands, payment_commands, payment_methods
from app.repository import prepaid_commands as commands
from app.repository import usage as usage_repo
from bss_mcp import tools
from bss_mcp.operations import AUTH_OPERATIONS, EXCLUDED, OPERATIONS, Op
from bss_mcp.tools import DriftError, generate
from tests import apidata, docs_collect, factories
from tests.apidata import CATALOG
from tests.test_api_bill_settlement import account_with_bills, bills, pay
from tests.test_billing_db import at, run_row
from tests.timeline import iso, midnight

BALANCE = "/tmf-api/prepayBalanceManagement/v4"
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


@pytest.mark.smoke
def test_the_tools_are_the_api_operations_minus_the_scope_exclusions():
    excluded = scope_exclusions()
    assert EXCLUDED == excluded  # the runtime image's copy (it has no docs/)
    excluded |= AUTH_OPERATIONS
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
    """The model names a handler passes to parse(Model, body) or parse_documented."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    return [
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) in ("parse", "parse_documented")
        and node.args
        and isinstance(node.args[0], ast.Name)
    ]


def test_every_write_names_the_model_its_handler_parses():
    handlers = _handlers()
    for name, (method, _) in api_operations().items():
        if (method, _) in EXCLUDED | AUTH_OPERATIONS:
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


def test_body_keys_popped_by_write_handlers_are_in_the_tool_schema():
    handlers = _handlers()
    for name, tool in generated().items():
        if tool.body is None:
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(handlers[name])))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "body"
                and node.func.attr == "pop"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assert (
                    node.args[0].value in tool.input_schema["properties"]["body"]["properties"]
                ), name


def test_bill_cycle_filter_and_passthrough_hints():
    generated_tools = generated()
    billing_date = generated_tools["list_bill_cycles"].query["filters"]["properties"]["billingDate"]
    assert billing_date["format"] == "date-time"
    assert billing_date["examples"] == ["2026-10-12T21:00:00Z"]
    offering = generated_tools["create_offering"].input_schema["properties"]["body"]
    hint = offering["properties"]["externalIdentifier"]["description"]
    assert "array of ExternalIdentifier_FVO" in hint
    assert "required: @type, id" in hint
    from bss_mcp.server import INSTRUCTIONS

    assert str(len(generated_tools)) in INSTRUCTIONS
    assert str(sum(t.read_only for t in generated_tools.values())) in INSTRUCTIONS


def test_draft_can_be_edited_and_submitted_through_mcp(api, mcp):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    writer = mcp.token("draft-writer", "write")

    def call(name, args, status):
        _, result = mcp.result(writer, name, args)
        assert isinstance(result, dict) and result["status"] == status, result
        return result.get("body", {})

    draft = call(
        "create_order",
        {
            "body": apidata.order_body(
                account, apidata.add_item(offering), requestedInitialState="draft"
            )
        },
        201,
    )
    assert draft["state"] == "draft"
    args = {
        "order_id": draft["id"],
        "body": {
            "productOrderItem": [apidata.add_item(offering)],
            "requestedStartDate": apidata.START,
        },
    }
    assert call("patch_order", args, 200)["state"] == "draft"
    args["body"] = {"state": "acknowledged"}
    assert call("patch_order", args, 200)["state"] == "completed"
    # The existing API rejects repeated submission with 422 (not the review's 409).
    call("patch_order", args, 422)
    args["body"] = {"requestedStartDate": iso(midnight(date(2028, 1, 1)))}
    call("patch_order", args, 409)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda ops: ops.pop("get_payment"), "no entry for \\['get_payment'\\]"),
        (lambda ops: ops.update(gone=Op("x")), "no operation for \\['gone'\\]"),
        (lambda ops: ops.update(create_payment=Op("x")), "create_payment: a POST with body None"),
        (lambda ops: ops.update(get_payment=Op("x", body=Op)), "get_payment: a GET with body"),
        (
            lambda ops: ops.update(list_bill_cycles=Op("x", query="list", filters={"gone": {}})),
            "registry declares unknown filters",
        ),
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
    generated_tools = generated()
    assert set(listed) == set(generated_tools)
    for name, tool in listed.items():
        read_only = generated_tools[name].read_only
        assert tool["annotations"]["readOnlyHint"] is read_only, name
        assert tool["annotations"]["destructiveHint"] is (not read_only), name
    reader = mcp.tools(mcp.token("reader", "read"))
    assert set(reader) == {n for n, t in generated_tools.items() if t.read_only}


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
def mcp_settings(mcp_settings):
    """Every tool once per test: more calls than the default per-minute budget (60)."""
    mcp_settings.mcp_rate_per_min = 200
    return mcp_settings


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
        "/tmf-api/payment/v4/refund",
        {
            "account": {"id": refund_account["id"]},
            "totalAmount": {"unit": "USD", "value": 10},
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
    export_job = api.post(f"{CATALOG}/exportJob", {"@type": "ExportJob"}).json()
    import_job = api.post(
        f"{CATALOG}/importJob", {"@type": "ImportJob", "url": export_job["url"]}
    ).json()
    later = apidata.order(
        api,
        apidata.account(api, name="Later"),
        apidata.add_item(offering),
        requestedStartDate=iso(midnight(date(2027, 1, 1))),
    )
    cancellation = api.post(
        "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
        {"productOrder": {"id": later["id"]}},
    ).json()
    hub = api.post("/tmf-api/customer/v5/hub", {"callback": "http://listener.test/events"}).json()
    number = api.post(
        "/tmf-api/resourceInventory/v5/resource",
        {
            "@type": "LogicalResource",
            "name": "MCP number",
            "value": "12025550123",
        },
    ).json()
    resource_hub = api.post(
        "/tmf-api/resourceInventory/v5/hub",
        {
            "callback": "http://listener.test/numbers",
        },
    ).json()
    prepaid_account = apidata.account(api, name="Prepaid")
    prepaid_bucket = factories.prepaid_bucket(db_conn, prepaid_account["id"], api.clock.now())
    db_conn.commit()  # the API reads the bucket in its own transaction
    topups = f"{BALANCE}/topupBalance"
    topup_body = {
        "usageType": "monetary",
        "bucket": {"id": str(prepaid_bucket["id"])},
        "partyAccount": {"id": prepaid_account["id"]},
    }
    topup = api.post(topups, {**topup_body, "amount": {"amount": 10, "units": "USD"}}).json()
    # a number that is not the account's: refused, stored failed
    refused = api.post(
        topups,
        {
            **topup_body,
            "amount": {"amount": 5, "units": "USD"},
            "logicalResource": [{"id": number["id"]}],
        },
    ).json()
    assert (topup.get("status"), refused.get("status")) == ("completed", "failed"), (topup, refused)
    adjusts = f"{BALANCE}/adjustBalance"
    adjust_body = {k: v for k, v in topup_body.items() if k != "partyAccount"}
    adjust = api.post(adjusts, {**adjust_body, "amount": {"amount": 3, "units": "USD"}}).json()
    # a debit above the spendable funds: refused, stored failed
    refused_adjust = api.post(
        adjusts, {**adjust_body, "amount": {"amount": -499, "units": "USD"}}
    ).json()
    assert (adjust.get("status"), refused_adjust.get("status")) == ("completed", "failed")
    reserves = f"{BALANCE}/reserveBalance"
    reserve = api.post(reserves, {**topup_body, "amount": {"amount": 2, "units": "USD"}}).json()
    refused_reserve = api.post(
        reserves, {**topup_body, "amount": {"amount": 499, "units": "USD"}}
    ).json()
    assert (reserve.get("status"), refused_reserve.get("status")) == ("created", "failed")
    receiver_account = apidata.account(api, name="Prepaid receiver")
    receiver_bucket = factories.prepaid_bucket(db_conn, receiver_account["id"], api.clock.now())
    db_conn.commit()
    transfer_body = {
        "reason": "MCP transfer",
        "channel": {"id": "mcp", "name": "MCP"},
        "logicalResource": [{"id": number["id"]}],
        "receiverLogicalResource": {"id": number["id"]},
        "amount": {"amount": 1, "units": "USD"},
        "usageType": "monetary",
        "bucket": {"id": str(prepaid_bucket["id"])},
        "receiverBucket": {"id": str(receiver_bucket["id"])},
        "receiverBucketUsageType": "monetary",
    }
    # the number is nobody's: refused, stored failed
    transfer = api.post(f"{BALANCE}/transferBalance", transfer_body).json()
    assert transfer.get("status") == "failed", transfer
    command = commands.enqueue(
        db_conn,
        uuid4(),
        UUID(prepaid_account["id"]),
        kind="renewal",
        due_date=date(2026, 12, 3),
        request={"previous_interval_id": str(uuid4())},
        now=api.clock.now(),
    )
    db_conn.commit()
    world = {
        "command": command,
        "bucket": prepaid_bucket,
        "topup": topup,
        "refused_topup": refused,
        "adjust": adjust,
        "refused_adjust": refused_adjust,
        "reserve": reserve,
        "refused_reserve": refused_reserve,
        "transfer": transfer,
        "transfer_body": transfer_body,
        "number": number,
        "resource_hub": resource_hub,
        "hub": hub,
        "cancellation": cancellation,
        "export_job": export_job,
        "import_job": import_job,
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
    order_id, item_id = db_conn.execute(
        "SELECT product_order_id, id FROM product_order_item ORDER BY created_at LIMIT 1"
    ).fetchone()
    world["network_command"] = network_commands.insert(
        db_conn,
        order_id=order_id,
        order_item_id=item_id,
        product_id=None,
        kind="activate",
        due_date=date(2026, 12, 3),
        profile={"voiceMinutes": 100},
        now=api.clock.now(),
    )
    world["usage"] = usage_repo.insert(
        db_conn,
        network_record_id="mcp-world-1",
        kind="data",
        quantity=1024,
        roaming=False,
        throttled=False,
        msisdn=None,
        imsi=None,
        event_at=api.clock.now(),
        event_date=api.clock.today(),
        product=usage_repo.product_row(db_conn, UUID(world["product"]["id"])),
        now=api.clock.now(),
    )
    paying = UUID(world["topup"]["partyAccount"]["id"])
    world["payment_method"] = payment_methods.insert(
        db_conn,
        account_id=paying,
        name="Visa",
        description=None,
        brand="visa",
        card_type=None,
        last_four="4242",
        expiration_date=None,
        token="tok_world",
        preferred=None,
        now=api.clock.now(),
    )
    world["payment_command"] = payment_commands.insert(
        db_conn,
        account_id=paying,
        method_id=world["payment_method"]["id"],
        purpose="topup",
        amount=Decimal("5.00"),
        currency="USD",
        client_key="world",
        request={"token": "tok_world", "amount": Decimal("5.00"), "currency": "USD"},
        now=api.clock.now(),
    )
    db_conn.commit()
    return world


def get_arguments(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    acc, offering = world["account"]["id"], world["offering"]["id"]
    by_id = {
        "get_resource": {"resource_id": world["number"]["id"]},
        "get_bucket": {"bucket_id": str(world["bucket"]["id"])},
        "get_topup_balance": {"topup_id": world["topup"]["id"]},
        "get_adjust_balance": {"adjust_id": world["adjust"]["id"]},
        "get_reserve_balance": {"reserve_id": world["reserve"]["id"]},
        "get_transfer_balance": {"transfer_id": world["transfer"]["id"]},
        "get_history": {"history_id": world["topup"]["id"]},
        "get_accumulated_balance": {"balance_id": str(world["bucket"]["accumulated_balance_id"])},
        "get_hub_resource": {"hub_id": world["resource_hub"]["id"]},
        "get_bill_format": {"format_id": world["format"]["id"]},
        "get_bill_media": {"media_id": world["media"]["id"]},
        "get_bill_demand": {"demand_id": world["demand"]["id"]},
        "get_refund": {"refund_id": world["refund"]["id"]},
        "get_category": {"category_id": world["category"]["id"]},
        "get_catalog": {"catalog_id": world["catalog"]["id"]},
        "get_export_job": {"job_id": world["export_job"]["id"], "fields": "status"},
        "get_import_job": {"job_id": world["import_job"]["id"]},
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
        "get_cancellation": {"cancel_id": world["cancellation"]["id"]},
        "get_hub_customer": {"hub_id": world["hub"]["id"]},
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
        "get_prepaid_command": {"command_id": str(world["command"]["id"])},
        "list_prepaid_commands": {"state": "pending", "limit": 1},
        "get_usage": {"usage_id": str(world["usage"]["id"])},
        "usage_summary": {"product.id": world["product"]["id"]},
        "get_network_command": {"command_id": str(world["network_command"]["id"])},
        "list_network_commands": {"state": "pending", "limit": 1},
        "list_payment_methods": {"limit": 1},
        "get_payment_method": {"method_id": str(world["payment_method"]["id"])},
        "list_card_topups": {"account_id": world["topup"]["partyAccount"]["id"], "limit": 1},
        "get_auto_topup": {"account_id": world["topup"]["partyAccount"]["id"]},
        "list_payment_commands": {"state": "pending", "limit": 1},
        "get_payment_command": {"command_id": str(world["payment_command"]["id"])},
        "list_event_subscriptions": {"api": "TMF676", "limit": 1},
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
    number = call(
        "create_resource",
        {
            "body": {
                "@type": "LogicalResource",
                "name": "Spare number",
                "value": "12025550124",
            }
        },
    )
    call("patch_resource", {"resource_id": number["id"], "body": {"administrativeState": "locked"}})
    call("delete_resource", {"resource_id": number["id"]}, expected=(204,))
    price_fields = {
        "@type": "ProductOfferingPrice",
        "name": "Monthly",
        "lifecycleStatus": "Active",
        "priceType": "recurring",
        "recurringChargePeriodType": "monthly",
        "price": {"unit": "USD", "value": "450.50"},
    }
    price = call("create_price", {"body": {**price_fields, "validFor": {"startDateTime": start}}})
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
    # deletes: a spare chain nothing references, then the catalog and its root category
    spare_spec = call(
        "create_specification",
        {"body": {"@type": "ProductSpecification", "name": "Spare", "lifecycleStatus": "Active"}},
    )
    spare = call(
        "create_offering",
        {
            "body": {
                "@type": "ProductOffering",
                "name": "Spare",
                "lifecycleStatus": "In Design",
                "validFor": {"startDateTime": start},
                "productSpecification": {"id": spare_spec["id"]},
            }
        },
    )
    call("delete_offering", {"offering_id": spare["id"]}, expected=(204,))
    call("delete_specification", {"spec_id": spare_spec["id"]}, expected=(204,))
    spare_price = call(
        "create_price", {"body": {**price_fields, "validFor": {"startDateTime": start}}}
    )
    call("delete_price", {"price_id": spare_price["id"]}, expected=(204,))
    call("delete_catalog", {"catalog_id": catalog["id"]}, expected=(204,))
    call("delete_category", {"category_id": category["id"]}, expected=(204,))
    exported = call("create_export_job", {"body": {"@type": "ExportJob"}})
    imported = call("create_import_job", {"body": {"url": exported["url"]}})
    assert imported["status"] == "Succeeded", imported
    call("delete_import_job", {"job_id": imported["id"]}, expected=(204,))
    call("delete_export_job", {"job_id": exported["id"]}, expected=(204,))
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
        call(f"delete_{resource}_account", {"account_id": created["id"]}, expected=(204,))
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
    # deletes: a spare chain nothing references, removed from the top
    spare_person = call("create_individual", {"body": {"givenName": "Spare", "familyName": "P"}})
    spare_org = call("create_organization", {"body": {"name": "Spare Ltd"}})
    spare_customer = call("create_customer", {"body": apidata.customer_body(spare_person)})
    spare_account = call("create_account", {"body": apidata.account_body(spare_customer)})
    spare_financial = call(
        "create_financial_account",
        {
            "body": {
                "name": "Spare",
                "relatedParty": [{"role": "owner", "partyOrPartyRole": {"id": spare_org["id"]}}],
            }
        },
    )
    spare_media = call("create_bill_media", {"body": {"name": "Spare medium"}})
    for name, key, entry in (
        ("delete_account", "account_id", spare_account),
        ("delete_customer", "customer_id", spare_customer),
        ("delete_individual", "party_id", spare_person),
        ("delete_financial_account", "account_id", spare_financial),
        ("delete_organization", "party_id", spare_org),
        ("delete_bill_media", "media_id", spare_media),
        ("delete_bill_format", "format_id", fmt),  # names are unique: no spare format
    ):
        call(name, {key: entry["id"]}, expected=(204,))
    call("create_bill_demand", {"body": {"billingAccount": {"id": account["id"]}}})
    early = call(
        "create_order",
        {"body": apidata.order_body(account, apidata.add_item(offering))},
    )
    assert early["state"] == "rejected"  # the offering is valid from 2027 only
    call("delete_order", {"order_id": early["id"]}, expected=(204,))
    future = call(
        "create_order",
        {"body": apidata.order_body(account, apidata.add_item(offering), requestedStartDate=start)},
    )
    assert future["state"] == "completed", future
    call("cancel_order", {"body": {"productOrder": {"id": future["id"]}}})
    call(
        "create_payment",
        {"body": money_as_strings(apidata.payment_body(account, "120.00"))},
    )
    call(
        "create_refund",
        {
            "body": {
                "account": {"id": account["id"]},
                "totalAmount": {"unit": "USD", "value": "20.00"},
                "paymentMethod": {"@type": "Cash"},
            }
        },
    )
    new_topup = call(
        "create_topup_balance",
        {
            "body": {
                "usageType": "monetary",
                "amount": {"amount": "7.50", "units": "USD"},
                "bucket": {"id": str(world["bucket"]["id"])},
                "partyAccount": {"id": world["topup"]["partyAccount"]["id"]},
            }
        },
    )
    assert new_topup["status"] == "completed"
    call(
        "patch_topup_balance",
        {"topup_id": new_topup["id"], "body": {"status": "cancelled", "reason": "Entered twice"}},
    )
    call("delete_topup_balance", {"topup_id": world["refused_topup"]["id"]}, expected=(204,))
    new_adjust = call(
        "create_adjust_balance",
        {
            "body": {
                "usageType": "monetary",
                "amount": {"amount": "-1.50", "units": "USD"},
                "bucket": {"id": str(world["bucket"]["id"])},
            }
        },
    )
    assert new_adjust["status"] == "completed"
    call(
        "patch_adjust_balance",
        {"adjust_id": new_adjust["id"], "body": {"status": "cancelled", "reason": "Wrong account"}},
    )
    call("delete_adjust_balance", {"adjust_id": world["refused_adjust"]["id"]}, expected=(204,))
    new_reserve = call(
        "create_reserve_balance",
        {
            "body": {
                "usageType": "monetary",
                "amount": {"amount": "1.00", "units": "USD"},
                "bucket": {"id": str(world["bucket"]["id"])},
                "partyAccount": {"id": world["topup"]["partyAccount"]["id"]},
            }
        },
    )
    assert new_reserve["status"] == "created"
    call(
        "patch_reserve_balance",
        {"reserve_id": new_reserve["id"], "body": {"status": "cancelled", "reason": "Not needed"}},
    )
    call("delete_reserve_balance", {"reserve_id": world["refused_reserve"]["id"]}, expected=(204,))
    new_transfer = call(
        "create_transfer_balance",
        {"body": {**world["transfer_body"], "amount": {"amount": "2.50", "units": "USD"}}},
    )
    assert new_transfer["status"] == "failed"
    call(
        "patch_transfer_balance",
        {
            "transfer_id": new_transfer["id"],
            "body": {
                "reason": "Corrected",
                "channel": world["transfer_body"]["channel"],
                "logicalResource": world["transfer_body"]["logicalResource"],
                "receiverLogicalResource": world["transfer_body"]["receiverLogicalResource"],
            },
        },
    )
    call("delete_transfer_balance", {"transfer_id": world["transfer"]["id"]}, expected=(204,))
    call("patch_bill", {"bill_id": world["bill"]["id"], "body": {"state": "onHold"}})
    call(
        "price_change",
        {
            "offering_id": offering["id"],
            "body": {"amount": "499.99", "currency": "USD", "validFrom": "2027-02-01"},
        },
    )
    call("catch_up", {"body": {"upTo": str(date(2026, 12, 2))}}, expected=(200, 202))
    # the run is not failed: the app refuses, but the tool reached it
    call("retry", {"run_id": str(world["run"]["id"])}, expected=(409,))
    # a pending command is not failedTechnical: the app refuses, but the tool reached it
    call("retry_prepaid_command", {"command_id": str(world["command"]["id"])}, expected=(409,))
    # a pending network command is not exhausted: refused (409), but the tool reached it
    call(
        "retry_network_command",
        {"command_id": str(world["network_command"]["id"])},
        expected=(409,),
    )
    usage = call(
        "create_usage",
        {
            "body": {
                "@type": "ProductUsage",
                "usageSpecification": {"id": "sms"},
                "product": {"id": world["product"]["id"]},
                "usageCharacteristic": [
                    {"@type": "IntegerCharacteristic", "name": "quantity", "value": 2}
                ],
                "externalIdentifier": [{"@type": "ExternalIdentifier", "id": "mcp-write-1"}],
            }
        },
    )
    assert usage["usageType"] == "sms"
    # the card tools: save a card, rename it, queue a top-up and an auto top-up rule, remove it
    paying = world["topup"]["partyAccount"]["id"]
    card = call(
        "create_payment_method",
        {
            "body": {
                "@type": "tokenizedCard",
                "name": "Spare card",
                "account": [{"id": paying}],
                "token": "tok_spare",
            }
        },
    )
    call("patch_payment_method", {"method_id": card["id"], "body": {"name": "Other card"}})
    call(
        "card_topup",
        {
            "account_id": paying,
            "body": {"amount": {"unit": "USD", "value": "12.50"}, "idempotencyKey": "mcp-1"},
        },
        expected=(202,),
    )
    call(
        "patch_auto_topup",
        {
            "account_id": paying,
            "body": {
                "enabled": True,
                "threshold": {"unit": "USD", "value": "5.00"},
                "amount": {"unit": "USD", "value": "20.00"},
            },
        },
    )
    call("delete_payment_method", {"method_id": card["id"]}, expected=(204,))
    # a pending card charge is not exhausted: refused (409), but the tool reached it
    call(
        "retry_payment_command",
        {"command_id": str(world["payment_command"]["id"])},
        expected=(409,),
    )
    for _, short in HUB_APIS:
        hub = call(f"create_hub_{short}", {"body": {"callback": "http://listener.test/x"}})
        call(f"delete_hub_{short}", {"hub_id": hub["id"]}, expected=(204,))
    # product writes are orders placed on the caller's behalf
    product_ref = {
        "billingAccount": {"id": account["id"]},
        "productOffering": {"id": offering["id"]},
    }
    product = call("create_product", {"body": {**product_ref, "startDate": start}})
    ends = iso(midnight(date(2027, 3, 1)))
    # a started product can have its end scheduled (one not started can only be cancelled)
    call("patch_product", {"product_id": world["product"]["id"], "body": {"terminationDate": ends}})
    assert product["status"] == "created"
    spare_product = call("create_product", {"body": {**product_ref, "startDate": start}})
    call("delete_product", {"product_id": spare_product["id"]}, expected=(204,))
    call("patch_order", {"order_id": future["id"], "body": {"priority": "1"}})
    cycle = call("create_cycle_specification", {"body": {"name": "Business"}})
    call(
        "patch_cycle_specification",
        {"specification_id": cycle["id"], "body": {"description": "B2B"}},
    )
    call("delete_cycle_specification", {"specification_id": cycle["id"]}, expected=(204,))

    assert called == {n for n, t in generated().items() if not t.read_only}
    history = api.get(f"/internal/v1/customerBill/{world['bill']['id']}").json()["history"]
    assert history[-1]["operator"] == "mcp-token:writer"
