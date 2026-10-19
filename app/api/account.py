"""TMF666 Account Management v5: billingAccount.

Two things TMF666 has no attribute for are carried by a documented extension, following
the TMF extension pattern (`@type` names the subclass, `@baseType` the TMF class,
`@schemaLocation` the JSON Schema of the additions, served by this app):

- `billCycleDay` (1..28): the day of month on which the account's billing period starts.
  The closest spec attribute, billStructure.cycleSpecification.billingDateShift, is an
  offset of the bill date *from* the period start - a different thing. Optional on POST
  (the spec requires only name and relatedParty): the creation day, 29-31 as 28.
- `currency`: the account's single currency. Immutable. Optional on POST: the
  creditLimit's unit, else UI_CURRENCY.

`relatedParty` holds parties in any role; exactly one has role `customer` - the owning
customer, which changes only while the account has no history (app/api/accounts.py).
`ratingType` is always `postpaid`: billing has no prepaid mode, so another value is a
422. contact, taxExemption, paymentPlan, paymentStatus, ... are documents: stored as
sent, checked against the spec.

`billCycleDay` is fixed after creation for now (PATCH is a 409): how a change treats the
current billing period is an open question for stage 3.

The balance is the spec's own `accountBalance[]`: one read-only `currentBalance` entry,
computed on every read. Positive = the customer is in credit (payments exceed charges).

`creditLimit` is the spec's own Money attribute, always in the account's currency.
Informational only: nothing (ordering included) is ever refused because of it.

`financialAccount` (optional, POST and PATCH, null unlinks) links the account to a
financial account owned by its customer's engagedParty (app/api/accounts.py).
"""

from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import Field

from app.api import listing
from app.api.accounts import (
    BALANCE_TYPE,
    FinancialAccountRef,
    balance_id,
    details_body,
    document_columns,
    financial_ref,
    keep_link,
    link_financial,
    related_body,
    related_parties,
    related_party_field,
)
from app.api.common import (
    CENT,
    CustomerRef,
    Money,
    TmfModel,
    check_server_set,
    check_type,
    claim_id,
    columns,
    created,
    documents_model,
    merge_documents,
    meta_body,
    money,
    not_null,
    parse_documented,
    parse_id,
    ref,
    take_id,
    take_server_set,
    utc,
    with_meta,
    without_nulls,
)
from app.api.customer import customer_href
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.presentation import BillStructure, structure_body
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.events import emit
from app.repository import accounts, billing, cycles, parties, presentation
from app.repository._sql import Row, count_references

API = "TMF666"
BASE = "/tmf-api/accountManagement/v5"
router = APIRouter(prefix=BASE)
# Outside the TMF666 resource paths: it is ours, not part of that API.
schema_router = APIRouter(prefix="/tmf-api/schema")

EXTENSION_TYPE = "BssBillingAccount"
SCHEMA_LOCATION = f"/tmf-api/schema/{EXTENSION_TYPE}.schema.json"
EXTENSION_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": SCHEMA_LOCATION,
    "title": EXTENSION_TYPE,
    "description": (
        "Extension of the TMF666 BillingAccount: the attributes this service adds."
        " An instance must also satisfy the TMF666 v5 BillingAccount schema."
    ),
    "type": "object",
    "properties": {
        "@type": {"const": EXTENSION_TYPE},
        "@baseType": {"const": "BillingAccount"},
        "billCycleDay": {
            "type": "integer",
            "minimum": 1,
            "maximum": 28,
            "description": "Day of month on which the account's billing period starts.",
        },
        "currency": {
            "type": "string",
            "pattern": "^[A-Z]{3}$",
            "description": "ISO 4217 code of the account's single currency. Immutable.",
        },
    },
    "required": ["@type", "@baseType", "billCycleDay", "currency"],
}

