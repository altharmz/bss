"""The tools: one per bss API operation, generated from app.openapi() and the registry in
bss_mcp/operations.py - and how a call becomes an HTTP request to the app.

Arguments of a tool: its path parameters by name; its query parameters (TMF lists:
offset, limit, fields and a `filters` object whose attributes are the ones the list
accepts; others as the registry says); `body` for a write - the schema of the request
model its handler parses. Amounts are decimal strings (bss_mcp/schema.py).

The list filters are read from the code itself, as tests/docs_collect.py does for the
docs: a list handler answers an unknown query parameter with "unknown query parameter X
(filters: a, b)" before it opens a database connection.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx2
from fastapi import FastAPI
from pydantic import BaseModel

from app.api.tmfjson import dumps, loads
from app.routes import routes
from bss_mcp import schema
from bss_mcp.operations import (
    AUTH_OPERATIONS,
    EXCLUDED,
    FAMILIES,
    FIELDS,
    LIST,
    OPERATIONS,
    PAGE,
    Op,
)

MERGE_PATCH = "application/merge-patch+json"
PARAM = re.compile(r"\{([a-z_]+)\}")
PROBE = "__mcp_probe__"

PAGING: dict[str, Any] = {
    "offset": {"type": "integer", "minimum": 0, "description": "Items to skip (paging)."},
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "description": "Page size (default 20, at most 100).",
    },
}
FIELDS_ARG: dict[str, Any] = {
    "type": "string",
    "description": "Comma-separated top-level attributes to return (id, href, @type always are).",
}
IDEMPOTENCY_ARG: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 255,
    "pattern": r"^[\x21-\x7e]+$",
    "description": (
        "Idempotency-Key: send the same key and body again to get the first answer back"
        " instead of a second action; the same key with a different body is a 409."
    ),
}


ACCOUNT_PIN_ARG: dict[str, Any] = {
    "type": "string",
    "pattern": r"^[0-9]{6,8}$",
    "description": (
        "The customer's account PIN (the X-Account-Pin header), asked by a SIM swap, a number"
        " change or a contact or address change on a customer with a live prepaid line: without"
        " a valid one the app refuses it (pinRequired, pinInvalid, pinNotSet, pinLocked)."
    ),
}


class DriftError(RuntimeError):
    """The registry and the app's operations disagree: the service does not start."""


