"""Validate API bodies against the vendored TMF OpenAPI schemas in spec/.

Built on openapi-schema-validator's OAS 3.0 dialect (for `nullable` etc.), with its
discriminator handling replaced:

- The library also dispatches on a `discriminator` that sits next to `allOf`. Every TMF v5
  schema has one that maps its own name to itself, so that dispatch recurses forever.
  Here `allOf`/`anyOf` are plain JSON Schema; such a discriminator only declares that
  subclasses may exist.
- Plain JSON Schema cannot validate the `...RefOrValue` schemas: they are a `oneOf` of two
  permissive schemas, so a plain reference matches both branches. Here a `oneOf` with a
  discriminator validates only the branch that `@type` selects.
- String `enum` values are compared after `strip()` (docs/decisions/data-model.md): upstream
  `ProductStatusType` spells `'aborted '`, the service sends `aborted` (spec/README.md).

tests/tmf/test_contract_validator.py proves each of these statements.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft4Validator, ValidationError, validators
from openapi_schema_validator import OAS30Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"

SPEC_FILES = {
    "TMF620": "TMF620-Product_Catalog_Management-v5.0.0.oas.yaml",
    "TMF622": "TMF622-ProductOrdering-v5.0.0.oas.yaml",
    "TMF629": "TMF629-Customer_Management-v5.0.1.oas.yaml",
    "TMF632": "TMF632-Party_Management-v5.0.0.oas.yaml",
    "TMF637": "TMF637-ProductInventory-v5.0.0.oas.yaml",
    "TMF635": "TMF635-Product_Usage_Management-v5.0.0.oas.yaml",
    "TMF677": "TMF677-Usage_Consumption-v5.0.0.oas.yaml",
    "TMF639": "TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml",
    "TMF654": "TMF654-PrepayBalance-v4.0.0.swagger.json",
    "TMF666": "TMF666-Account_Management-v5.0.0.oas.yaml",
    "TMF676": "TMF676-Payment-v4.0.0.swagger.json",
    "TMF670": "TMF670-PaymentMethod-v4.0.0.swagger.json",
    "TMF681": "TMF681-Communication-v4.0.0.swagger.json",
    "TMF683": "TMF683-Party_Interaction-v5.0.0.oas.yaml",
    "TMF621": "TMF621-Trouble_Ticket-v5.0.1.oas.yaml",
    "TMF678": "TMF678-CustomerBill-v5.0.0.oas.yaml",
    # ours: the internal API for the admin UI (not vendored, not TMF)
    "INTERNAL": "internal/bss-internal.oas.yaml",
    # ours: the customers' self-care API (docs/decisions/self-care.md)
    "SELF_CARE": "self-care/bss-self-care.oas.yaml",
}


def _one_of(validator, one_of, instance, schema):
    discriminator = schema.get("discriminator")
    if discriminator is None:
        yield from Draft4Validator.VALIDATORS["oneOf"](validator, one_of, instance, schema)
        return
    prop = discriminator["propertyName"]
    if not isinstance(instance, dict) or prop not in instance:
        yield ValidationError(f"{prop!r} is a required property (discriminator)")
        return
    ref = discriminator.get("mapping", {}).get(instance[prop])
    if ref is None:
        yield ValidationError(f"{instance[prop]!r} is not a known {prop} for this oneOf")
        return
    yield from validator.descend(instance, {"$ref": ref})


def _enum(validator, enums, instance, schema):
    if isinstance(instance, str) and any(
        isinstance(value, str) and value.strip() == instance for value in enums
    ):
        return
    yield from OAS30Validator.VALIDATORS["enum"](validator, enums, instance, schema)


TmfValidator = validators.extend(
    OAS30Validator,
    validators={
        "enum": _enum,
        "oneOf": _one_of,
        "allOf": Draft4Validator.VALIDATORS["allOf"],
        "anyOf": Draft4Validator.VALIDATORS["anyOf"],
    },
)


@cache
def spec(api: str) -> dict[str, Any]:
    path = SPEC_DIR / SPEC_FILES[api]
    assert path.is_file(), f"spec file missing: {path} (is ./spec mounted?)"
    text = path.read_text()
    # libyaml's loader: same documents, ~6x faster than safe_load's pure-Python one, and
    # every xdist worker parses all the specs once
    return json.loads(text) if path.suffix == ".json" else yaml.load(text, Loader=yaml.CSafeLoader)


def _schemas_pointer(api: str) -> str:
    # OAS 3 keeps schemas under components/schemas, Swagger 2 (TMF676 v4) under definitions
    return "#/components/schemas" if "components" in spec(api) else "#/definitions"


@cache
def validator(api: str, schema: str) -> Draft4Validator:
    doc = spec(api)
    schemas = doc.get("components", {}).get("schemas") or doc["definitions"]
    assert schema in schemas, f"{api} has no schema named {schema}"
    uri = f"urn:spec:{api}"
    registry = Registry().with_resource(uri, Resource(contents=doc, specification=DRAFT4))
    return TmfValidator({"$ref": f"{uri}{_schemas_pointer(api)}/{schema}"}, registry=registry)


def errors(api: str, schema: str, instance: Any, *, partial: bool = False) -> list[str]:
    """Human-readable validation errors; empty when the instance conforms."""
    found = validator(api, schema).iter_errors(instance)
    return sorted(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in found
        if not (partial and e.validator == "required" and not e.absolute_path)
    )


# Listener events whose schema the spec names differently from the event (TMF683 drops
# the "Event" of its status change).
EVENT_SCHEMAS = {("TMF683", "PartyInteractionStatusChangeEvent"): "PartyInteractionStatusChange"}


def assert_valid(api: str, schema: str, instance: Any, *, partial: bool = False) -> None:
    schema = EVENT_SCHEMAS.get((api, schema), schema)
    problems = errors(api, schema, instance, partial=partial)
    assert not problems, f"body does not conform to {api} {schema}:\n  " + "\n  ".join(problems)


def balance_history_errors(kind: str, instance: Any) -> list[str]:
    """TMF654's sole schema exception: non-transfers have no receiver resource.

    The caller must supply the persisted action kind; never infer it from a missing
    receiver. Transfers use the unchanged schema, and status is always required.
    """
    if kind not in {"TopupBalance", "AdjustBalance", "ReserveBalance", "TransferBalance"}:
        raise ValueError(f"unknown balance action kind: {kind}")
    found = validator("TMF654", "BalanceActionHistory").iter_errors(instance)
    return sorted(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in found
        if not (
            kind != "TransferBalance"
            and not e.absolute_path
            and e.validator == "required"
            and e.message == "'receiverLogicalResource' is a required property"
        )
    )


def declared_statuses(api: str, path: str, method: str) -> set[str]:
    """Status codes the spec declares for an operation, e.g. ('/productOffering', 'get')."""
    return {str(code) for code in spec(api)["paths"][path][method]["responses"]}


def response_schema(api: str, path: str, method: str, status: int) -> str | None:
    """The schema name the spec gives a JSON response, e.g. 'CareSession' (of each item, for an
    array); None without one."""
    response = spec(api)["paths"][path][method]["responses"].get(str(status), {})
    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    if schema.get("type") == "array":
        schema = schema.get("items", {})
    ref = schema.get("$ref")
    return None if ref is None else ref.rsplit("/", 1)[-1]
