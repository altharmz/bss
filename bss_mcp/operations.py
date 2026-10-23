"""What the generated tools need to know beyond app.openapi() (bss_mcp/tools.py).

The app parses query parameters and bodies itself (app/api/listing.py, `parse(Model,
body)`), so app.openapi() carries neither. This registry adds, per operation: the request
model of a write, the query parameters of a read, and a one-line note condensed from the
API notes (scripts/docs/api-notes/, docs/api/) - the handlers mostly have no docstring.

Kept honest by tests/test_mcp_tools.py (the drift check): the operations here are exactly
app.openapi()'s minus EXCLUDED and AUTH_OPERATIONS. EXCLUDED is exactly the progress
scope's excluded API entries; AUTH_OPERATIONS keeps interactive credentials and user
management outside MCP. Every exposed POST/PATCH names the model its handler parses
(read from the handler's source), and nothing else does.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.api.account import AccountCreate, AccountPatch, CycleCreate, CyclePatch
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
from app.api.inventory import ProductCreate, ProductPatch
from app.api.on_demand import DemandCreate
from app.api.ordering import CancelIn, OrderIn, OrderPatch
from app.api.party import IndividualCreate, IndividualPatch, OrganizationCreate, OrganizationPatch
from app.api.payment import PaymentCreate
from app.api.presentation import FormatCreate, FormatPatch, MediaCreate, MediaPatch
from app.api.refunds import RefundCreate
from app.api.resource_inventory import ResourceCreate, ResourcePatch
from app.api.topup import TopupCreate, TopupPatch
from app.internal.billing_runs import OUTCOMES, CatchUp
from app.internal.catalog import PriceChange
from app.internal.prepaid_commands import STATES

# Not tools: the same list as docs/progress/scope.yaml `excluded` (area api).
EXCLUDED = {("GET", "/")}
# Interactive account credentials are deliberately never exposed as model tools.
AUTH_OPERATIONS = {
    ("GET", "/internal/v1/session"),
    ("POST", "/internal/v1/session"),
    ("DELETE", "/internal/v1/session"),
    ("POST", "/internal/v1/session/password"),
    ("GET", "/internal/v1/users"),
    ("POST", "/internal/v1/users"),
    ("PATCH", "/internal/v1/users/{username}"),
}

# Query shapes
NONE = "none"  # no query parameters (any is a 400)
FIELDS = "fields"  # TMF get-one: fields=
LIST = "list"  # TMF list: offset, limit, fields, equality filters (probed from the code)
PAGE = "page"  # offset, limit only

UUID_ARG = {"type": "string", "format": "uuid"}
DATE_ARG = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "YYYY-MM-DD"}

# path prefix -> what the API is, at the head of each description
FAMILIES = {
    "/tmf-api/resourceInventory/v5": "TMF639 Subscriber Number Inventory",
    "/tmf-api/productCatalogManagement/v5": "TMF620 Product Catalog",
    "/tmf-api/partyManagement/v5": "TMF632 Party",
    "/tmf-api/customer/v5": "TMF629 Customer",
    "/tmf-api/accountManagement/v5": "TMF666 Account",
    "/tmf-api/payment/v4": "TMF676 Payment (v4)",
    "/tmf-api/prepayBalanceManagement/v4": "TMF654 Prepay Balance (v4)",
    "/tmf-api/productInventory/v5": "TMF637 Product Inventory",
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
    body_extra: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)


OPERATIONS: dict[str, Op] = {
    "list_resources": Op(
        "Subscriber numbers; filter by value, administrativeState, usageState or allocationStatus.",
        query=LIST,
    ),
    "get_resource": Op("One subscriber number with dated allocation state.", query=FIELDS),
    "create_resource": Op(
        "Create a canonical E.164 number (digits only); built-in msisdn specification.",
        body=ResourceCreate,
    ),
    "patch_resource": Op(
        "Edit descriptions or lock/unlock a number. Value and specification are immutable;"
        " locking preserves assignments.",
        body=ResourcePatch,
    ),
    "delete_resource": Op(
        "Delete a never-assigned number; any retained assignment history gives 409."
    ),
    # --- TMF654 prepaid balances ---
    "list_buckets": Op(
        "Prepaid monetary buckets, one per prepaid account and currency: remainingValue"
        " (spendable), reservedValue (held); filter by partyAccount.id, status or usageType.",
        query=LIST,
    ),
    "get_bucket": Op(
        "One prepaid bucket with spendable and held amounts, live products and numbers.",
        query=FIELDS,
    ),
    "list_accumulated_balances": Op(
        "Total prepaid balance per account and currency; filter by partyAccount.id.",
        query=LIST,
    ),
    "get_accumulated_balance": Op(
        "One account's total prepaid balance over its buckets.", query=FIELDS
    ),
    "list_topup_balances": Op(
        "Top-ups: money received on a prepaid bucket (payments to a prepaid account appear here"
        " too); filter by status, bucket.id or partyAccount.id.",
        query=LIST,
    ),
    "get_topup_balance": Op("One top-up.", query=FIELDS),
    "create_topup_balance": Op(
        "Record money received on a prepaid bucket: creates its done TMF676 payment in the same"
        " transaction. Monetary only, exact cents in the bucket currency; a refusal (closed"
        " account, number not the account's) is stored failed with 201.",
        body=TopupCreate,
    ),
    "patch_topup_balance": Op(
        "status cancelled reverses a completed top-up whose money is unspent and unheld (409"
        " otherwise); reason and description may change; the rest must repeat as stored.",
        body=TopupPatch,
    ),
    "delete_topup_balance": Op("Delete a failed top-up; a completed or cancelled one is history."),
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
        " Recurring prices use daily, weekly, monthly or yearly periods with length 1..1000;"
        " a price never starts in the past.",
        body=PriceCreate,
    ),
    "patch_price": Op(
        "Change a price: lifecycle, name, or end it (validFor.endDateTime, not in the past)."
        " The charge (price, priceType, period) changes only while its offering was never"
        " ordered - to change a live price use price_change.",
        body=PricePatch,
    ),
    "delete_price": Op(
        "Delete a price that is unattached or whose offering was never ordered; a price of"
        " an ordered offering is a 409 (end its validFor instead)."
    ),
    "list_offerings": Op("Product offerings (what can be ordered).", query=LIST),
    "get_offering": Op("One product offering.", query=FIELDS),
    "create_offering": Op(
        "Create an offering: prices attached by reference or sent inline (new prices);"
        " an optional offering code as the externalIdentifier of type offeringCode.",
        body=OfferingCreate,
    ),
    "patch_offering": Op(
        "Change an offering: attach prices, move lifecycleStatus forward, set validFor (not"
        " in the past), categories, its specification (while never ordered). Prices are"
        " never detached.",
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
        "Create an individual: givenName and familyName; optional status (initialized,"
        " validated, deceased) and every other TMF632 attribute (contactMedium, birthDate,"
        " ...), checked against the spec - nested objects need their @type.",
        body=IndividualCreate,
    ),
    "patch_individual": Op(
        "Change an individual's attributes; an attribute sent replaces it whole, null removes it.",
        body=IndividualPatch,
    ),
    "delete_individual": Op(
        "Delete an individual nothing refers to: 409 while a customer engages it or it owns"
        " an account."
    ),
    "list_organizations": Op("Organizations.", query=LIST),
    "get_organization": Op("One organization.", query=FIELDS),
    "create_organization": Op(
        "Create an organization: name; optional status (initialized, validated, closed) and"
        " every other TMF632 attribute (tradingName, contactMedium, ...), checked against"
        " the spec.",
        body=OrganizationCreate,
    ),
    "patch_organization": Op(
        "Change an organization's attributes; an attribute sent replaces it whole, null"
        " removes it.",
        body=OrganizationPatch,
    ),
    "delete_organization": Op(
        "Delete an organization nothing refers to: 409 while a customer engages it or it"
        " owns an account."
    ),
    # --- TMF629 customer ---
    "list_customers": Op("Customers (the role a party plays towards us).", query=LIST),
    "get_customer": Op("One customer.", query=FIELDS),
    "create_customer": Op(
        "Create a customer for an existing party: name, engagedParty (an Individual or"
        " Organization); status Created, Active or Inactive; optional TMF629 attributes"
        " (description, contactMedium, creditProfile, ...) checked against the spec.",
        body=CustomerCreate,
    ),
    "patch_customer": Op(
        "Change a customer's name, status or other attributes; engagedParty only while"
        " the customer has no billing account; account is the billing accounts' list.",
        body=CustomerPatch,
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
    "get_cycle_specification": Op(
        "One billing cycle specification: monthly (built in) or a stored one.", query=FIELDS
    ),
    "create_cycle_specification": Op(
        "Name a billing cycle specification: name, description; billingPeriod/frequency"
        " monthly and billingDateShift 0 only - billing has one monthly policy.",
        body=CycleCreate,
    ),
    "patch_cycle_specification": Op(
        "Rename or describe a stored cycle specification (the built-in one is fixed).",
        body=CyclePatch,
    ),
    "delete_cycle_specification": Op(
        "Delete a stored cycle specification no billing account names (409 otherwise)."
    ),
    "list_accounts": Op("Billing accounts, with their balance.", query=LIST),
    "get_account": Op(
        "One billing account; accountBalance is payments minus charges (positive = credit).",
        query=FIELDS,
    ),
    "create_account": Op(
        "Create a billing account for a customer: relatedParty needs exactly one party with"
        " role customer (others, e.g. bill receiver, are stored as sent); billCycleDay"
        " (1..28, default today's day) and currency (default creditLimit's unit or the"
        " server's) are optional; ratingType is always postpaid; @type BssBillingAccount.",
        body=AccountCreate,
    ),
    "patch_account": Op(
        "Change an account's name, state (Active, Suspended, Closed), creditLimit,"
        " financialAccount (null unlinks), documents (contact, paymentPlan, ...) or"
        " relatedParty (the customer only while the account has no history: 409);"
        " currency and billCycleDay are fixed.",
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
        "Change a financial account's name, description, accountType, state, creditLimit,"
        " contact, taxExemption or relatedParty (the owner only while no account links"
        " to it: 409).",
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
        "Change a party account; financialAccount null unlinks; a new owner needs the"
        " link unlinked or to one of its own financial accounts.",
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
        "Change a settlement account; financialAccount null unlinks; a new owner needs the"
        " link unlinked or to one of its own financial accounts.",
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
    "create_product": Op(
        "Add a product: places an add order (billingAccount, productOffering, optional"
        " startDate). A rejection is a 409 with the reasons; nothing is stored.",
        body=ProductCreate,
    ),
    "patch_product": Op(
        "Schedule a product's end: terminationDate only (exclusive), through a delete"
        " order. To take an end back, cancel that order (cancel_order).",
        body=ProductPatch,
    ),
    "delete_product": Op(
        "End a product today through a delete order (a product not started is"
        " cancelled); it stays readable as history."
    ),
    # --- TMF678 customer bill ---
    "list_bill_demands": Op("On-demand bill requests.", query=LIST),
    "get_bill_demand": Op("One on-demand bill request and its result.", query=FIELDS),
    "create_bill_demand": Op(
        "Queue an off-cycle bill; the worker waits for the next daily run then collects"
        " posted unbilled charges.",
        body=DemandCreate,
    ),
    "list_bills": Op("Customer bills, written by the billing runs.", query=LIST),
    "list_bill_cycles": Op(
        "Daily billing cycles, one per billing run.",
        query=LIST,
        filters={
            "billingDate": {
                "type": "string",
                "format": "date-time",
                "examples": ["2026-10-12T21:00:00Z"],
                "description": "Local midnight of the billing day (BILLING_TZ), with offset.",
            }
        },
    ),
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
    "patch_order": Op(
        "Change what describes an order: description, category, priority (0 highest ..4),"
        " externalId; null removes. A draft also accepts items, account and start date;"
        " state acknowledged submits it. After processing, placed-with attributes cannot change.",
        body=OrderPatch,
        body_extra={
            "state": {
                "anyOf": [{"type": "string", "enum": ["acknowledged"]}, {"type": "null"}],
                "description": "Submit a draft for processing with acknowledged.",
            }
        },
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
    "get_hub_resource": Op("One TMF639 event subscription.", query=FIELDS),
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
        " in that currency at validFrom and attaches the new amount from then on,"
        " preserving its charge interval.",
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
    "list_prepaid_commands": Op(
        "Prepaid order, renewal and transfer commands, newest first: internal state"
        " (pending, completed, failedTechnical), outcome, technical error, needs-top-up"
        " amounts and the linked order, products, run and bills.",
        query=PAGE,
        extra={"billingAccount.id": UUID_ARG, "state": {"type": "string", "enum": list(STATES)}},
    ),
    "get_prepaid_command": Op("One prepaid command with its state, outcome and links."),
    "retry_prepaid_command": Op(
        "Return a failedTechnical prepaid command to pending with the same identity, request"
        " and hold; the worker attempts it within a minute (409 unless failedTechnical).",
        no_body=True,
    ),
    "bill_detail": Op(
        "A bill with its charges, payments and state history (who held or released it)."
    ),
    # --- service ---
    "billing_status": Op(
        "Billing freshness: the latest run's date, status and age (503 when overdue)."
    ),
    "healthz": Op("Liveness: the app and its database answer."),
}
