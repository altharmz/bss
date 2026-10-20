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
means removing its line there.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.api import common
from tests import apidata, contract, timeline
from tests.apiclient import APIS, MERGE_PATCH
from tests.seed import seed

CONTROL = "zzProbe"
# The specs' base paths are ours, per API (tests/apiclient.py)
BASES = {spec: base for base, (spec, _) in APIS.items()}
SPECS = ("TMF620", "TMF622", "TMF629", "TMF632", "TMF637", "TMF666", "TMF676", "TMF678")


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
    if api == "TMF676":  # Swagger 2: a body parameter
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
            if path.startswith("/listener/"):
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


def probe_writes(client: TestClient, server_set: set[str] | None = None) -> list[str]:
    """The app tells which attributes a request took as server-set (`on_server_set`):
    those are compared whole, so the level below them is not probed. `server_set`, if
    given, collects them as "<api> <method> <path> <name>"."""
    taken: set[str] = set()
    common.on_server_set = taken.update
    try:
        return _probe_writes(client, taken, set() if server_set is None else server_set)
    finally:
        common.on_server_set = None


def _probe_writes(client: TestClient, taken: set[str], server_set: set[str]) -> list[str]:
    lines = []
    for api, method, path, schema in write_operations():
        url = _url(api, path)
        label = f"{api} {method} {path}"
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


def probe_reads(client: TestClient) -> list[str]:
    lines = []
    for api in SPECS:
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


def populate(api: Any) -> None:
    """Data for the nested read probe: the demo seed (tests/seed.py), and one of each
    resource it does not create."""
    seed(api, api.clock, timeline.TODAY)
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
    for url, body in (
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


_BILL_API = "/tmf-api/customerBillManagement/v5"


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
        if not page.json() or len(items) >= int(page.headers["X-Total-Count"]):
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


def probe_patch_formats(client: TestClient) -> list[str]:
    """Every content type a spec's PATCH lists must reach the body's validation (the
    control attribute refused), not a 415."""
    lines = []
    for api in SPECS:
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


def probe_base_paths() -> list[str]:
    """The API name and version of the spec's own server URL (`servers`, v4 `basePath`)
    are ours. The prefix before them is the deployment's apiRoot."""
    lines = []
    for api in SPECS:
        doc = contract.spec(api)
        url = doc.get("basePath") or doc["servers"][0]["url"]
        path = urlsplit(url.replace("{apiRoot}", "")).path.strip("/")
        tail = "/".join(path.split("/")[-2:])
        if tail and not BASES[api].endswith(f"/{tail}"):
            lines.append(f"{api} base path .../{tail} (ours {BASES[api]})")
    return lines


def gaps(api: Any) -> list[str]:
    """`api`: a TmfClient with a clock (the `api` fixture) on an empty database. The
    writes are probed first, on the empty database; then it is populated for the nested
    reads."""
    client = api.client
    server_set: set[str] = set()
    writes = probe_writes(client, server_set)
    found = writes + probe_reads(client) + probe_patch_formats(client)
    populate(api)
    found += probe_nested_reads(client, writes, server_set)
    return sorted(set(found + probe_base_paths()))