AccountState = Literal["Active", "Suspended", "Closed"]
# Billing has no prepaid mode (docs/decisions/accounts.md, "Account attributes")
RatingType = Literal["postpaid"]
RATING_TYPE = "postpaid"
# Documents to us: stored as sent (checked against the spec), no behaviour.
DOCUMENTS = (
    "accountRelationship",
    "contact",
    "defaultPaymentMethod",
    "paymentPlan",
    "paymentStatus",
    "taxExemption",
)
ATTRIBUTES = (
    "name",
    "description",
    "accountType",
    "ratingType",
    *DOCUMENTS,
    "billStructure",
    "state",
    "relatedParty",
    "accountBalance",
    "creditLimit",
    "billCycleDay",
    "currency",
    "financialAccount",
    "lastUpdate",
    "@baseType",
    "@schemaLocation",
)
# The one related party of an account is its owning customer (`customer_id`). Its role is
# required and must be exactly this, so the role the response states is the one stored -
# never a rewrite of whatever the client sent.
CUSTOMER_ROLE = "customer"

# The implemented policy is shared by all accounts; the calendar anchor remains the
# account's billCycleDay extension, not TMF's billingDateShift (an offset).
CYCLE_ID = "monthly"
CYCLE_BASE = f"{BASE}/billingCycleSpecification"
CYCLE_DOCUMENTS = ("validFor",)
CYCLE_ATTRIBUTES = (
    "name",
    "description",
    "billingPeriod",
    "frequency",
    "billingDateShift",
    "chargeDateOffset",
    "creditDateOffset",
    "mailingDateOffset",
    "paymentDueDateOffset",
    *CYCLE_DOCUMENTS,
)


def policy_offsets() -> dict[str, int]:
    """The policy's offsets from the period start (= the bill date): a bill of D collects
    the charges and credits of runs up to D (billing-core.md, "Bills"), is issued on D,
    and is due BILL_PAYMENT_TERM_DAYS later."""
    return {
        "chargeDateOffset": 0,
        "creditDateOffset": 0,
        "mailingDateOffset": 0,
        "paymentDueDateOffset": settings.bill_payment_term_days,
    }


def cycle_specification_body() -> dict[str, Any]:
    return {
        "id": CYCLE_ID,
        "href": f"{CYCLE_BASE}/{CYCLE_ID}",
        "@type": "BillingCycleSpecification",
        "name": "Monthly billing",
        "description": (
            "Calendar monthly periods anchored by the billing account's billCycleDay."
            " Bills collect unbilled charges on the period start date; daily runs"
            " catch up missed dates."
        ),
        "billingPeriod": "monthly",
        "frequency": "monthly",
        "billingDateShift": 0,
        **policy_offsets(),
    }


# Stored specifications (migration 0019) are named instances of the same policy: the
# values billing can honour only, so billing never reads them. The built-in one above
# is not stored and cannot be changed or deleted. validFor is a record only.

CycleDocuments = documents_model("CycleDocuments", API, CYCLE_DOCUMENTS)


class CyclePolicy(CycleDocuments):
    """The policy's values: accepted when sent equal to them, a 422 otherwise."""

    billing_period: Literal["monthly"] | None = None
    frequency: Literal["monthly"] | None = None
    billing_date_shift: Literal[0] | None = None
    charge_date_offset: Literal[0] | None = None
    credit_date_offset: Literal[0] | None = None
    mailing_date_offset: Literal[0] | None = None
    payment_due_date_offset: int | None = Field(
        default=None, description="BILL_PAYMENT_TERM_DAYS of this server (GET monthly)"
    )


class CycleCreate(CyclePolicy):
    name: str = Field(min_length=1)
    description: str | None = None


class CyclePatch(CyclePolicy):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


def check_payment_term(data: CyclePolicy) -> None:
    term = settings.bill_payment_term_days
    if data.payment_due_date_offset not in (None, term):
        raise unprocessable(
            f"paymentDueDateOffset must be {term}: bills are due BILL_PAYMENT_TERM_DAYS"
            " after their date"
        )


def stored_cycle_body(row: Row) -> dict[str, Any]:
    return without_nulls(
        {
            "id": str(row["id"]),
            "href": f"{CYCLE_BASE}/{row['id']}",
            "@type": "BillingCycleSpecification",
            "name": row["name"],
            "description": row["description"],
            "billingPeriod": row["billing_period"],
            "frequency": row["frequency"],
            "billingDateShift": row["billing_date_shift"],
            **policy_offsets(),
            **row["details"],
        }
    ) | meta_body(row)


