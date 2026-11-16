"""The exact TMF gap list: what the vendored specs define and the app does not take or give.

Black-box, through the app itself (docs/decisions/tmf-api.md, "Following TMF"):

- **Writes.** For every POST and PATCH of the specs (not `/listener/*`), every attribute of
  the request schema (`_FVO`, `_MVO`, v4 `_Create`) is sent alone, and one level deeper
  for object and array attributes. The app answers "<path>: unknown attribute" (the
  attribute is **refused**) or "<path> is read-only" (**read-only**), or neither (taken).
  A control attribute, `zzProbe`, must be refused at the same place, else the place is
  **unprobed** - the handler failed before it looked at the body there. An attribute the
  server sets (a balance, a job's status) is taken when it equals the server's value and
  is a 422 otherwise; it counts as taken, and nothing below it is probed.
- **Required.** An empty body: every attribute the app reports missing that the spec's
  request schema does not require is listed as **requires**.
- **Reads.** For every resource with a GET by id, every top-level attribute of the spec's
  resource schema is asked for with `fields=`: an unknown field means the app never
  returns it (**missing**). Nested response attributes are not probed.
- **PATCH formats.** Every content type a PATCH lists reaches the body (not a 415).
- **Base paths.** The API name and version of the spec's server URL are ours.

Behaviour - states reached, values rewritten, order kept - is not probed here; those
gaps are listed in docs/tmf-gap-analysis.md.

`tests/test_tmf_gaps.py` compares the result with `tests/tmf-gaps.txt`; closing a gap
means removing its line there. It runs in parts (`PARTS`, `part_of`) that xdist spreads
over its workers: one per API on an empty database, and `nested`, which alone needs the
populated one. Every line starts with its API, so the parts never overlap.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import psycopg
from fastapi.testclient import TestClient

from app.api import common
from app.config import settings
from tests import apidata, contract, factories, timeline
from tests.apiclient import APIS, MERGE_PATCH
from tests.seed import seed

CONTROL = "zzProbe"
# The specs' base paths are ours, per API (tests/apiclient.py)
BASES = {spec: base for base, (spec, _) in APIS.items()}
SPECS = (
    "TMF620",
    "TMF622",
    "TMF629",
    "TMF632",
    "TMF637",
    "TMF639",
    "TMF654",
    "TMF666",
    "TMF676",
    "TMF678",
    "TMF683",
    "TMF621",
)
# Resources (first path segment) whose operations are not implemented yet: each is one
# "endpoint absent" line, and the attribute probes skip it. Remove a resource here when
# its routes land, which enables those probes.
# TMF621: simple cases have no templates (docs/decisions/care.md)
PENDING: dict[str, frozenset[str]] = {"TMF621": frozenset({"troubleTicketSpecification"})}
NESTED = "nested"
PARTS = (*SPECS, NESTED)
# probe_nested_reads' lines: one level into a response attribute
_NESTED_LINE = re.compile(r"\S+ GET \S+ missing \S+\.\S+")


def part_of(line: str) -> str:
    """The part of `gaps` that reports `line`: `nested`, else the API it starts with."""
    return NESTED if _NESTED_LINE.fullmatch(line) else line.split(" ", 1)[0]


def server_path(doc: dict[str, Any]) -> str:
    """Resolve OAS server variable defaults before extracting the path (Swagger: basePath)."""
    if "basePath" in doc:
        return doc["basePath"]
    server = doc["servers"][0]
    url = server["url"]
    for name, variable in server.get("variables", {}).items():
        url = url.replace("{" + name + "}", variable["default"])
    return urlsplit(url).path


def is_pending(api: str, path: str) -> bool:
    return path.split("/")[1] in PENDING.get(api, ())


def pending_operations(spec: str | None = None):
    for api in PENDING:
        if spec is not None and api != spec:
            continue
        for path, item in contract.spec(api)["paths"].items():
            if not is_pending(api, path):
                continue
            for method in ("get", "post", "patch", "delete"):
                if method in item:
                    yield api, BASES[api], method.upper(), path


def probe_pending_operations(client: TestClient, spec: str | None = None) -> list[str]:
    lines = []
    for api, base, method, path in pending_operations(spec):
        url = base + path.replace("{id}", str(uuid.uuid4()))
        response = client.request(method, url, json={})
        assert response.status_code == 404, (
            f"{api} {method} {path} is no longer absent: enable its attribute probes"
        )
        lines.append(f"{api} {method} {path} endpoint absent")
    return lines


def _resolve(api: str, schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        ref = schema["$ref"]
        node: Any = contract.spec(api)
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def properties(api: str, schema: dict[str, Any], seen: frozenset[str] = frozenset()) -> dict:
    """Name -> schema of every property, through allOf and the branches of oneOf/anyOf
    (a RefOrValue is a reference or a value: both shapes are accepted on input)."""
    key = schema.get("$ref")
    if key in seen:
        return {}
    seen = seen | {key} if key else seen
    schema = _resolve(api, schema)
    found: dict[str, Any] = {}
    for part in (*schema.get("allOf", ()), *schema.get("oneOf", ()), *schema.get("anyOf", ())):
        found.update(properties(api, part, seen))
    found.update(schema.get("properties", {}))
    return found


def required(api: str, schema: dict[str, Any]) -> set[str]:
    schema = _resolve(api, schema)
    names = set(schema.get("required", ()))
    for part in schema.get("allOf", ()):
        names |= required(api, part)
    return names


def _shape(api: str, schema: dict[str, Any]) -> str | None:
    """'object' or 'array' (of objects) when the attribute has attributes of its own."""
    schema = _resolve(api, schema)
    if schema.get("type") == "array":
        return "array" if properties(api, schema.get("items", {})) else None
    return "object" if properties(api, schema) else None


def _element(api: str, schema: dict[str, Any]) -> dict[str, Any]:
    schema = _resolve(api, schema)
    return schema.get("items", schema) if schema.get("type") == "array" else schema


def request_schema(api: str, operation: dict[str, Any]) -> dict[str, Any] | None:
    if "swagger" in contract.spec(api):  # Swagger 2 (the v4 APIs): a body parameter
        for parameter in operation.get("parameters", ()):
            if parameter.get("in") == "body":
                return parameter["schema"]
        return None
    body = operation.get("requestBody")
    if body is None:
        return None
    content = _resolve(api, body)["content"]
    return next(iter(content.values()))["schema"]


def write_operations() -> Iterator[tuple[str, str, str, dict[str, Any]]]:
    """(api, method, spec path, request schema) of every POST and PATCH."""
    for api in SPECS:
        for path, item in contract.spec(api)["paths"].items():
            if path.startswith("/listener/") or is_pending(api, path):
                continue
            for method in ("post", "patch"):
                if method in item:
                    schema = request_schema(api, item[method])
                    if schema is not None:
                        yield api, method.upper(), path, schema


def _url(api: str, path: str) -> str:
    return BASES[api] + path.replace("{id}", str(uuid.uuid4()))


def _send(client: TestClient, method: str, url: str, body: dict[str, Any]) -> tuple[int, str]:
    content_type = MERGE_PATCH if method == "PATCH" else "application/json"
    response = client.request(method, url, json=body, headers={"Content-Type": content_type})
    if response.status_code < 300:
        return response.status_code, ""
    return response.status_code, response.json().get("message", "")


def _verdict(message: str, where: str) -> str | None:
    if f"{where}: unknown attribute" in message:
        return "refused"
    if f"{where} is read-only" in message:
        return "read-only"
    return None


def _value(shape: str | None, inner: dict[str, Any] | None = None) -> Any:
    if shape == "array":
        return [inner or {}]
    if shape == "object":
        return inner or {}
    return "probe"


def probe_writes(
    client: TestClient,
    server_set: set[str] | None = None,
    only: frozenset[str] | None = None,
) -> list[str]:
    """The app tells which attributes a request took as server-set (`on_server_set`):
    those are compared whole, so the level below them is not probed. `server_set`, if
    given, collects them as "<api> <method> <path> <name>". `only`, if given, limits the
    probe to those operations ("<api> <method> <path>")."""
    taken: set[str] = set()
    common.on_server_set = taken.update
    try:
        return _probe_writes(client, taken, set() if server_set is None else server_set, only)
    finally:
        common.on_server_set = None


def _probe_writes(
    client: TestClient, taken: set[str], server_set: set[str], only: frozenset[str] | None
) -> list[str]:
    lines = []
    for api, method, path, schema in write_operations():
        label = f"{api} {method} {path}"
        if only is not None and label not in only:
            continue
        url = _url(api, path)
        _, message = _send(client, method, url, {CONTROL: 1})
        if _verdict(message, CONTROL) != "refused":
            lines.append(f"{label} unprobed")
            continue
        props = properties(api, schema)
        for name, prop in sorted(props.items()):
            if name.startswith("@"):  # @type, @baseType, @schemaLocation: TmfModel
                continue
            shape = _shape(api, prop)
            taken.clear()
            _, message = _send(client, method, url, {name: _value(shape)})
            verdict = _verdict(message, name)
            if verdict:
                lines.append(f"{label} {verdict} {name}")
                continue
            if name in taken:
                server_set.add(f"{label} {name}")
            if shape is None or name in taken:
                continue  # a server-set attribute is compared whole: nothing below it
            index = ".0" if shape == "array" else ""
            _, message = _send(client, method, url, {name: _value(shape, {CONTROL: 1})})
            if _verdict(message, f"{name}{index}.{CONTROL}") != "refused":
                lines.append(f"{label} unprobed {name}")
                continue
            for sub in sorted(properties(api, _element(api, prop))):
                if sub.startswith("@") or sub in ("id", "href", "name"):  # Ref: always taken
                    continue
                _, message = _send(client, method, url, {name: _value(shape, {sub: "probe"})})
                verdict = _verdict(message, f"{name}{index}.{sub}")
                if verdict:
                    lines.append(f"{label} {verdict} {name}.{sub}")
        if method == "POST":
            _, message = _send(client, method, url, {})
            spec_required = required(api, schema)
            for part in message.split("; "):
                if part.endswith(": Field required"):
                    name = part.removesuffix(": Field required")
                    if name not in spec_required:
                        lines.append(f"{label} requires {name}")
    return lines


def probe_reads(client: TestClient, spec: str | None = None) -> list[str]:
    lines = []
    for api in SPECS:
        if spec is not None and api != spec:
            continue
        for resource, schema_name in APIS[BASES[api]][1].items():
            url = f"{BASES[api]}/{resource}/{uuid.uuid4()}"
            label = f"{api} GET /{resource}/{{id}}"
            status, message = _send_get(client, f"{url}?fields={CONTROL}")
            if status == 405:  # no read operation (a hub)
                continue
            if "unknown attribute" not in message:
                lines.append(f"{label} unprobed")
                continue
            doc = contract.spec(api)
            schemas = doc.get("components", {}).get("schemas") or doc["definitions"]
            for name in sorted(properties(api, schemas[schema_name])):
                _, message = _send_get(client, f"{url}?fields={name}")
                if "unknown attribute" in message:
                    lines.append(f"{label} missing {name}")
    return lines


# The history the nested read probe seeds. The probe looks at which attributes the
# resources carry, not at how many there are: from 25 days on (the first bills, which the
# seed's last day needs) the lines are the ones of the demo's 120 days (#373), and a day
# costs a billing run. If a probed attribute ever needs a later event, the comparison
# with tests/tmf-gaps.txt fails: lengthen the history then.
SEED_DAYS = 30


def populate(api: Any) -> None:
    """Data for the nested read probe: the demo seed (tests/seed.py) over a short
    history (`SEED_DAYS`), and one of each resource it does not create."""
    seed(api, api.clock, timeline.TODAY, days=SEED_DAYS)
    (party, *_) = api.get(f"{apidata.PARTY}/individual").json()
    (cust, *_) = api.get(apidata.CUSTOMER).json()
    acc = apidata.account(api, cust, name="Probe")
    apidata.payment(api, acc, 100)
    payout = {
        "account": {"id": acc["id"]},
        "totalAmount": {"unit": "USD", "value": 20},
        "paymentMethod": {"name": "cash payout", "@type": "Cash"},
    }
    offering = _versioned_offering(api)
    starts = timeline.iso(timeline.START + timedelta(days=5))
    apidata.order(api, acc, apidata.add_item(offering), requestedStartDate=starts)
    placed = apidata.order(api, acc, apidata.add_item(offering), requestedStartDate=starts)
    owner = {
        "@type": "RelatedPartyRefOrPartyRoleRef",
        "role": "owner",
        "partyOrPartyRole": {"id": party["id"], "@type": "PartyRef", "@referredType": "Individual"},
    }
    account_api = "/tmf-api/accountManagement/v5"
    export = _created(api, f"{apidata.CATALOG}/exportJob", {"@type": "ExportJob"})
    _prepaid(api, cust)
    for url, body in (
        (
            "/tmf-api/resourceInventory/v5/resource",
            {"@type": "LogicalResource", "name": "Probe number", "value": "12025550123"},
        ),
        ("/tmf-api/payment/v4/refund", payout),
        (
            "/tmf-api/productOrderingManagement/v5/cancelProductOrder",
            {"@type": "CancelProductOrder", "productOrder": {"id": placed["id"]}},
        ),
        (f"{apidata.CATALOG}/importJob", {"@type": "ImportJob", "url": export["url"]}),
        (f"{account_api}/billFormat", {"@type": "BillFormat", "name": "JSON summary"}),
        (
            f"{account_api}/billPresentationMedia",
            {"@type": "BillPresentationMedia", "name": "Portal"},
        ),
        (f"{account_api}/financialAccount", {"name": "Probe", "relatedParty": [owner]}),
        (f"{account_api}/partyAccount", {"name": "Probe", "relatedParty": [owner]}),
        (f"{account_api}/settlementAccount", {"name": "Probe", "relatedParty": [owner]}),
        (f"{_BILL_API}/customerBillOnDemand", {"billingAccount": {"id": acc["id"]}}),
    ):
        _created(api, url, body)
    _care(api, cust, acc)


def _care(api: Any, cust: dict[str, Any], acc: dict[str, Any]) -> None:
    """An interaction and a ticket that refer to each other, the account and an audit entry,
    with notes and a status history (TMF683, TMF621 reads)."""
    customer = [{"role": "customer", "partyOrPartyRole": {"id": cust["id"]}}]
    account = {"role": "billing", "entity": {"id": acc["id"], "@referredType": "BillingAccount"}}
    ticket = _created(
        api,
        "/tmf-api/troubleTicket/v5/troubleTicket",
        {
            "description": "Probe case",
            "severity": "minor",
            "ticketType": "request",
            "relatedParty": customer,
            "channel": {"id": "phone"},
            "relatedEntity": [account],
            "note": [{"text": "probe"}],
        },
    )
    (entry, *_) = api.get("/internal/v1/security/audit?action=ticket_opened").json()
    interaction = _created(
        api,
        "/tmf-api/partyInteractionManagement/v5/partyInteraction",
        {
            "direction": "inbound",
            "reason": "Probe",
            "interactionDate": {"startDateTime": timeline.iso(timeline.START)},
            "relatedChannel": [{"role": "interaction", "channel": {"id": "chat"}}],
            "relatedParty": customer,
            "note": [{"text": "probe"}],
            "interactionItem": [
                {"item": account},
                {
                    "item": {
                        "role": "case",
                        "entity": {"id": ticket["id"], "@referredType": "TroubleTicket"},
                    }
                },
                {
                    "item": {
                        "role": "audit",
                        "entity": {"id": entry["id"], "@referredType": "AuditEntry"},
                    }
                },
            ],
        },
    )
    moved = api.patch(
        ticket["href"],
        {
            "status": "inProgress",
            "relatedEntity": [
                account,
                {
                    "role": "call",
                    "entity": {"id": interaction["id"], "@referredType": "PartyInteraction"},
                },
            ],
        },
    )
    assert moved.status_code == 200, moved.text
    held = api.patch(ticket["href"], {"status": "held", "statusChangeReason": "Probe"})
    assert held.status_code == 200, held.text


_BILL_API = "/tmf-api/customerBillManagement/v5"


def _prepaid(api: Any, cust: dict[str, Any]) -> None:
    """A prepaid bucket with a payment and an outstanding hold (TMF654 reads). Prepaid
    accounts cannot be created through the API yet, so the mode is set in the database."""
    acc = apidata.account(api, cust, name="Prepaid probe", description="Probe prepaid account")
    with psycopg.connect(settings.database_url) as conn:
        factories.prepaid_bucket(conn, acc["id"], api.clock.now())
    paid = apidata.payment(api, acc, 50)
    with psycopg.connect(settings.database_url) as conn:
        factories.payment_hold(conn, acc["id"], paid["id"], 10, api.clock.now())
    bucket = api.get(f"/tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id={acc['id']}")
    adjusted = api.post(
        "/tmf-api/prepayBalanceManagement/v4/adjustBalance",
        {
            "@type": "AdjustBalance",
            "amount": {"amount": 5, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": bucket.json()[0]["id"]},
        },
    )
    assert adjusted.status_code == 201, adjusted.text
    held = api.post(
        "/tmf-api/prepayBalanceManagement/v4/reserveBalance",
        {
            "@type": "ReserveBalance",
            "amount": {"amount": 2, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": bucket.json()[0]["id"]},
            "partyAccount": {"id": acc["id"]},
        },
    )
    assert held.status_code == 201, held.text
    # a transfer to a second prepaid account; the number is nobody's, so it is stored failed
    number = _created(
        api,
        "/tmf-api/resourceInventory/v5/resource",
        {"@type": "LogicalResource", "name": "Transfer number", "value": "12025550199"},
    )
    other = apidata.account(api, cust, name="Prepaid receiver", description="Probe receiver")
    with psycopg.connect(settings.database_url) as conn:
        receiver = factories.prepaid_bucket(conn, other["id"], api.clock.now())
    moved = api.post(
        "/tmf-api/prepayBalanceManagement/v4/transferBalance",
        {
            "@type": "TransferBalance",
            "reason": "Probe transfer",
            "channel": {"id": "probe", "name": "Probe"},
            "logicalResource": [{"id": number["id"]}],
            "receiverLogicalResource": {"id": number["id"]},
            "amount": {"amount": 1, "units": "USD"},
            "usageType": "monetary",
            "bucket": {"id": bucket.json()[0]["id"]},
            "receiverBucket": {"id": str(receiver["id"])},
            "receiverBucketUsageType": "monetary",
        },
    )
    assert moved.status_code == 201, moved.text


def _versioned_offering(api: Any) -> dict[str, Any]:
    """An offering whose references carry what the seed's never do: versions, a target
    product schema, a price's unit of measure."""
    spec = _created(
        api,
        f"{apidata.CATALOG}/productSpecification",
        {
            "@type": "ProductSpecification",
            "name": "Probe",
            "lifecycleStatus": "Active",
            "version": "1.0",
            "targetProductSchema": {
                "@type": "ProbeProduct",
                "@schemaLocation": "https://example.com/probe.json",
            },
        },
    )
    price = _created(
        api,
        f"{apidata.CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Probe monthly",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "monthly",
            "price": {"unit": "USD", "value": 100},
            "unitOfMeasure": {"amount": 1, "units": "line"},
            "validFor": {"startDateTime": apidata.START},
        },
    )
    return _created(
        api,
        f"{apidata.CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Probe",
            "version": "1.0",
            "lifecycleStatus": "Active",
            "validFor": {"startDateTime": apidata.START},
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [{"id": price["id"], "@type": "ProductOfferingPriceRef"}],
        },
    )


