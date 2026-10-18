"""JSON Patch (RFC 6902) and TMF630's JSON Patch Query, as PATCH bodies.

Every v5 spec lists four PATCH content types. `application/json` and
`application/merge-patch+json` are merge patches. The two JSON Patch types are applied
here to the resource's current representation (what its GET answers), and the result
is handed to the handler as a merge patch of the top-level attributes that changed: one
set of rules - read-only, server-set, validation - for every format. A nested change
sends that attribute's whole new value, which is how the handlers treat nested values.

JSON Patch Query (`application/json-patch-query+json`, TMF630; the vendored specs show
`/contactMedium/preferred?city=Paris`): a path may end in `?attr=value[&attr=value]`. It
selects the elements of the first array on the path whose attributes (dotted names
reach into objects) equal the values, compared as text; the rest of the path applies
inside each of them, or the operation applies to the elements themselves when the path
ends at the array. A query that selects nothing is a 422, never a silent no-op.

docs/decisions/tmf-api.md, "PATCH formats".
"""

import copy
from decimal import Decimal
from typing import Any

from app.api.errors import bad_request, conflict, unprocessable

OPS = frozenset({"add", "remove", "replace", "move", "copy", "test"})
_ABSENT = object()


def operations(doc: Any) -> list[dict[str, Any]]:
    """The operations of a JSON Patch document (400 if it is not one). Members an
    operation does not define are ignored (RFC 6902, section 4)."""
    if not isinstance(doc, list):
        raise bad_request("a JSON Patch document is an array of operations")
    for n, op in enumerate(doc):
        where = f"operation {n}"
        if not isinstance(op, dict):
            raise bad_request(f"{where}: must be an object")
        if op.get("op") not in OPS:
            raise bad_request(f"{where}: op must be one of {', '.join(sorted(OPS))}")
        if not isinstance(op.get("path"), str):
            raise bad_request(f"{where}: path must be a string (a JSON Pointer)")
        if op["op"] in ("add", "replace", "test") and "value" not in op:
            raise bad_request(f"{where}: {op['op']} needs a value")
        if op["op"] in ("move", "copy") and not isinstance(op.get("from"), str):
            raise bad_request(f"{where}: {op['op']} needs from (a JSON Pointer)")
    return doc


def merge_patch(current: dict[str, Any], ops: list[dict[str, Any]], *, query: bool) -> dict:
    """The merge patch that turns `current` into `current` with `ops` applied."""
    patched = {"": copy.deepcopy(current)}  # a holder, so "" can be replaced too
    for n, op in enumerate(ops):
        try:
            _apply(patched, op, query)
        except _Failed as exc:
            raise exc.error(f"operation {n} ({op['op']} {op['path']}): {exc}") from None
    result = patched[""]
    if not isinstance(result, dict):
        raise unprocessable("the patched resource must stay a JSON object")
    changes = {key: None for key in current if key not in result}
    changes |= {k: v for k, v in result.items() if k not in current or current[k] != v}
    return changes


class _Failed(Exception):
    def __init__(self, message: str, error=unprocessable) -> None:
        super().__init__(message)
        self.error = error


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise _Failed("a JSON Pointer starts with /", bad_request)
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _apply(holder: dict[str, Any], op: dict[str, Any], query: bool) -> None:
    path = op["path"]
    if query and "?" in path:
        if op["op"] in ("move", "copy"):
            raise _Failed("a query path cannot be used with move or copy")
        pointer, _, raw = path.partition("?")
        for target in _query_targets(holder, _tokens(pointer), raw):
            _one(holder, op["op"], target, op)
        return
    if query and "?" in op.get("from", ""):
        raise _Failed("from cannot hold a query")
    _one(holder, op["op"], [""] + _tokens(path), op)


def _one(holder: dict[str, Any], name: str, target: list[str], op: dict[str, Any]) -> None:
    if name == "add":
        _add(holder, target, copy.deepcopy(op["value"]))
    elif name == "remove":
        _remove(holder, target)
    elif name == "replace":
        _remove(holder, target)
        _add(holder, target, copy.deepcopy(op["value"]))
    elif name == "test":
        if not _equal(_get(holder, target), op["value"]):
            raise _Failed("the value differs", conflict)
    else:
        source = [""] + _tokens(op["from"])
        value = _get(holder, source)
        if name == "move":
            if target[: len(source)] == source and target != source:
                raise _Failed("cannot move a value into itself")
            _remove(holder, source)
        _add(holder, target, copy.deepcopy(value))


def _container(holder: dict[str, Any], target: list[str]) -> Any:
    node: Any = holder
    for token in target[:-1]:
        node = _child(node, token)
        if node is _ABSENT:
            raise _Failed(f"{'/'.join(target[1:])}: no such location")
    return node


def _child(node: Any, token: str) -> Any:
    if isinstance(node, dict):
        return node.get(token, _ABSENT)
    if isinstance(node, list) and token.isdigit() and int(token) < len(node):
        return node[int(token)]
    return _ABSENT


def _get(holder: dict[str, Any], target: list[str]) -> Any:
    value = _child(_container(holder, target), target[-1])
    if value is _ABSENT:
        raise _Failed("no value at the path")
    return value


def _add(holder: dict[str, Any], target: list[str], value: Any) -> None:
    node, last = _container(holder, target), target[-1]
    if isinstance(node, dict):
        node[last] = value
    elif isinstance(node, list):
        if last == "-":
            node.append(value)
        elif last.isdigit() and int(last) <= len(node):
            node.insert(int(last), value)
        else:
            raise _Failed(f"index {last} is out of range")
    else:
        raise _Failed("the parent is not an object or array")


def _remove(holder: dict[str, Any], target: list[str]) -> None:
    node, last = _container(holder, target), target[-1]
    if _child(node, last) is _ABSENT:
        raise _Failed("no value at the path")
    if isinstance(node, dict):
        del node[last]
    else:
        del node[int(last)]


def _query_targets(holder: dict[str, Any], tokens: list[str], raw: str) -> list[list[str]]:
    """Full target paths for every element the query selects, highest index first (so
    removing elements one by one keeps the others' indexes valid)."""
    conditions = []
    for part in raw.split("&"):
        name, sep, value = part.partition("=")
        if not sep or not name:
            raise _Failed(f"query {raw!r}: expected attr=value[&attr=value]", bad_request)
        conditions.append((name.split("."), value))
    node: Any = holder[""]
    for i, token in enumerate(tokens):
        if isinstance(node, list):
            break
        node = _child(node, token)
        if node is _ABSENT:
            raise _Failed(f"{'/'.join(tokens[: i + 1])}: no such location")
    else:
        i = len(tokens)
    if not isinstance(node, list):
        raise _Failed("a query needs an array on the path")
    array_path, rest = [""] + tokens[:i], tokens[i:]
    selected = [n for n, element in enumerate(node) if _matches(element, conditions)]
    if not selected:
        raise _Failed(f"query {raw!r} selects nothing")
    return [array_path + [str(n)] + rest for n in reversed(selected)]


def _matches(element: Any, conditions: list[tuple[list[str], str]]) -> bool:
    for names, expected in conditions:
        value = element
        for name in names:
            value = value.get(name, _ABSENT) if isinstance(value, dict) else _ABSENT
        if value is _ABSENT or _text(value) != expected:
            return False
    return True


def _text(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, Decimal)):
        return str(value)
    return None


def _equal(a: Any, b: Any) -> bool:
    """JSON equality (RFC 6902 test): numbers by value, never a bool for a number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_equal, a, b))
    return a == b
