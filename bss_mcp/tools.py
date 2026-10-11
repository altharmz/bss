"""The tools: one per bss API operation, and how a call becomes an HTTP request.

Slice 1 carries a small hand-written set (a list, two creates, a PATCH); slice 3 replaces
`TOOLS` with tools generated from app.openapi() and a drift check.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from app.api.bill import BillPatch
from app.api.party import IndividualCreate
from app.api.payment import PaymentCreate
from app.api.tmfjson import dumps, loads
from bss_mcp import schema

MERGE_PATCH = "application/merge-patch+json"
PARAM = re.compile(r"\{([a-z_]+)\}")

LIST_ARGUMENTS: dict[str, Any] = {
    "offset": {"type": "integer", "minimum": 0, "description": "Items to skip (paging)."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Page size."},
    "fields": {
        "type": "string",
        "description": "Comma-separated attributes to return (id, href, @type always are).",
    },
    "filters": {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "description": "Equality filters, attribute -> value (TMF630).",
    },
}


@dataclass(frozen=True)
class Tool:
    name: str
    method: str
    path: str  # the app's path template, {param} per path parameter
    description: str
    body: type[BaseModel] | None = None
    is_list: bool = False
    input_schema: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name in PARAM.findall(self.path):
            properties[name] = {"type": "string", "minLength": 1}
            required.append(name)
        if self.is_list:
            properties.update(LIST_ARGUMENTS)
        if self.body is not None:
            properties["body"] = schema.model_schema(self.body)
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
        if self.is_list:
            params.update(arguments.get("filters", {}))
            for name in ("offset", "limit", "fields"):
                if name in arguments:
                    params[name] = str(arguments[name])
        body = content_type = None
        if self.body is not None:
            wire = schema.to_wire(arguments["body"], self.input_schema["properties"]["body"])
            body = dumps(wire).encode()
            content_type = MERGE_PATCH if self.method == "PATCH" else "application/json"
        return {
            "method": self.method,
            "path": path,
            "params": params,
            "body": body,
            "content_type": content_type,
        }


def result_text(status: int, headers: dict[str, str], content: bytes) -> str:
    """The app's answer for the model: status, list counts, and the body with every
    amount exact (parsed and re-encoded without float)."""
    out: dict[str, Any] = {"status": status}
    for header, key in (("x-total-count", "totalCount"), ("x-result-count", "resultCount")):
        if header in headers:
            out[key] = int(headers[header])
    if content:
        try:
            out["body"] = loads(content)
        except ValueError:
            out["body"] = content.decode(errors="replace")
    try:
        return dumps(out)
    except TypeError:
        return json.dumps(out, default=str)


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "list_customers",
            "GET",
            "/tmf-api/customerManagement/v5/customer",
            "List customers (TMF629).",
            is_list=True,
        ),
        Tool(
            "create_individual",
            "POST",
            "/tmf-api/partyManagement/v5/individual",
            "Create an individual party (TMF632).",
            body=IndividualCreate,
        ),
        Tool(
            "create_payment",
            "POST",
            "/tmf-api/paymentManagement/v4/payment",
            "Record a payment on a billing account (TMF676).",
            body=PaymentCreate,
        ),
        Tool(
            "patch_bill",
            "PATCH",
            "/tmf-api/customerBillManagement/v5/customerBill/{bill_id}",
            "Hold or release a customer bill (TMF678, JSON merge patch).",
            body=BillPatch,
        ),
    )
}
