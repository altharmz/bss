"""Tool input schemas from the app's request models, and the way back: tool arguments
checked against them and turned into the exact JSON the app expects.

Money: the app reads amounts as exact JSON numbers (app/api/tmfjson.py) and refuses
strings. An MCP client's JSON parser turns numbers into binary floats, so the tools ask
for **decimal strings** instead ("19.99") and refuse a JSON float anywhere in the
arguments; the string becomes a Decimal and goes to the app as an exact number.
"""

import copy
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel

MONEY_VALUE = {
    "type": "string",
    "format": "decimal",
    "pattern": r"^-?\d{1,10}(\.\d{1,2})?$",
    "description": 'An exact amount as a decimal string with at most 2 decimal places, e.g. "19.99"'
    " - never a JSON number.",
}


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of a request model as the app reads it (camelCase aliases), with
    every $ref inlined (some clients handle no $defs), titles dropped, and Money.value as
    a decimal string."""
    raw = model.model_json_schema(by_alias=True, mode="validation")
    defs = copy.deepcopy(raw.pop("$defs", {}))
    if "Money" in defs:
        defs["Money"]["properties"]["value"] = MONEY_VALUE
    if model.__name__ == "Money":
        raw["properties"]["value"] = MONEY_VALUE
    return _inline(raw, defs, depth=0)


def _inline(node: Any, defs: dict[str, Any], depth: int) -> Any:
    if depth > 32:
        raise ValueError("schema nests too deep (a recursive model?)")
    if isinstance(node, list):
        return [_inline(item, defs, depth) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs, depth + 1)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue  # an annotation, noise in a tool schema
        if key == "properties":  # names here are attributes, never keywords
            out[key] = {name: _inline(sub, defs, depth) for name, sub in value.items()}
        else:
            out[key] = _inline(value, defs, depth)
    return out


# --- arguments -> the app ---


def find_float(value: Any, path: str = "") -> str | None:
    """The path of the first JSON float in the arguments, or None. A float is refused
    outright: an amount must not have been through binary floating point."""
    if isinstance(value, float):
        return path or "(arguments)"
    if isinstance(value, dict):
        for key, item in value.items():
            found = find_float(item, f"{path}.{key}" if path else str(key))
            if found:
                return found
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = find_float(item, f"{path}[{i}]")
            if found:
                return found
    return None


def errors(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Why the arguments do not match the tool's input schema (empty: they do)."""
    out = []
    for error in sorted(Draft202012Validator(schema).iter_errors(arguments), key=str):
        where = "/".join(str(p) for p in error.absolute_path) or "(arguments)"
        out.append(f"{where}: {error.message}")
    return out


def to_wire(value: Any, schema: dict[str, Any]) -> Any:
    """The arguments as the app reads them: decimal strings become Decimal (sent as exact
    JSON numbers by app.api.tmfjson). Call only after `errors` found nothing."""
    if isinstance(value, dict):
        props = _branch_props(schema)
        return {key: to_wire(item, props.get(key, {})) for key, item in value.items()}
    if isinstance(value, list):
        items = _branch(schema, "items")
        return [to_wire(item, items) for item in value]
    if isinstance(value, str) and _is_decimal(schema):
        return Decimal(value)
    return value


def _branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [schema, *schema.get("anyOf", []), *schema.get("oneOf", [])]


def _branch_props(schema: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for branch in _branches(schema):
        props.update(branch.get("properties", {}))
    return props


def _branch(schema: dict[str, Any], key: str) -> dict[str, Any]:
    for branch in _branches(schema):
        if isinstance(branch.get(key), dict):
            return branch[key]
    return {}


def _is_decimal(schema: dict[str, Any]) -> bool:
    return any(branch.get("format") == "decimal" for branch in _branches(schema))
