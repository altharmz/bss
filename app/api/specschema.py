"""Attributes validated against the vendored spec itself (`spec/`, shipped in the image).

For attributes that are documents to us - stored as sent, no behaviour (a party's
contact media, a customer's credit profiles, ...): their nested schemas are many and
polymorphic, so they are checked against the spec's `components.schemas` rather than
restated as Pydantic models. docs/decisions/tmf-api.md, "Party and customer attributes".

What is checked: `$ref`, `allOf`, a `discriminator` (the `@type` selects the subclass
its mapping names, else 422), `oneOf` (only with a discriminator - every one in the
specs has one), `required`, `type`, `enum`. Unknown attributes at any depth are a 400,
as everywhere (`<path>: unknown attribute`); an object schema that declares no
properties at all (an ObjectCharacteristic's value) takes anything. Date-times go out
as UTC, as every other date-time of the API; numbers stay Decimal (tmfjson).
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from app.api.errors import Problem

SPEC_DIR = Path(__file__).resolve().parent.parent.parent / "spec"

SPEC_FILES = {
    "TMF620": "TMF620-Product_Catalog_Management-v5.0.0.oas.yaml",
    "TMF622": "TMF622-ProductOrdering-v5.0.0.oas.yaml",
    "TMF629": "TMF629-Customer_Management-v5.0.1.oas.yaml",
    "TMF632": "TMF632-Party_Management-v5.0.0.oas.yaml",
    "TMF637": "TMF637-ProductInventory-v5.0.0.oas.yaml",
    "TMF666": "TMF666-Account_Management-v5.0.0.oas.yaml",
    "TMF676": "TMF676-Payment-v4.0.0.swagger.json",
    "TMF678": "TMF678-CustomerBill-v5.0.0.oas.yaml",
}

Schema = dict[str, Any]


@cache
def schemas(api: str) -> dict[str, Schema]:
    path = SPEC_DIR / SPEC_FILES[api]
    text = path.read_text()
    if path.suffix == ".json":
        return json.loads(text)["definitions"]
    return yaml.load(text, Loader=yaml.CSafeLoader)["components"]["schemas"]


def _resolve(api: str, schema: Schema) -> Schema:
    while "$ref" in schema:
        schema = schemas(api)[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _flatten(api: str, schema: Schema) -> tuple[dict[str, Schema], set[str], bool]:
    """Properties and required names of a schema with its `allOf` parents, and whether
    it declares any structure at all (False: a free-form object)."""
    schema = _resolve(api, schema)
    props: dict[str, Schema] = {}
    required: set[str] = set()
    structured = "properties" in schema or "allOf" in schema
    for part in schema.get("allOf", ()):
        p, r, _ = _flatten(api, part)
        props |= p
        required |= r
    props |= schema.get("properties", {})
    required |= set(schema.get("required", ()))
    return props, required, structured


def properties(api: str, schema_name: str) -> dict[str, Schema]:
    return _flatten(api, schemas(api)[schema_name])[0]


def _join(path: str, key: str | int) -> str:
    return f"{path}.{key}" if path else str(key)


def _subclass(api: str, schema: Schema, value: dict[str, Any], path: str, out: list[Problem]):
    """The schema the instance's `@type` selects through a discriminator; None after
    reporting a problem. A `@type` the mapping does not name (or none) selects the
    schema itself, as plain JSON Schema reads an `allOf` (tests/contract.py): the specs'
    own examples use such names (TMF629's Customer_Create_example sends relatedParty
    as `RelatedPartyRefOrPartyRoleRef`), and the base still refuses what it does not
    declare. A `oneOf` has no schema of its own: a mapped `@type` is required."""
    discriminator = schema["discriminator"]
    name = discriminator["propertyName"]
    mapping = discriminator.get("mapping", {})
    selected = value.get(name)
    if isinstance(selected, str) and selected in mapping:
        return schemas(api)[mapping[selected].rsplit("/", 1)[-1]]
    if "oneOf" not in schema:
        return schema
    if name not in value:
        out.append((_join(path, name), "Field required", False))
    else:
        allowed = ", ".join(sorted(mapping))
        out.append((_join(path, name), f"must be one of: {allowed}", False))
    return None


def _check(api: str, schema: Schema, value: Any, path: str, out: list[Problem]) -> Any:
    """The value normalized; problems appended to `out`. A subclass is selected once, on
    the schema an attribute names: its `allOf` parents carry the same discriminator, and
    `_flatten` reads them without selecting again."""
    schema = _resolve(api, schema)
    if value is None:
        out.append((path, "cannot be null", False))
        return None
    if "discriminator" in schema:
        if not isinstance(value, dict):
            out.append((path, "must be an object", False))
            return None
        if "oneOf" in schema and "@type" not in value:
            # no subclass selected: still name every attribute none of them has
            union: set[str] = set()
            for branch in schema["oneOf"]:
                union |= set(_flatten(api, branch)[0])
            out.extend((_join(path, k), "unknown attribute", True) for k in value if k not in union)
        selected = _subclass(api, schema, value, path, out)
        if selected is None:
            return None
        schema = _resolve(api, selected)
    if "oneOf" in schema:
        out.append((path, "cannot be validated: oneOf without a discriminator", False))
        return None
    kind = schema.get("type")
    if kind == "array":
        if not isinstance(value, list):
            out.append((path, "must be an array", False))
            return None
        return [_check(api, schema["items"], v, _join(path, i), out) for i, v in enumerate(value)]
    if kind == "object" or "allOf" in schema or "properties" in schema:
        return _object(api, schema, value, path, out)
    return _scalar(schema, value, path, out)


def _object(api: str, schema: Schema, value: Any, path: str, out: list[Problem]) -> Any:
    if not isinstance(value, dict):
        out.append((path, "must be an object", False))
        return None
    props, required, structured = _flatten(api, schema)
    if not structured:
        return value  # free-form (ObjectCharacteristic.value)
    result = {}
    for key, item in value.items():
        if key not in props:
            out.append((_join(path, key), "unknown attribute", True))
            continue
        result[key] = _check(api, props[key], item, _join(path, key), out)
    out.extend(
        (_join(path, name), "Field required", False) for name in sorted(required - set(value))
    )
    return result


def _scalar(schema: Schema, value: Any, path: str, out: list[Problem]) -> Any:
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str):
            out.append((path, "must be a string", False))
            return None
        if "enum" in schema and value not in schema["enum"]:
            out.append((path, f"must be one of: {', '.join(schema['enum'])}", False))
            return None
        return _formatted(schema.get("format"), value, path, out)
    if kind == "boolean" and not isinstance(value, bool):
        out.append((path, "must be a boolean", False))
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        out.append((path, "must be an integer", False))
    elif kind == "number" and (isinstance(value, bool) or not isinstance(value, int | Decimal)):
        out.append((path, "must be a number", False))
    return value  # no type: anything (a Characteristic's value)


def _formatted(fmt: str | None, value: str, path: str, out: list[Problem]) -> str:
    if fmt == "date-time":
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            moment = None
        if moment is None or moment.tzinfo is None:
            out.append((path, "must be a date-time with a time zone", False))
            return value
        return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            out.append((path, "must be a date", False))
    return value


def check(
    api: str, schema_name: str, attributes: dict[str, Any]
) -> tuple[dict[str, Any], list[Problem]]:
    """Validate top-level attributes of a request body against the spec schema that
    declares them (e.g. Individual_FVO). Returns the normalized values and the problems."""
    props = properties(api, schema_name)
    out: list[Problem] = []
    values = {
        name: _check(api, props[name], value, name, out) for name, value in attributes.items()
    }
    return values, out


def check_element(
    api: str, schema_name: str, attribute: str, value: Any, path: str
) -> tuple[Any, list[Problem]]:
    """Validate one element of an array attribute (e.g. one of an account's relatedParty),
    reporting problems under `path`."""
    out: list[Problem] = []
    result = _check(api, properties(api, schema_name)[attribute]["items"], value, path, out)
    return result, out
