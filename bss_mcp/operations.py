"""What the generated tools need to know beyond app.openapi() (bss_mcp/tools.py).

The app parses query parameters and bodies itself (app/api/listing.py, `parse(Model,
body)`), so app.openapi() carries neither. This registry adds, per operation: the request
model of a write, the query parameters of a read, and a one-line note condensed from the
API notes (scripts/docs/api-notes/, docs/api/) - the handlers mostly have no docstring.

Kept honest by tests/test_mcp_tools.py (the drift check): the operations here are exactly
app.openapi()'s minus EXCLUDED; EXCLUDED is exactly docs/progress/scope.yaml's excluded
api entries; every POST/PATCH names the model its handler parses (read from the handler's
source), and nothing else does.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.api.account import AccountCreate, AccountPatch
from app.api.bill import BillPatch
from app.api.catalog import (
    OfferingCreate,
    OfferingPatch,
    PriceCreate,
    PricePatch,
    SpecificationCreate,
    SpecificationPatch,
)
from app.api.customer import CustomerCreate, CustomerPatch
from app.api.ordering import OrderIn
from app.api.party import IndividualCreate, IndividualPatch, OrganizationCreate, OrganizationPatch
from app.api.payment import PaymentCreate
from app.internal.billing_runs import OUTCOMES, CatchUp
from app.internal.catalog import PriceChange

# Not tools: the same list as docs/progress/scope.yaml `excluded` (area api).
EXCLUDED = {("GET", "/")}

# Query shapes
NONE = "none"  # no query parameters (any is a 400)
FIELDS = "fields"  # TMF get-one: fields=
LIST = "list"  # TMF list: offset, limit, fields, equality filters (probed from the code)
PAGE = "page"  # offset, limit only

UUID_ARG = {"type": "string", "format": "uuid"}
DATE_ARG = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "YYYY-MM-DD"}

# path prefix -> what the API is, at the head of each description
FAMILIES = {
    "/tmf-api/productCatalogManagement/v5": "TMF620 Product Catalog",
    "/tmf-api/partyManagement/v5": "TMF632 Party",
    "/tmf-api/customerManagement/v5": "TMF629 Customer",
    "/tmf-api/accountManagement/v5": "TMF666 Account",
    "/tmf-api/paymentManagement/v4": "TMF676 Payment (v4)",
    "/tmf-api/productInventory/v5": "TMF637 Product Inventory (read-only)",
    "/tmf-api/customerBillManagement/v5": "TMF678 Customer Bill",
    "/tmf-api/productOrderingManagement/v5": "TMF622 Product Ordering",
    "/tmf-api/schema": "TMF666 extension schema",
    "/internal/v1": "bss internal API (dates are plain YYYY-MM-DD business dates)",
    "": "bss service",
}


@dataclass(frozen=True)
class Op:
    note: str
    body: type[BaseModel] | None = None
    query: str = NONE
    # more query parameters (name -> JSON schema), on top of the shape's
    extra: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    # a POST that takes no body (its handler parses none)
    no_body: bool = False


OPERATIONS: dict[str, Op] = {
    # --- TMF620 product catalog ---
    "list_specifications": Op("Product specifications.", query=LIST),
    "get_specification": Op("One product specification.", query=FIELDS),
    "create_specification": Op(
        "Create a product specification (lifecycleStatus In Design, Active or Retired).",
        body=SpecificationCreate,
    ),
    "patch_specification": Op(
        "Change a specification; lifecycleStatus only moves forward (In Design -> Active ->"
        " Retired).",
        body=SpecificationPatch,
    ),
    "list_prices": Op("Product offering prices.", query=LIST),
    "get_price": Op("One product offering price.", query=FIELDS),
    "create_price": Op(
        "Create a price on its own; attach it through an offering's productOfferingPrice."
        " Recurring prices are monthly; a price never starts in the past.",
        body=PriceCreate,
    ),
    "patch_price": Op(
        "Change a price: lifecycle, name, or end it (validFor.endDateTime, not in the past)."
        " The amount and type are read-only - to change a live price use price_change.",
        body=PricePatch,
    ),
    "list_offerings": Op("Product offerings (what can be ordered).", query=LIST),
    "get_offering": Op("One product offering.", query=FIELDS),
    "create_offering": Op(
        "Create an offering: productSpecification is mandatory; prices are attached by"
        " reference; an optional offering code in externalIdentifier.",
        body=OfferingCreate,
    ),
    "patch_offering": Op(
        "Change an offering: attach prices, move lifecycleStatus forward, set validFor (not"
        " in the past). Prices are never detached.",
        body=OfferingPatch,
    ),
    # --- TMF632 party ---
    "list_individuals": Op("Individuals (people).", query=LIST),
    "get_individual": Op("One individual.", query=FIELDS),
    "create_individual": Op(
        "Create an individual: givenName and familyName.", body=IndividualCreate
    ),
    "patch_individual": Op("Change an individual's name.", body=IndividualPatch),
    "list_organizations": Op("Organizations.", query=LIST),
    "get_organization": Op("One organization.", query=FIELDS),
    "create_organization": Op("Create an organization: name.", body=OrganizationCreate),
    "patch_organization": Op("Change an organization's name.", body=OrganizationPatch),
    # --- TMF629 customer ---
    "list_customers": Op("Customers (the role a party plays towards us).", query=LIST),
    "get_customer": Op("One customer.", query=FIELDS),
    "create_customer": Op(
        "Create a customer for an existing party: name, engagedParty (an Individual or"
        " Organization); status Created, Active or Inactive.",
        body=CustomerCreate,
    ),
    "patch_customer": Op(
        "Change a customer's name or status; engagedParty is read-only.", body=CustomerPatch
    ),
    # --- TMF666 account ---
    "list_accounts": Op("Billing accounts, with their balance.", query=LIST),
    "get_account": Op(
        "One billing account; accountBalance is payments minus charges (positive = credit).",
        query=FIELDS,
    ),
    "create_account": Op(
        "Create a billing account for a customer: exactly one relatedParty (role customer),"
        " billCycleDay 1..28 and currency are required; @type BssBillingAccount.",
        body=AccountCreate,
    ),
    "patch_account": Op(
        "Change an account's name, state (Active, Suspended, Closed) or creditLimit;"
        " currency and billCycleDay are fixed.",
        body=AccountPatch,
    ),
    # --- TMF676 payment ---
    "list_payments": Op("Payments received.", query=LIST),
    "get_payment": Op("One payment, with the bills it paid (paymentItem).", query=FIELDS),
    "create_payment": Op(
        "Record a payment that has been received (status done) on a billing account, in"
        " its currency; it settles the account's bills at once (oldest first, or the bills"
        " named in paymentItem). Taxes are out of scope. Payments are never changed.",
        body=PaymentCreate,
    ),
    # --- TMF637 product inventory ---
    "list_products": Op(
        "Products (subscriptions). Written only by orders; terminationDate is exclusive.",
        query=LIST,
    ),
    "get_product": Op("One product.", query=FIELDS),
    # --- TMF678 customer bill ---
    "list_bills": Op("Customer bills, written by the billing runs.", query=LIST),
    "get_bill": Op(
        "One customer bill: amounts, remainingAmount, paymentDueDate, state.", query=FIELDS
    ),
    "patch_bill": Op(
        "Hold or release a bill: state onHold (from sent or partiallyPaid), or back to the"
        " state its payments imply. Nothing else can be changed.",
        body=BillPatch,
    ),
    "list_rates": Op(
        "Applied customer billing rates (charges and refunds), written by the billing runs.",
        query=LIST,
    ),
    "get_rate": Op("One applied customer billing rate.", query=FIELDS),
    # --- TMF622 product ordering ---
    "list_orders": Op("Product orders.", query=LIST),
    "get_order": Op("One product order, with its items and any rejection reasons.", query=FIELDS),
    "create_order": Op(
        "Place an order - the only way to add (action add + productOffering) or end (action"
        " delete + product) a product. Processed at once: a business problem is stored as"
        " state rejected with productOrderErrorMessage (still 201); a malformed order is a"
        " 4xx and stores nothing.",
        body=OrderIn,
    ),
    "extension_schema": Op("The JSON Schema of BssBillingAccount (the TMF666 extension)."),
    # --- internal ---
    "get_context": Op(
        "Business date, time zone, currency, billing run time, payment term, and the"
        " operator (your principal)."
    ),
    "first_charge": Op(
        "Dry run of an add order: what the first charge of an offering would be for this"
        " account from startDate. Changes nothing.",
        extra={"productOffering.id": UUID_ARG, "startDate": DATE_ARG},
        required=("productOffering.id", "startDate"),
    ),
    "open_products": Op("The account's products that have not ended, with the price valid today."),
    "account_settlement": Op(
        "The account's settlement summary: outstanding, unallocated credit, bills on hold, overdue."
    ),
    "charges": Op("The account's charges (ABRs), newest run first.", query=PAGE),
    "transactions": Op(
        "The account's ledger: charges, refunds and payments with the balance after each.",
        query=PAGE,
    ),
    "price_change": Op(
        "Change the price of a live offering in one step: ends the current recurring price"
        " in that currency at validFrom and attaches the new amount from then on.",
        body=PriceChange,
    ),
    "list_runs": Op(
        "Billing runs, newest first, with status, account counts and charges.", query=PAGE
    ),
    "get_run": Op("One billing run."),
    "run_accounts": Op(
        "The accounts of a billing run with their outcome.",
        query=PAGE,
        extra={"status": {"type": "string", "enum": list(OUTCOMES)}},
    ),
    "catch_up": Op(
        "Queue billing runs up to a date (the worker bills them; the API never does)."
        " upTo may not be after the business date.",
        body=CatchUp,
    ),
    "retry": Op("Queue a retry of a failed billing run (409 if it is not failed).", no_body=True),
    "bill_detail": Op(
        "A bill with its charges, payments and state history (who held or released it)."
    ),
    # --- service ---
    "billing_status": Op(
        "Billing freshness: the latest run's date, status and age (503 when overdue)."
    ),
    "healthz": Op("Liveness: the app and its database answer."),
}
