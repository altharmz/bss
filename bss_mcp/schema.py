"""Tool input schemas from the app's request models, and the way back: tool arguments
checked against them and turned into the exact JSON the app expects.

Money: the app reads amounts as exact JSON numbers (app/api/tmfjson.py) and refuses
strings. An MCP client's JSON parser turns numbers into binary floats, so the tools ask
for **decimal strings** instead ("19.99") and refuse a JSON float anywhere in the
arguments; the string becomes a Decimal and goes to the app as an exact number.
"""

import copy
from decimal import Decimal
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from app.api import specschema

MONEY_VALUE = {
    "type": "string",
    "format": "decimal",
    "pattern": r"^-?\d{1,10}(\.\d{1,2})?$",
    "description": 'An exact amount as a decimal string with at most 2 decimal places, e.g. "19.99"'
    " - never a JSON number.",
}
# Every Decimal of the request models is an amount with the app's Money rules (at most 2
# decimals, never rounded), so one schema serves them all.


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of a request model as the app reads it (camelCase aliases), with
    every $ref inlined (some clients handle no $defs), titles dropped, and every Decimal
    (Money.value, an internal amount) as a decimal string."""
    raw = model.model_json_schema(by_alias=True, mode="validation")
    defs = copy.deepcopy(raw.pop("$defs", {}))
    return _inline(raw, defs, depth=0, within=())


def _recursion(name: str) -> dict[str, Any]:
    """Where a model contains itself (TMF622's nested productOrderItem): inlined once,
    below that an object of the same shape, described rather than repeated."""
    return {"type": "object", "description": f"A nested {name}: the same attributes as its parent."}


def _is_pydantic_decimal(node: dict[str, Any]) -> bool:
    """Pydantic's schema of a Decimal: a number, or a numeric string."""
    branches = node.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2:
        return False
    types = sorted(str(b.get("type")) for b in branches if isinstance(b, dict))
    return types == ["number", "string"]


@cache
def _document_description(description: str) -> str:
    """Summarize passthrough fields without inlining the spec's polymorphic trees."""
    if not description.endswith(", as the spec defines it"):
        return description
    api, attribute, *_ = description.split()
    attribute = attribute.rstrip(",")
    if api not in specschema.SPEC_FILES:
        return description
    summaries: set[str] = set()
    for name in specschema.schemas(api):
        if not name.endswith("_FVO"):
            continue
        node = specschema.properties(api, name).get(attribute)
        if node is None:
            continue
        array = node.get("type") == "array"
        target = node.get("items", {}) if array else node
        ref = target.get("$ref", "").rsplit("/", 1)[-1]
        if not ref:
            continue
        _, required, _ = specschema._flatten(api, target)
        summary = f"array of {ref}" if array else f"object {ref}"
        if required:
            summary += f"; required: {', '.join(sorted(required))}"
        summaries.add(summary)
    return description + (". " + " or ".join(sorted(summaries)) if summaries else "")


def _inline(node: Any, defs: dict[str, Any], depth: int, within: tuple[str, ...]) -> Any:
    if depth > 32:
        raise ValueError("schema nests too deep")
    if isinstance(node, list):
        return [_inline(item, defs, depth, within) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name in within:
            return _recursion(name)
        return _inline(defs[name], defs, depth + 1, (*within, name))
    if _is_pydantic_decimal(node):
        return dict(MONEY_VALUE)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue  # an annotation, noise in a tool schema
        if key == "description":
            out[key] = _document_description(value)
            continue
        if key == "properties":  # names here are attributes, never keywords
            out[key] = {name: _inline(sub, defs, depth, within) for name, sub in value.items()}
        else:
            out[key] = _inline(value, defs, depth, within)
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