@dataclass(frozen=True)
class Tool:
    name: str
    method: str
    path: str  # the app's path template, {param} per path parameter
    description: str
    body: type[BaseModel] | None = None
    query: dict[str, Any] = field(default_factory=dict)  # name -> schema (filters: object)
    required_query: tuple[str, ...] = ()
    body_extra: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = False
    account_pin: bool = False
    input_schema: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name in PARAM.findall(self.path):
            properties[name] = {"type": "string", "minLength": 1}
            required.append(name)
        properties.update(self.query)
        required += self.required_query
        if self.idempotent:
            properties["idempotency_key"] = IDEMPOTENCY_ARG
        if self.account_pin:
            properties["account_pin"] = ACCOUNT_PIN_ARG
        if self.body is not None:
            properties["body"] = schema.model_schema(self.body)
            properties["body"]["properties"].update(self.body_extra)
            required.append("body")
        object.__setattr__(
            self,
            "input_schema",
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    @property
    def read_only(self) -> bool:
        return self.method == "GET"

    def request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """The HTTP request for valid arguments: method, path, params, body, content_type."""
        path = PARAM.sub(lambda m: quote(str(arguments[m.group(1)]), safe=""), self.path)
        params: dict[str, str] = {}
        for name in self.query:
            if name == "filters":
                params.update(arguments.get("filters", {}))
            elif name in arguments:
                params[name] = str(arguments[name])
        body = content_type = None
        if self.body is not None:
            wire = schema.to_wire(arguments["body"], self.input_schema["properties"]["body"])
            body = dumps(wire).encode()
            content_type = MERGE_PATCH if self.method == "PATCH" else "application/json"
        headers = {}
        if self.idempotent and "idempotency_key" in arguments:
            headers["Idempotency-Key"] = str(arguments["idempotency_key"])
        if self.account_pin and "account_pin" in arguments:
            headers["X-Account-Pin"] = str(arguments["account_pin"])
        return {
            "method": self.method,
            "path": path,
            "params": params,
            "body": body,
            "content_type": content_type,
            "headers": headers,
        }


def result_text(status: int, headers: dict[str, str], content: bytes) -> str:
    """The app's answer for the model: status, paging (resultCount, and hasMore when the
    app announced a next page with Link rel=next; lists carry no total, #312), and the
    body with every amount exact (parsed and re-encoded without float)."""
    out: dict[str, Any] = {"status": status}
    if "x-result-count" in headers:
        out["resultCount"] = int(headers["x-result-count"])
        out["hasMore"] = 'rel="next"' in headers.get("link", "")
    if content:
        try:
            out["body"] = loads(content)
        except ValueError:
            out["body"] = content.decode(errors="replace")
    try:
        return dumps(out)
    except TypeError:  # pragma: no cover - loads() yields only types dumps() encodes
        return json.dumps(out, default=str)


# --- generation ---


def api_operations(openapi: dict[str, Any]) -> list[dict[str, str]]:
    """Every operation of app.openapi() but the excluded ones."""
    out = []
    for path, operations in openapi["paths"].items():
        for method, op in operations.items():
            if (method.upper(), path) in EXCLUDED | AUTH_OPERATIONS:
                continue
            out.append(
                {
                    "name": op["operationId"],
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary", ""),
                    "doc": (op.get("description") or "").strip(),
                }
            )
    return out


async def list_filters(app: Any, path: str) -> list[str]:
    """The equality filters a TMF list accepts, from the handler's own 400."""
    # Probe only this handler in an isolated ASGI application. The real application's
    # authentication remains intact; no secret, database or network is needed here.
    probe = FastAPI()
    from app.api.errors import install

    install(probe)
    endpoint = next(
        route.endpoint
        for route, _ in routes(app)
        if route.path == path and "GET" in getattr(route, "methods", set())
    )
    probe.add_api_route(path, endpoint, methods=["GET"])
    transport = httpx2.ASGITransport(app=probe)
    async with httpx2.AsyncClient(transport=transport, base_url="http://probe") as client:
        response = await client.get(path, params={PROBE: "1"})
    message = response.json().get("message", "") if response.status_code == 400 else ""
    marker = f"unknown query parameter {PROBE} (filters: "
    if not (message.startswith(marker) and message.endswith(")")):
        raise DriftError(f"{path}: not a TMF list (the probe answered {response.status_code})")
    listed = message[len(marker) : -1]
    return [] if listed == "none" else listed.split(", ")


def _family(path: str) -> str:
    return next(label for prefix, label in FAMILIES.items() if path.startswith(prefix))


async def _query(app: Any, path: str, op: Op) -> tuple[dict[str, Any], str]:
    """The query arguments of an operation, and a sentence about them."""
    query: dict[str, Any] = {}
    hint = ""
    if op.query == LIST:
        filters = await list_filters(app, path)
        if op.filters.keys() - set(filters):
            raise DriftError(f"{path}: registry declares unknown filters")
        query = {
            **PAGING,
            "fields": FIELDS_ARG,
            "filters": {
                "type": "object",
                "properties": {name: op.filters.get(name, {"type": "string"}) for name in filters},
                "additionalProperties": False,
                "description": "Equality filters, attribute -> value.",
            },
        }
        hint = " Paged (resultCount, hasMore: ask again with offset + limit while hasMore)." + (
            f" Filters: {', '.join(filters)}." if filters else ""
        )
    elif op.query == PAGE:
        query = dict(PAGING)
        hint = " Paged (resultCount, hasMore: ask again with offset + limit while hasMore)."
    elif op.query == FIELDS:
        query = {"fields": FIELDS_ARG}
    query.update(op.extra)
    return query, hint


async def generate(app: Any) -> dict[str, Tool]:
    """One tool per operation of `app` (app.main.app). DriftError when the registry and
    the operations disagree - the service then refuses to start."""
    operations = api_operations(app.openapi())
    names = {o["name"] for o in operations}
    missing = sorted(names - OPERATIONS.keys())
    unknown = sorted(OPERATIONS.keys() - names)
    if missing or unknown:
        raise DriftError(
            f"bss_mcp/operations.py is out of date: no entry for {missing}, no operation for"
            f" {unknown}"
        )
    tools: dict[str, Tool] = {}
    for o in operations:
        op = OPERATIONS[o["name"]]
        writes = o["method"] in ("POST", "PATCH")
        if writes != (op.body is not None or op.no_body) or (op.body and op.no_body):
            raise DriftError(f"{o['name']}: a {o['method']} with body {op.body}")
        query, hint = await _query(app, o["path"], op)
        if writes and o["path"].startswith("/tmf-api/"):
            query["fields"] = FIELDS_ARG
        parts = [f"{o['summary']} - {_family(o['path'])}.", op.note]
        if o["doc"]:
            parts.append(" ".join(o["doc"].split()))
        description = " ".join(parts) + hint
        if o["method"] == "PATCH":
            description += (
                " JSON merge patch: send only what changes; null removes an optional attribute."
            )
        if op.idempotent:
            description += (
                " Takes an optional idempotency_key (the Idempotency-Key header): retry with the"
                " same key after a timeout and the first answer is replayed, nothing repeats."
            )
        if op.account_pin:
            description += (
                " Takes an optional account_pin (the customer's account PIN, X-Account-Pin"
                " header) for a SIM swap, number change or contact or address change."
            )
        if o["method"] != "GET":
            description += " Changes data (write scope)."
        if op.body is not None and "decimal" in json.dumps(schema.model_schema(op.body)):
            description += ' Amounts are decimal strings ("19.99").'
        tools[o["name"]] = Tool(
            name=o["name"],
            method=o["method"],
            path=o["path"],
            description=description,
            body=op.body,
            query=query,
            required_query=op.required,
            body_extra=op.body_extra,
            idempotent=op.idempotent,
            account_pin=op.account_pin,
        )
    return tools
