"""N1 contract fixtures: design examples, not evidence of implemented API routes."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.api import tmfjson
from tests import contract, tmf_gaps

NUMBER = {
    "@type": "LogicalResource",
    "name": "Subscriber number",
    "value": "+12025550123",
    "administrativeState": "unlocked",
    "usageState": "idle",
    "resourceSpecification": {"@type": "ResourceSpecificationRef", "id": "msisdn"},
}
TOPUP = {
    "@type": "TopupBalance",
    "amount": {"amount": Decimal("50.00"), "units": "USD"},
    "usageType": "monetary",
    "bucket": {"id": "sender-bucket"},
    "partyAccount": {"id": "sender-account", "@referredType": "BillingAccount"},
}
TRANSFER = {
    "@type": "TransferBalance",
    "reason": "Customer requested credit transfer",
    "channel": {"id": "admin-ui", "name": "Admin UI"},
    "logicalResource": [{"id": "sender-number"}],
    "receiverLogicalResource": {"id": "receiver-number"},
    "amount": {"amount": Decimal("10.00"), "units": "USD"},
    "usageType": "monetary",
    "bucket": {"id": "sender-bucket"},
    "receiverBucket": {"id": "receiver-bucket"},
    "receiverBucketUsageType": "monetary",
    "transferCost": {"value": Decimal("1.00"), "unit": "USD"},
    "costOwner": "originator",
}
ADJUSTMENT = {
    "@type": "AdjustBalance",
    "amount": {"amount": Decimal("-1.00"), "units": "USD"},
    "usageType": "monetary",
    "bucket": {"id": "sender-bucket"},
    "adjustType": "oneTime",
}
RESERVATION = {
    **TOPUP,
    "@type": "ReserveBalance",
    "reason": "Operator hold",
    "amount": {"amount": Decimal("5.00"), "units": "USD"},
}
ACTIONS = [TOPUP, TRANSFER, ADJUSTMENT, RESERVATION]


def test_number_request_and_assigned_response():
    contract.assert_valid("TMF639", "LogicalResource_FVO", NUMBER)
    contract.assert_valid(
        "TMF639", "LogicalResource", {**NUMBER, "id": "number", "usageState": "busy"}
    )
    broken = {**NUMBER, "usageState": "assigned"}
    assert contract.errors("TMF639", "LogicalResource", broken)
    # Quarantine is an allocationStatus string, not an invented usage-state enum.
    contract.assert_valid(
        "TMF639", "LogicalResource", {**NUMBER, "allocationStatus": "quarantined"}
    )


@pytest.mark.parametrize("body", ACTIONS, ids=lambda b: b["@type"])
def test_action_create_and_response(body):
    kind = body["@type"]
    contract.assert_valid("TMF654", kind + "_Create", body)
    response = {
        **body,
        "id": "action",
        "href": f"/tmf-api/prepayBalanceManagement/v4/{kind[0].lower() + kind[1:]}/action",
        "status": "created" if kind in {"ReserveBalance", "TransferBalance"} else "completed",
        "requestedDate": "2026-10-20T12:00:00Z",
    }
    contract.assert_valid("TMF654", kind, response)
    assert contract.errors("TMF654", kind, {**response, "status": "inProgress"})
    missing = {k: v for k, v in response.items() if k != "status"}
    assert contract.errors("TMF654", kind, missing)


@pytest.mark.parametrize("body", ACTIONS, ids=lambda b: b["@type"])
def test_create_required_fields_are_enforced(body):
    kind = body["@type"]
    schema = contract.spec("TMF654")["definitions"][kind + "_Create"]
    for name in schema["required"]:
        broken = {k: v for k, v in body.items() if k != name}
        assert contract.errors("TMF654", kind + "_Create", broken), name


def test_reservation_amount_is_a_local_restriction_not_an_upstream_requirement():
    body = {k: v for k, v in RESERVATION.items() if k != "amount"}
    contract.assert_valid("TMF654", "ReserveBalance_Create", body)
    assert (
        "partyAccount"
        not in contract.spec("TMF654")["definitions"]["AdjustBalance_Create"]["properties"]
    )


def test_money_and_quantity_have_distinct_shapes_and_exact_numbers():
    body = deepcopy(TRANSFER)
    encoded = tmfjson.dumps(body)
    decoded = tmfjson.loads(encoded.encode())
    assert decoded["amount"]["amount"] == Decimal("10.00")
    assert decoded["transferCost"]["value"] == Decimal("1.00")
    contract.assert_valid("TMF654", "TransferBalance_Create", decoded)
    body["amount"]["amount"] = "10.00"
    assert contract.errors("TMF654", "TransferBalance_Create", body)
    body = deepcopy(TRANSFER)
    body["transferCost"]["value"] = "1.00"
    assert contract.errors("TMF654", "TransferBalance_Create", body)


@pytest.mark.parametrize("body", ACTIONS, ids=lambda b: b["@type"])
def test_history_exception_is_conditional_and_keeps_status(body):
    kind = body["@type"]
    history = {**body, "@type": "BalanceActionHistory", "id": "action", "status": "completed"}
    assert contract.balance_history_errors(kind, history) == []
    if kind != "TransferBalance":
        assert contract.errors("TMF654", "BalanceActionHistory", history)
    missing_status = {k: v for k, v in history.items() if k != "status"}
    assert contract.balance_history_errors(kind, missing_status)
    no_receiver = {k: v for k, v in history.items() if k != "receiverLogicalResource"}
    assert contract.balance_history_errors("TransferBalance", no_receiver)


def test_history_exception_rejects_unknown_kinds_and_bad_nested_values():
    with pytest.raises(ValueError, match="unknown balance action kind"):
        contract.balance_history_errors("unknown", {"status": "completed"})
    assert contract.balance_history_errors(
        "TopupBalance", {"status": "completed", "amount": {"amount": "50"}}
    )


def test_bucket_available_and_reserved_fixture():
    contract.assert_valid(
        "TMF654",
        "Bucket",
        {
            "id": "sender-bucket",
            "status": "active",
            "usageType": "monetary",
            "remainingValue": {"amount": Decimal("39.00"), "units": "USD"},
            "reservedValue": {"amount": Decimal("11.00"), "units": "USD"},
            "partyAccount": TOPUP["partyAccount"],
        },
    )


def test_server_defaults_and_endpoint_listener_counts():
    for api, expected_path, endpoints, listeners in [
        ("TMF639", "/resourceInventory/v5", 8, 4),
        ("TMF654", "/tmf-api/prepayBalanceManagement/v4", 28, 12),
    ]:
        doc = contract.spec(api)
        assert tmf_gaps.server_path(doc) == expected_path
        assert (
            sum(
                method in ("get", "post", "patch", "delete")
                for path, item in doc["paths"].items()
                if not path.startswith("/listener/")
                for method in item
            )
            == endpoints
        )
        assert sum(p.startswith("/listener/") for p in doc["paths"]) == listeners
    assert tmf_gaps.probe_base_paths() == []


@pytest.mark.parametrize(
    "api,schema,values",
    [
        ("TMF639", "ResourceUsageStateType", ["idle", "active", "busy"]),
        ("TMF639", "ResourceAdministrativeStateType", ["locked", "unlocked", "shuttingDown"]),
        ("TMF654", "ActionStatusType", ["created", "failed", "cancelled", "completed"]),
        ("TMF654", "BucketStatusType", ["active", "suspended", "expired"]),
        ("TMF654", "CostOwnerType", ["originator", "receiver"]),
        ("TMF654", "UsageType", ["monetary", "voice", "data", "sms", "other"]),
    ],
)
def test_future_runtime_enum_contracts(api, schema, values):
    doc = contract.spec(api)
    schemas = doc["components"]["schemas"] if "components" in doc else doc["definitions"]
    assert schemas[schema]["enum"] == values