def _stored_cycle(conn: psycopg.Connection, raw_id: str, lock=None) -> Row | None:
    try:
        uid = UUID(raw_id)
    except ValueError:
        return None
    return cycles.get(conn, uid, lock)


def cycle_ref(conn: psycopg.Connection, spec_id: Any) -> dict[str, Any]:
    if spec_id is None:
        return ref(
            "BillingCycleSpecification", f"{CYCLE_BASE}/{CYCLE_ID}", CYCLE_ID, "Monthly billing"
        )
    row = cycles.get(conn, spec_id)
    assert row is not None  # foreign key
    return ref("BillingCycleSpecification", f"{CYCLE_BASE}/{row['id']}", row["id"], row["name"])


@router.get("/billingCycleSpecification")
def list_cycle_specifications(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "billingPeriod": ("billingPeriod", listing.as_str),
            "frequency": ("frequency", listing.as_str),
        },
        attributes=CYCLE_ATTRIBUTES,
    )
    with tx() as conn:
        bodies = [cycle_specification_body()] + [
            stored_cycle_body(r) for r in cycles.all_rows(conn)
        ]
    rows = [b for b in bodies if all(b.get(key) == value for key, value in query.where.items())]
    return listing.list_response(rows[query.offset : query.offset + query.limit], len(rows), query)


