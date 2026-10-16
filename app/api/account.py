"""TMF666 Account Management v5: billingAccount.

Two things TMF666 has no attribute for are carried by a documented extension, following
the TMF extension pattern (`@type` names the subclass, `@baseType` the TMF class,
`@schemaLocation` the JSON Schema of the additions, served by this app):

- `billCycleDay` (1..28): the day of month on which the account's billing period starts.
  The closest spec attribute, billStructure.cycleSpecification.billingDateShift, is an
  offset of the bill date *from* the period start - a different thing.
- `currency`: the account's single currency. Immutable.

`billCycleDay` is fixed after creation for now (PATCH is a 409): how a change treats the
current billing period is an open question for stage 3.

The balance is the spec's own `accountBalance[]`: one read-only `currentBalance` entry,
computed on every read. Positive = the customer is in credit (payments exceed charges).

`creditLimit` is the spec's own Money attribute, always in the account's currency.
Informational only: nothing (ordering included) is ever refused because of it.
"""

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field

from app.api import listing
from app.api.common import (
    CENT,
    CustomerRef,
    Money,
    TmfModel,
    check_type,
    columns,
    created,
    money,
    not_null,
    parse,
    parse_id,
    ref,
    utc,
)
from app.api.customer import customer_href
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import conflict, not_found, unprocessable
from app.api.presentation import BillStructure, structure_body
from app.api.tmfjson import TmfJSONResponse
from app.repository import billing, parties, presentation
from app.repository._sql import Row

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
ATTRIBUTES = (
    "name",
    "billStructure",
    "state",
    "relatedParty",
    "accountBalance",
    "creditLimit",
    "billCycleDay",
    "currency",
    "lastUpdate",
    "@baseType",
    "@schemaLocation",
)
BALANCE_TYPE = "currentBalance"
# The one related party of an account is its owning customer (`customer_id`). Its role is
# required and must be exactly this, so the role the response states is the one stored -
# never a rewrite of whatever the client sent.
CUSTOMER_ROLE = "customer"

# The implemented policy is shared by all accounts; the calendar anchor remains the
# account's billCycleDay extension, not TMF's billingDateShift (an offset).
CYCLE_ID = "monthly"
CYCLE_BASE = f"{BASE}/billingCycleSpecification"
CYCLE_ATTRIBUTES = ("name", "description", "billingPeriod", "frequency", "billingDateShift")


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
    }


@router.get("/billingCycleSpecification")
def list_cycle_specifications(request: Request) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "billingPeriod": ("billingPeriod", listing.as_str),
            "frequency": ("frequency", listing.as_str),
        },
        attributes=CYCLE_ATTRIBUTES,
    )
    policy = cycle_specification_body()
    rows = [policy] if all(policy[key] == value for key, value in query.where.items()) else []
    return listing.list_response(rows[query.offset : query.offset + query.limit], len(rows), query)


@router.get("/billingCycleSpecification/{specification_id}")
def get_cycle_specification(specification_id: str, request: Request) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, CYCLE_ATTRIBUTES)
    if specification_id != CYCLE_ID:
        raise not_found("billingCycleSpecification", specification_id)
    return TmfJSONResponse(listing.select_fields(cycle_specification_body(), fields))


class RelatedParty(TmfModel):
    role: str | None = None
    party_or_party_role: CustomerRef


class AccountCreate(TmfModel):
    name: str = Field(min_length=1)
    related_party: list[RelatedParty] = Field(min_length=1, max_length=1)
    bill_cycle_day: int = Field(ge=1, le=28, strict=True)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    state: AccountState = "Active"
    credit_limit: Money | None = None
    last_update: Any = None
    bill_structure: BillStructure | None = None


class AccountPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    state: AccountState | None = None
    credit_limit: Money | None = None
    bill_structure: BillStructure | None = None


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


def account_href(account_id: Any) -> str:
    return f"{BASE}/billingAccount/{account_id}"


def account_ref(row: Row) -> dict[str, Any]:
    return ref("BillingAccount", account_href(row["id"]), row["id"], row["name"])


def account_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    customer = parties.get_customer(conn, row["customer_id"])
    assert customer is not None  # foreign key
    return {
        "id": str(row["id"]),
        "href": account_href(row["id"]),
        "name": row["name"],
        "state": row["state"],
        "billStructure": {
            **structure_body(conn, row["id"]),
            "@type": "BillStructure",
            "cycleSpecification": ref(
                "BillingCycleSpecification",
                f"{CYCLE_BASE}/{CYCLE_ID}",
                CYCLE_ID,
                "Monthly billing",
            ),
        },
        "relatedParty": [
            {
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
        ],
        "accountBalance": [
            {
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
            "relatedParty.id": ("customer_id", listing.as_uuid),
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
    data = parse(AccountCreate, body, read_only=("accountBalance",))
    (party,) = data.related_party
    if party.role != CUSTOMER_ROLE:
        sent = "no role" if party.role is None else f"role {party.role!r}"
        raise unprocessable(
            f'relatedParty: exactly one party with role "{CUSTOMER_ROLE}" is required, not {sent}'
        )
    customer_id = party.party_or_party_role.id
    with tx() as conn:
        if parties.get_customer(conn, customer_id) is None:
            raise unprocessable(f"relatedParty: customer {customer_id} does not exist")
        row = parties.create_billing_account(
            conn,
            customer_id=customer_id,
            name=data.name,
            currency=data.currency,
            bill_cycle_day=data.bill_cycle_day,
            state=data.state,
            credit_limit=_credit_limit(data.credit_limit, data.currency),
            opened_at=clock.now(),
        )
        if data.bill_structure is not None:
            presentation.set_preferences(conn, row["id"], data.bill_structure)
            row = parties.get_billing_account(conn, row["id"])
        new = account_body(conn, row)
    return created(listing.select_fields(new, fields))


@router.patch("/billingAccount/{account_id}")
def patch_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, EXTENSION_TYPE, "BillingAccount")
    if "billCycleDay" in body:
        # Open question for stage 3: how a cycle change treats the current period.
        raise conflict(
            "billCycleDay cannot be changed yet: how a change treats the current billing"
            " period is not decided"
        )
    data = parse(
        AccountPatch,
        body,
        read_only=("accountBalance", "currency", "relatedParty", "lastUpdate"),
    )
    not_null(data, "name", "state", "bill_structure")
    with tx() as conn:
        uid = parse_id("billingAccount", account_id)
        current = parties.get_billing_account(conn, uid)
        if current is None:
            raise not_found("billingAccount", account_id)
        billing.load_account(conn, uid)
        changes = columns(data, exclude=frozenset({"credit_limit", "bill_structure"}))
        if "credit_limit" in data.model_fields_set:  # null removes it
            changes["credit_limit"] = _credit_limit(data.credit_limit, current["currency"])
        row = parties.update_billing_account(conn, uid, changes)
        if data.bill_structure is not None:
            presentation.set_preferences(conn, uid, data.bill_structure)
            row = parties.get_billing_account(conn, uid)
        patched = account_body(conn, row)
    return TmfJSONResponse(listing.select_fields(patched, fields))
