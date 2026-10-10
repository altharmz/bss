"""A TestClient wrapper that contract-checks every response it sees.

Every body that comes back through this client is validated against the vendored TMF
schema for its path: the resource schema for 2xx, `Error` for everything else. A test
therefore cannot pass with a response that violates the spec, whatever it asserts itself.
"""

import json
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from jsonschema import Draft7Validator

from tests import contract

MERGE_PATCH = "application/merge-patch+json"

# base path -> (spec, {resource: response schema})
APIS: dict[str, tuple[str, dict[str, str]]] = {
    "/tmf-api/productCatalogManagement/v5": (
        "TMF620",
        {
            "productSpecification": "ProductSpecification",
            "productOffering": "ProductOffering",
            "productOfferingPrice": "ProductOfferingPrice",
        },
    ),
    "/tmf-api/partyManagement/v5": (
        "TMF632",
        {"individual": "Individual", "organization": "Organization"},
    ),
    "/tmf-api/customerManagement/v5": ("TMF629", {"customer": "Customer"}),
    "/tmf-api/accountManagement/v5": ("TMF666", {"billingAccount": "BillingAccount"}),
    "/tmf-api/paymentManagement/v4": ("TMF676", {"payment": "Payment"}),
    "/tmf-api/productInventory/v5": ("TMF637", {"product": "Product"}),
    "/tmf-api/productOrderingManagement/v5": ("TMF622", {"productOrder": "ProductOrder"}),
    "/tmf-api/customerBillManagement/v5": (
        "TMF678",
        {
            "customerBill": "CustomerBill",
            "appliedCustomerBillingRate": "AppliedCustomerBillingRate",
        },
    ),
}
# Our internal API (spec/internal/bss-internal.oas.yaml): spec path -> response schema
# (of the items, for a list).
INTERNAL_BASE = "/internal/v1"
INTERNAL: dict[str, str] = {
    "/context": "Context",
    "/billingAccount/{id}/firstCharge": "FirstCharge",
    "/billingAccount/{id}/products": "AccountProduct",
    "/billingAccount/{id}/charges": "Charge",
    "/billingAccount/{id}/settlement": "AccountSettlement",
    "/customerBill/{id}": "BillDetail",
    "/billingAccount/{id}/transactions": "Transaction",
    "/productOffering/{id}/priceChange": "PriceChange",
    "/billingRun": "BillingRun",
    "/billingRun/{id}": "BillingRun",
    "/billingRun/{id}/accounts": "RunAccount",
    "/billingRun/catchUp": "CatchUp",
    "/billingRun/{id}/retry": "RetryQueued",
}
# Our own JSON Schemas for TMF extensions, served outside the TMF resource paths.
SCHEMA_PREFIX = "/tmf-api/schema/"
# Statuses we return that the specs do not declare on any operation (decided in CLAUDE.md).
UNDECLARED_BUT_OURS = {"415", "422"}


def decimals(response: Response) -> Any:
    """The body with every non-integer number as an exact Decimal."""
    return json.loads(response.text, parse_float=Decimal)


class TmfClient:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.checked = 0

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        response = self.client.request(method, url, **kwargs)
        self._check(method, url.split("?")[0], response, partial="fields=" in url)
        return response

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, body: Any, **kwargs: Any) -> Response:
        return self.request("POST", url, json=body, **kwargs)

    def post_raw(self, url: str, text: str, content_type: str = "application/json") -> Response:
        """Send the body text verbatim - for number literals the json module would alter."""
        return self.request("POST", url, content=text, headers={"Content-Type": content_type})

    def patch(self, url: str, body: Any, content_type: str = MERGE_PATCH) -> Response:
        return self.request(
            "PATCH", url, content=json.dumps(body), headers={"Content-Type": content_type}
        )

    def _check(self, method: str, path: str, response: Response, partial: bool = False) -> None:
        self.checked += 1
        assert response.headers["content-type"] == "application/json", path
        body = response.json()
        if path.startswith(SCHEMA_PREFIX) and response.status_code == 200:
            Draft7Validator.check_schema(body)
            return
        api, resource, schema, spec_path = self._locate(path)

        if response.status_code >= 400:
            contract.assert_valid(api, "Error", body)
            assert "detail" not in body, "FastAPI's default error body leaked"
            return

        assert schema is not None, f"2xx from a path with no schema: {path}"
        declared = contract.declared_statuses(api, spec_path, method.lower())
        assert str(response.status_code) in declared, (
            f"{method} {spec_path} answered {response.status_code}, spec declares {declared}"
        )
        if isinstance(body, list):
            assert "X-Total-Count" in response.headers and "X-Result-Count" in response.headers
            for item in body:
                self._validate(api, schema, item, partial)
        else:
            self._validate(api, schema, body, partial)

    def _validate(self, api: str, schema: str, body: dict[str, Any], partial: bool) -> None:
        contract.assert_valid(api, schema, body)
        if api == "TMF666" and not partial:
            # A TMF extension: the body must name the schema of its additions, the app
            # must serve it, and the body must satisfy it. (A fields= selection leaves
            # attributes out on request, so only full bodies are checked.)
            location = body.get("@schemaLocation")
            assert location, "an extended resource must carry @schemaLocation"
            extension = self.client.get(location)
            assert extension.status_code == 200, f"@schemaLocation {location} is not served"
            problems = [e.message for e in Draft7Validator(extension.json()).iter_errors(body)]
            assert not problems, f"body does not conform to {location}: {problems}"

    @staticmethod
    def _locate(path: str) -> tuple[str, str | None, str | None, str]:
        if path.startswith(INTERNAL_BASE + "/"):
            parts = path[len(INTERNAL_BASE) + 1 :].split("/")
            if len(parts) == 3:  # /<resource>/{id}/<view or action>
                parts[1] = "{id}"
            elif len(parts) == 2 and "/" + "/".join(parts) not in INTERNAL:
                parts[1] = "{id}"  # /<resource>/{id}, unless a literal path (/billingRun/catchUp)
            spec_path = "/" + "/".join(parts)
            return "INTERNAL", parts[0], INTERNAL.get(spec_path), spec_path
        for base, (api, resources) in APIS.items():
            if path.startswith(base + "/"):
                parts = path[len(base) + 1 :].split("/")
                spec_path = f"/{parts[0]}" + ("/{id}" if len(parts) > 1 else "")
                return api, parts[0], resources.get(parts[0]), spec_path
        # outside every API (unknown route): only an error can come back; any spec's
        # Error schema will do
        return "TMF620", None, None, path