@router.get("/billingCycleSpecification/{specification_id}")
def get_cycle_specification(specification_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    if specification_id == CYCLE_ID:
        return TmfJSONResponse(listing.select_fields(cycle_specification_body(), fields))
    with tx() as conn:
        row = _stored_cycle(conn, specification_id)
        if row is None:
            raise not_found("billingCycleSpecification", specification_id)
    return TmfJSONResponse(listing.select_fields(stored_cycle_body(row), fields))


@router.post("/billingCycleSpecification")
def create_cycle_specification(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Values billing cannot honour (another period, a date shift) are a 422."""
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    check_type(body, "BillingCycleSpecification")
    uid = take_id(body)
    data, documents = parse_documented(
        CycleCreate, body, CYCLE_DOCUMENTS, API, "BillingCycleSpecification_FVO"
    )
    check_payment_term(data)
    with tx() as conn:
        claim_id(conn, cycles.TABLE, uid)
        values = {"name": data.name, "description": data.description}
        row = cycles.create(conn, values if uid is None else {"id": uid, **values})
        if documents:
            row = cycles.update(conn, row["id"], {"details": merge_documents({}, documents)})
        new = stored_cycle_body(with_meta(conn, cycles.TABLE, row, data))
        emit.created(conn, API, "billingCycleSpecification", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/billingCycleSpecification/{specification_id}")
def patch_cycle_specification(
    specification_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    check_type(body, "BillingCycleSpecification")
    data, documents = parse_documented(
        CyclePatch, body, CYCLE_DOCUMENTS, API, "BillingCycleSpecification_MVO"
    )
    not_null(data, "name")
    check_payment_term(data)
    if specification_id == CYCLE_ID:
        raise conflict("the built-in monthly specification cannot be changed")
    with tx() as conn:
        current = _stored_cycle(conn, specification_id, "update")
        if current is None:
            raise not_found("billingCycleSpecification", specification_id)
        old = emit.before(
            conn, API, "billingCycleSpecification", lambda: stored_cycle_body(current)
        )
        changes = {k: v for k, v in columns(data).items() if k in ("name", "description")}
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        row = cycles.update(conn, current["id"], changes)
        new = stored_cycle_body(with_meta(conn, cycles.TABLE, row, data))
        emit.changed(conn, API, "billingCycleSpecification", old, new, at=clock.now())
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(
    "/billingCycleSpecification/{specification_id}", status_code=204, response_class=Response
)
def delete_cycle_specification(
    specification_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    """Only a stored one no billing account names (accounts take it FOR KEY SHARE)."""
    listing.no_query(request)
    if specification_id == CYCLE_ID:
        raise conflict("the built-in monthly specification cannot be deleted")
    with tx() as conn:
        row = _stored_cycle(conn, specification_id, "update")
        if row is None:
            raise not_found("billingCycleSpecification", specification_id)
        counts = count_references(
            conn, row["id"], {"billingAccount": ("billing_account", "cycle_specification_id")}
        )
        refuse_if_referenced(
            "billingCycleSpecification", row["id"], counts, "change their billStructure first"
        )
        emit.deleted(conn, API, "billingCycleSpecification", stored_cycle_body(row), at=clock.now())
        delete_row(conn, cycles.TABLE, row["id"])
    return Response(status_code=204)


class CustomerParty(TmfModel):
    """The relatedParty entry with role `customer`."""

    role: Literal["customer"]
    party_or_party_role: CustomerRef


AccountDocuments = documents_model("AccountDocuments", API, DOCUMENTS)


class AccountCreate(AccountDocuments):
    name: str = Field(min_length=1)
    related_party: list[Any] = related_party_field(CUSTOMER_ROLE, required=True)
    bill_cycle_day: int | None = Field(default=None, ge=1, le=28, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    rating_type: RatingType | None = None
    state: AccountState = "Active"
    credit_limit: Money | None = None
    last_update: Any = None
    bill_structure: BillStructure | None = None
    financial_account: FinancialAccountRef | None = None


class AccountPatch(AccountDocuments):
    name: str | None = Field(default=None, min_length=1)
    related_party: list[Any] | None = related_party_field(CUSTOMER_ROLE, required=False)
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    rating_type: RatingType | None = None
    state: AccountState | None = None
    credit_limit: Money | None = None
    bill_structure: BillStructure | None = None
    financial_account: FinancialAccountRef | None = None


def _credit_limit(limit: Money | None, currency: str) -> Any:
    """The column value for a creditLimit: in the account's currency, never negative."""
    if limit is None:
        return None
    if limit.unit != currency:
        raise unprocessable(
            f"creditLimit.unit must be the account's currency {currency}, not {limit.unit}"
        )
    if limit.value < 0:
        raise unprocessable("creditLimit.value must not be negative")
    return limit.value


def _owner_of(conn: psycopg.Connection, row: Row) -> Any:
    """The party a billing account belongs to: its customer's engagedParty."""
    customer = parties.get_customer(conn, row["customer_id"])
    assert customer is not None  # foreign key
    return customer["party_id"]


def _change_customer(conn: psycopg.Connection, current: Row, party: CustomerParty) -> Row:
    """The customer a PATCH's relatedParty names: the current one, or - while the account
    has no history (what DELETE asks) - another existing customer. Writers that add
    history lock the account first, as this PATCH does."""
    customer_id = party.party_or_party_role.id
    if customer_id == current["customer_id"]:
        customer = parties.get_customer(conn, customer_id)
        assert customer is not None  # foreign key
        return customer
    counts = count_references(conn, current["id"], parties.ACCOUNT_HISTORY)
    if any(counts.values()):
        raise conflict(
            "relatedParty: the customer cannot change once the account has history"
            " (orders, charges, bills, payments or billing runs)"
        )
    # FOR KEY SHARE until commit, as on POST
    customer = parties.get_customer(conn, customer_id, "key share")
    if customer is None:
        raise unprocessable(f"relatedParty: customer {customer_id} does not exist")
    return customer


def account_href(account_id: Any) -> str:
    return f"{BASE}/billingAccount/{account_id}"


def account_ref(row: Row) -> dict[str, Any]:
    """The v5 BillingAccountRef (TMF622/637/678) also carries the account's ratingType."""
    body = ref("BillingAccount", account_href(row["id"]), row["id"], row["name"])
    return body | {"ratingType": RATING_TYPE}


def customer_party(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    """The account's customer as a relatedParty entry (role `customer`)."""
    customer = parties.get_customer(conn, row["customer_id"])
    assert customer is not None  # foreign key
    return {
        "role": CUSTOMER_ROLE,
        "partyOrPartyRole": ref(
            "Customer",
            customer_href(customer["id"]),
            customer["id"],
            customer["name"],
            ref_type="PartyRoleRef",
        ),
        "@type": "RelatedPartyRefOrPartyRoleRef",
    }


def linked_financial(conn: psycopg.Connection, row: Row) -> Row | None:
    if row["financial_account_id"] is None:
        return None
    return accounts.get_financial(conn, row["financial_account_id"])


def account_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    financial = linked_financial(conn, row)
    anchor = customer_party(conn, row)
    return {
        "id": str(row["id"]),
        "href": account_href(row["id"]),
        "name": row["name"],
        **without_nulls({"description": row["description"], "accountType": row["account_type"]}),
        "ratingType": RATING_TYPE,
        "state": row["state"],
        "billStructure": {
            **structure_body(conn, row["id"]),
            "@type": "BillStructure",
            "cycleSpecification": cycle_ref(conn, row["cycle_specification_id"]),
        },
        "relatedParty": related_body(row, CUSTOMER_ROLE, anchor),
        **details_body(row),
        "accountBalance": [
            {
                "id": balance_id(row["currency"]),
                "balanceType": BALANCE_TYPE,
                "amount": money(
                    billing.account_balance(conn, row["id"]).quantize(CENT), row["currency"]
                ),
                "@type": "AccountBalance",
            }
        ],
        **(
            {}
            if row["credit_limit"] is None
            else {"creditLimit": money(row["credit_limit"], row["currency"])}
        ),
        **({} if financial is None else {"financialAccount": financial_ref(financial)}),
        "billCycleDay": row["bill_cycle_day"],
        "currency": row["currency"],
        "lastUpdate": utc(row["updated_at"]),
        "@type": EXTENSION_TYPE,
        "@baseType": "BillingAccount",
        "@schemaLocation": SCHEMA_LOCATION,
    }


@schema_router.get(f"/{EXTENSION_TYPE}.schema.json")
def extension_schema() -> TmfJSONResponse:
    return TmfJSONResponse(EXTENSION_SCHEMA)


@router.get("/billingAccount")
def list_accounts(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "state": ("state", listing.as_str),
            "accountType": ("account_type", listing.as_str),
            "relatedParty.id": ("customer_id", listing.as_uuid),
            "financialAccount.id": ("financial_account_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = parties.list_billing_accounts(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [account_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/billingAccount/{account_id}")
def get_account(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = parties.get_billing_account(conn, parse_id("billingAccount", account_id))
        if row is None:
            raise not_found("billingAccount", account_id)
        body = account_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post("/billingAccount")
def create_account(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, EXTENSION_TYPE, "BillingAccount")
    uid = take_id(body)
    server_set = take_server_set(body, ("accountBalance",))
    party, related, found = related_parties(
        body, CUSTOMER_ROLE, CustomerParty, "BillingAccount_FVO"
    )
    data, documents = parse_documented(
        AccountCreate, body, DOCUMENTS, API, "BillingAccount_FVO", found
    )
    assert party is not None and related is not None  # else found a problem
    customer_id = party.party_or_party_role.id
    currency = data.currency or (
        data.credit_limit.unit if data.credit_limit is not None else settings.ui_currency
    )
    # the creation day: an account opened on the 31st starts its periods on the 28th
    bill_cycle_day = data.bill_cycle_day or min(clock.today().day, 28)
    with tx() as conn:
        # FOR KEY SHARE until commit: a concurrent delete of the customer waits for this
        customer = parties.get_customer(conn, customer_id, "key share")
        if customer is None:
            raise unprocessable(f"relatedParty: customer {customer_id} does not exist")
        financial_id = (
            None
            if data.financial_account is None
            else link_financial(conn, data.financial_account, customer["party_id"], None)
        )
        claim_id(conn, "billing_account", uid)
        row = parties.create_billing_account(
            conn,
            customer_id=customer_id,
            name=data.name,
            currency=currency,
            bill_cycle_day=bill_cycle_day,
            state=data.state,
            credit_limit=_credit_limit(data.credit_limit, currency),
            opened_at=clock.now(),
            id=uid,
        )
        row = parties.update_billing_account(
            conn,
            row["id"],
            {
                "financial_account_id": financial_id,
                "description": data.description,
                "account_type": data.account_type,
                "details": merge_documents({"relatedParty": related}, documents),
            },
        )
        if data.bill_structure is not None:
            presentation.set_preferences(conn, row["id"], data.bill_structure)
            row = parties.get_billing_account(conn, row["id"])
        new = account_body(conn, row)
        check_server_set(server_set, new)
        emit.created(conn, API, "billingAccount", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/billingAccount/{account_id}")
def patch_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, EXTENSION_TYPE, "BillingAccount")
    if "billCycleDay" in body:
        # Open question for stage 3: how a cycle change treats the current period.
        raise conflict(
            "billCycleDay cannot be changed yet: how a change treats the current billing"
            " period is not decided"
        )
    server_set = take_server_set(body, ("accountBalance", "lastUpdate"))
    if "currency" in body:
        raise bad_request("currency is read-only")
    party, related, found = related_parties(
        body, CUSTOMER_ROLE, CustomerParty, "BillingAccount_MVO"
    )
    data, documents = parse_documented(
        AccountPatch, body, DOCUMENTS, API, "BillingAccount_MVO", found
    )
    not_null(data, "name", "state", "bill_structure", "related_party", "rating_type")
    with tx() as conn:
        uid = parse_id("billingAccount", account_id)
        # FOR UPDATE, as billing locks it: a concurrent delete either went first (404)
        # or waits for this change; so do the writers that add history
        current = parties.get_billing_account(conn, uid, "update")
        if current is None:
            raise not_found("billingAccount", account_id)
        before = account_body(conn, current)
        check_server_set(server_set, before)
        old = emit.before(conn, API, "billingAccount", lambda: before)
        changes = columns(
            data,
            exclude=document_columns(DOCUMENTS)
            | {"credit_limit", "bill_structure", "financial_account", "rating_type"},
        )
        if "credit_limit" in data.model_fields_set:  # null removes it
            changes["credit_limit"] = _credit_limit(data.credit_limit, current["currency"])
        owner = _owner_of(conn, current)
        if party is not None and related is not None:
            customer = _change_customer(conn, current, party)
            changes["customer_id"] = customer["id"]
            owner = customer["party_id"]
            documents["relatedParty"] = related
        if "financial_account" in data.model_fields_set:  # null unlinks
            changes["financial_account_id"] = (
                None
                if data.financial_account is None
                else link_financial(
                    conn, data.financial_account, owner, current["financial_account_id"]
                )
            )
        elif owner != _owner_of(conn, current):
            keep_link(conn, current["financial_account_id"], owner)
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        row = parties.update_billing_account(conn, uid, changes)
        if data.bill_structure is not None:
            presentation.set_preferences(conn, uid, data.bill_structure)
            row = parties.get_billing_account(conn, uid)
        patched = account_body(conn, row)
        emit.changed(conn, API, "billingAccount", old, patched, at=clock.now(), state="state")
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/billingAccount/{account_id}", status_code=204, response_class=Response)
def delete_account(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Only an account without history: nothing ordered, charged, billed, paid, refunded
    or processed by a billing run (`parties.ACCOUNT_HISTORY`) - close it otherwise. Its
    presentation preferences go with it. Every writer that adds history takes the
    account FOR KEY SHARE or FOR UPDATE first, and the worker skips an account that is
    gone, so the delete waits for them or they see it gone."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("billingAccount", account_id)
        row = parties.get_billing_account(conn, uid, "update")
        if row is None:
            raise not_found("billingAccount", account_id)
        counts = count_references(conn, uid, parties.ACCOUNT_HISTORY)
        refuse_if_referenced("billingAccount", uid, counts, "close it instead (state Closed)")
        emit.deleted(conn, API, "billingAccount", lambda: account_body(conn, row), at=clock.now())
        presentation.remove_preferences(conn, uid)
        delete_row(conn, "billing_account", uid)
    return Response(status_code=204)