def _created(api: Any, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = api.post(url, body)
    assert response.status_code == 201, f"{url}: {response.text}"
    return response.json()


def _every(client: TestClient, url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        page = client.get(f"{url}?offset={len(items)}&limit=100")
        assert page.status_code == 200, f"{url}: {page.text}"
        items += page.json()
        if 'rel="next"' not in page.headers.get("Link", ""):
            return items


def probe_nested_reads(
    client: TestClient,
    writes: list[str],
    server_set: set[str],
    only: frozenset[str] | None = None,
) -> list[str]:
    """One level into responses, on populated data (`populate`): for every object or
    array attribute some resource returns, each attribute of its spec schema that no
    returned value carries is **missing** - unless POST takes it into a stored value
    (stored as sent, returned as sent: an unset optional attribute is no gap). An
    attribute POST takes as server-set (compared, not stored) is probed. Attributes no
    resource returns a value for are not probed below their top level."""
    lines = []
    for api in SPECS:
        doc = contract.spec(api)
        schemas = doc.get("components", {}).get("schemas") or doc["definitions"]
        for resource, schema_name in APIS[BASES[api]][1].items():
            if resource == "hub" or (only is not None and resource not in only):
                continue
            items = _every(client, f"{BASES[api]}/{resource}")
            assert items, f"{api} {resource}: no data to probe (populate)"
            post = f"{api} POST /{resource}"
            request = next(
                (
                    s
                    for a, m, p, s in write_operations()
                    if (a, m, p) == (api, "POST", f"/{resource}")
                ),
                None,
            )
            request_props = properties(api, request) if request else {}
            for name, prop in sorted(properties(api, schemas[schema_name]).items()):
                if _shape(api, prop) is None:
                    continue
                values = [item[name] for item in items if item.get(name) not in (None, [])]
                if not values:
                    continue
                seen: set[str] = set()
                for value in values:
                    for element in value if isinstance(value, list) else [value]:
                        seen |= set(element) if isinstance(element, dict) else set()
                stored: dict[str, Any] = {}
                if name in request_props and f"{post} {name}" not in server_set:
                    stored = properties(api, _element(api, request_props[name]))
                for sub in sorted(properties(api, _element(api, prop))):
                    if sub.startswith("@") or sub in seen:
                        continue
                    refused = any(
                        line.startswith(f"{post} ")
                        and line.endswith((f" {name}", f" {name}.{sub}"))
                        for line in writes
                    )
                    if sub not in stored or refused:
                        lines.append(f"{api} GET /{resource}/{{id}} missing {name}.{sub}")
    return lines


def _send_get(client: TestClient, url: str) -> tuple[int, str]:
    response = client.get(url)
    message = response.json().get("message", "") if response.status_code >= 300 else ""
    return response.status_code, message


def probe_patch_formats(client: TestClient, spec: str | None = None) -> list[str]:
    """Every content type a spec's PATCH lists must reach the body's validation (the
    control attribute refused), not a 415."""
    lines = []
    for api in SPECS:
        if spec is not None and api != spec:
            continue
        for path, item in contract.spec(api)["paths"].items():
            if "patch" not in item or "requestBody" not in item["patch"]:
                continue
            content = _resolve(api, item["patch"]["requestBody"])["content"]
            for content_type in sorted(content):
                response = client.request(
                    "PATCH",
                    _url(api, path),
                    content=b'{"zzProbe": 1}',
                    headers={"Content-Type": content_type},
                )
                if response.status_code == 415:
                    lines.append(f"{api} PATCH {path} content-type {content_type}")
    return lines


def probe_base_paths(spec: str | None = None) -> list[str]:
    """The API name and version of the spec's own server URL (`servers`, v4 `basePath`)
    are ours. The prefix before them is the deployment's apiRoot."""
    lines = []
    for api, base in BASES.items():
        if spec is not None and api != spec:
            continue
        doc = contract.spec(api)
        path = server_path(doc).strip("/")
        tail = "/".join(path.split("/")[-2:])
        if tail and not base.endswith(f"/{tail}"):
            lines.append(f"{api} base path .../{tail} (ours {base})")
    return lines


def _operations(spec: str | None = None, method: str | None = None) -> frozenset[str]:
    return frozenset(
        f"{a} {m} {p}"
        for a, m, p, _ in write_operations()
        if (spec is None or a == spec) and (method is None or m == method)
    )


def gaps(api: Any, part: str) -> list[str]:
    """The lines of one of `PARTS`. `api`: a TmfClient with a clock (the `api` fixture)
    on an empty database. An API's part probes it on that empty database. `nested`
    probes the POSTs there first (the nested reads need their verdicts and server-set
    attributes), then populates it for the nested reads."""
    client = api.client
    if part == NESTED:
        server_set: set[str] = set()
        writes = probe_writes(client, server_set, only=_operations(method="POST"))
        populate(api)
        return sorted(set(probe_nested_reads(client, writes, server_set)))
    found = probe_writes(client, only=_operations(part))
    found += probe_reads(client, part) + probe_patch_formats(client, part)
    found += probe_pending_operations(client, part) + probe_base_paths(part)
    return sorted(set(found))
