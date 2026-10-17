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
from app.api.accounts import (
    FinancialCreate,
    FinancialPatch,
    PartyAccountCreate,
    PartyAccountPatch,
)
from app.api.bill import BillPatch
from app.api.catalog import (
    OfferingCreate,
    OfferingPatch,
    PriceCreate,
    PricePatch,
    SpecificationCreate,
    SpecificationPatch,
)
from app.api.catalog_jobs import ExportJobCreate, ImportJobCreate
from app.api.classification import CatalogCreate, CatalogPatch, CategoryCreate, CategoryPatch
from app.api.customer import CustomerCreate, CustomerPatch
from app.api.hub import HUB_APIS, HubIn
from app.api.on_demand import DemandCreate
from app.api.ordering import CancelIn, OrderIn
from app.api.party import IndividualCreate, IndividualPatch, OrganizationCreate, OrganizationPatch
from app.api.payment import PaymentCreate
from app.api.presentation import FormatCreate, FormatPatch, MediaCreate, MediaPatch
from app.api.refunds import RefundCreate
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
    "list_categories": Op("Categories and their offerings and child categories.", query=LIST),
    "get_category": Op("One category.", query=FIELDS),
    "create_category": Op(
        "Create a category; parent is optional; productOffering replaces membership.",
        body=CategoryCreate,
    ),
    "patch_category": Op(
        "Change a category; parent controls hierarchy, subCategory is derived.", body=CategoryPatch
    ),
    "delete_category": Op(
        "Delete an empty category: 409 while subcategories, offerings or catalogs refer to it."
    ),
    "list_catalogs": Op("Product catalogs and their root categories.", query=LIST),
    "get_catalog": Op("One product catalog.", query=FIELDS),
    "create_catalog": Op(
        "Create a product catalog referencing root categories.", body=CatalogCreate
    ),
    "patch_catalog": Op(
        "Change a product catalog; category replaces the root category list.", body=CatalogPatch
    ),
    "delete_catalog": Op("Delete a product catalog; its root categories stay."),
    "list_export_jobs": Op("Catalog export jobs (finished: Succeeded or Failed).", query=LIST),
    "get_export_job": Op(
        "One export job; url is the exported document as a data: URL.", query=FIELDS
    ),
    "create_export_job": Op(
        "Export the whole catalog, or one product catalog with query productCatalog.id=<id>:"
        " the job's url is the document as a data: URL that create_import_job accepts.",
        body=ExportJobCreate,
    ),
    "delete_export_job": Op("Delete an export job record."),
    "list_import_jobs": Op("Catalog import jobs (finished: Succeeded or Failed).", query=LIST),
    "get_import_job": Op("One import job; errorLog says why a failed one failed.", query=FIELDS),
    "create_import_job": Op(
        "Import a catalog document: url is an export's data: URL. All or nothing under the"
        " POST rules, keeping the source ids; identical entries are skipped, a failure is"
        " stored as status Failed with errorLog.",
        body=ImportJobCreate,
    ),
    "delete_import_job": Op("Delete an import job record; what it imported stays."),
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
    "delete_specification": Op("Delete a specification no offering uses (409 otherwise)."),
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
    "delete_price": Op(
        "Delete a price that is unattached or whose offering was never ordered; a price of"
        " an ordered offering is a 409 (end its validFor instead)."
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
    "delete_offering": Op(
        "Delete an offering that was never ordered and has no prices left (delete them"
        " first); an ordered one is a 409 - retire it instead."
    ),
    # --- TMF632 party ---
    "list_individuals": Op("Individuals (people).", query=LIST),
    "get_individual": Op("One individual.", query=FIELDS),
    "create_individual": Op(
        "Create an individual: givenName and familyName.", body=IndividualCreate
    ),
    "patch_individual": Op("Change an individual's name.", body=IndividualPatch),
    "delete_individual": Op(
        "Delete an individual nothing refers to: 409 while a customer engages it or it owns"
        " an account."
    ),
    "list_organizations": Op("Organizations.", query=LIST),
    "get_organization": Op("One organization.", query=FIELDS),
    "create_organization": Op("Create an organization: name.", body=OrganizationCreate),
    "patch_organization": Op("Change an organization's name.", body=OrganizationPatch),
    "delete_organization": Op(
        "Delete an organization nothing refers to: 409 while a customer engages it or it"
        " owns an account."
    ),
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
    "delete_customer": Op("Delete a customer without billing accounts (409 otherwise)."),
    # --- TMF666 account ---
    "list_bill_formats": Op("Bill formats (JSON detailed or JSON summary).", query=LIST),
    "get_bill_format": Op("One bill format.", query=FIELDS),
    "create_bill_format": Op(
        "Create a supported JSON bill renderer configuration.", body=FormatCreate
    ),
    "patch_bill_format": Op("Change a bill format for future documents.", body=FormatPatch),
    "delete_bill_format": Op(
        "Delete a bill format no billing account uses (409 otherwise); issued bills keep"
        " their snapshot."
    ),
    "list_bill_media": Op("Bill presentation media preferences.", query=LIST),
    "get_bill_media": Op("One presentation medium.", query=FIELDS),
    "create_bill_media": Op(
        "Create a presentation medium descriptor; no external delivery.", body=MediaCreate
    ),
    "patch_bill_media": Op("Change a presentation medium descriptor.", body=MediaPatch),
    "delete_bill_media": Op(
        "Delete a presentation medium no billing account prefers (409 otherwise)."
    ),
    "list_cycle_specifications": Op("Supported billing cycle specifications.", query=LIST),
    "get_cycle_specification": Op("The monthly billing policy.", query=FIELDS),
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
        "Change an account's name, state (Active, Suspended, Closed), creditLimit or"
        " financialAccount (null unlinks); currency and billCycleDay are fixed.",
        body=AccountPatch,
    ),
    "delete_account": Op(
        "Delete a billing account without history: 409 once anything was ordered, charged,"
        " billed, paid or refunded on it or a billing run processed it - close it instead."
    ),
    "list_financial_accounts": Op(
        "Financial accounts: a party's receivables across its linked billing accounts.",
        query=LIST,
    ),
    "get_financial_account": Op(
        "One financial account; accountBalance is one entry per currency of the linked"
        " billing accounts, accountRelationship lists them.",
        query=FIELDS,
    ),
    "create_financial_account": Op(
        "Create a financial account owned by one party (relatedParty role owner, an"
        " Individual or Organization). Link billing accounts via their financialAccount.",
        body=FinancialCreate,
    ),
    "patch_financial_account": Op(
        "Change a financial account's name, description, accountType, state or"
        " creditLimit; the owner is fixed.",
        body=FinancialPatch,
    ),
    "delete_financial_account": Op(
        "Delete a financial account no account links to (409 otherwise: unlink them first)."
    ),
    "list_party_accounts": Op(
        "Party accounts: a party's accounts that are not billed (no balance).", query=LIST
    ),
    "get_party_account": Op("One party account.", query=FIELDS),
    "create_party_account": Op(
        "Create a party account owned by one party (relatedParty role owner); optional"
        " financialAccount of the same owner.",
        body=PartyAccountCreate,
    ),
    "patch_party_account": Op(
        "Change a party account; financialAccount null unlinks; the owner is fixed.",
        body=PartyAccountPatch,
    ),
    "delete_party_account": Op("Delete a party account (nothing refers to one)."),
    "list_settlement_accounts": Op(
        "Settlement accounts: records for settling with a party; never billed.", query=LIST
    ),
    "get_settlement_account": Op("One settlement account.", query=FIELDS),
    "create_settlement_account": Op(
        "Create a settlement account owned by one party (relatedParty role owner);"
        " optional financialAccount of the same owner.",
        body=PartyAccountCreate,
    ),
    "patch_settlement_account": Op(
        "Change a settlement account; financialAccount null unlinks; the owner is fixed.",
        body=PartyAccountPatch,
    ),
    "delete_settlement_account": Op("Delete a settlement account (nothing refers to one)."),
    # --- TMF676 payment ---
    "list_refunds": Op("Completed refunds paid out of unused credit.", query=LIST),
    "get_refund": Op("One completed refund.", query=FIELDS),
    "create_refund": Op(
        "Record a completed payout, bounded by unused credit and net account balance.",
        body=RefundCreate,
    ),
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
    "list_bill_demands": Op("On-demand bill requests.", query=LIST),
    "get_bill_demand": Op("One on-demand bill request and its result.", query=FIELDS),
    "create_bill_demand": Op(
        "Queue an off-cycle bill; the worker waits for the next daily run then collects"
        " posted unbilled charges.",
        body=DemandCreate,
    ),
    "list_bills": Op("Customer bills, written by the billing runs.", query=LIST),
    "list_bill_cycles": Op("Daily billing cycles, one per billing run.", query=LIST),
    "get_bill_cycle": Op(
        "One daily billing cycle (not an account's monthly period).", query=FIELDS
    ),
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
    "delete_order": Op(
        "Delete a rejected order (it never executed); any other order is a 409 - cancel a"
        " completed one instead."
    ),
    "list_cancellations": Op("Order cancellations (cancelProductOrder), all done.", query=LIST),
    "get_cancellation": Op("One order cancellation.", query=FIELDS),
    "cancel_order": Op(
        "Cancel a completed order before it takes effect: its new products have not started,"
        " its terminations have not happened and billing has not charged those periods."
        " New products are cancelled, ended ones open-ended again; 409 (nothing stored) if"
        " any of it already took effect. requestedCancellationDate: omit or today.",
        body=CancelIn,
    ),
    "extension_schema": Op("The JSON Schema of BssBillingAccount (the TMF666 extension)."),
    # --- TMF hubs: event subscriptions, one pair per API ---
    **{
        f"create_hub_{short}": Op(
            f"Subscribe a callback to {api} events: callback (https, public host outside"
            " dev), optional query eventType=<type>[,<type>]; events are POSTed to it.",
            body=HubIn,
        )
        for api, short in HUB_APIS
    },
    **{
        f"delete_hub_{short}": Op(f"Remove a {api} event subscription; nothing more is sent.")
        for api, short in HUB_APIS
    },
    "get_hub_customer": Op("One TMF629 event subscription.", query=FIELDS),
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
