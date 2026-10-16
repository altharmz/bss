"""TMF666 Account Management v5: financialAccount.

A financial account is owned by a party (Individual or Organization: `relatedParty`, role
`owner`, fixed at creation) and aggregates the billing accounts of that party's customers.
The link is set on the billing account (`financialAccount`); here it is the derived,
read-only `accountRelationship[]` (`contains`). `accountBalance[]` is one `currentBalance`
entry per currency of the linked billing accounts - their balances summed, positive = in
credit - and empty without links. Billing never looks at a financial account.
docs/decisions/accounts.md.
"""

from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field

from app.api import listing
from app.api.common import (
    CENT,
    Money,
    Ref,
    TmfModel,
    check_referred_type,
    check_type,
    columns,
    created,
    money,
    not_null,
    parse,
    parse_id,
    ref,
    utc,
    without_nulls,
)
from app.api.deps import JsonBody, MergePatchBody, Tx
from app.api.errors import not_found, unprocessable
from app.api.party import party_href, party_name
from app.api.tmfjson import TmfJSONResponse
from app.repository import accounts, parties
from app.repository._sql import Row

BASE = "/tmf-api/accountManagement/v5"
router = APIRouter(prefix=BASE)

AccountState = Literal["Active", "Suspended", "Closed"]
OWNER_ROLE = "owner"
BALANCE_TYPE = "currentBalance"
ATTRIBUTES = (
    "name",
    "description",
    "accountType",
    "state",
    "relatedParty",
    "creditLimit",
    "accountBalance",
    "accountRelationship",
    "lastUpdate",
)


class FinancialAccountRef(Ref):
    referred_type = "FinancialAccount"


class OwnerParty(TmfModel):
    role: str | None = None
    party_or_party_role: Ref  # Individual or Organization: checked against the party


class FinancialCreate(TmfModel):
    name: str = Field(min_length=1)
    related_party: list[OwnerParty] = Field(min_length=1, max_length=1)
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    state: AccountState = "Active"
    credit_limit: Money | None = None
    last_update: Any = None


class FinancialPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    state: AccountState | None = None
    credit_limit: Money | None = None


def owner_party(conn: psycopg.Connection, related: list[OwnerParty]) -> Row:
    """The one owner: role exactly `owner`, an existing Individual or Organization."""
    (party,) = related
    if party.role != OWNER_ROLE:
        sent = "no role" if party.role is None else f"role {party.role!r}"
        raise unprocessable(
            f'relatedParty: exactly one party with role "{OWNER_ROLE}" is required, not {sent}'
        )
    row = parties.get_party(conn, party.party_or_party_role.id)
    if row is None:
        raise unprocessable(f"relatedParty: party {party.party_or_party_role.id} does not exist")
    check_referred_type(
        party.party_or_party_role, row["party_type"], "relatedParty.partyOrPartyRole"
    )
    return row


def owner_body(conn: psycopg.Connection, party_id: UUID) -> list[dict[str, Any]]:
    party = parties.get_party(conn, party_id)
    assert party is not None  # foreign key
    return [
        {
            "role": OWNER_ROLE,
            "partyOrPartyRole": ref(
                party["party_type"],
                party_href(party),
                party["id"],
                party_name(party),
                ref_type="PartyRef",
            ),
            "@type": "RelatedPartyRefOrPartyRoleRef",
        }
    ]


def credit_limit_columns(limit: Money | None) -> dict[str, Any]:
    """creditLimit in its own currency, never negative; None removes it."""
    if limit is not None and limit.value < 0:
        raise unprocessable("creditLimit.value must not be negative")
    return {
        "credit_limit": None if limit is None else limit.value,
        "credit_limit_currency": None if limit is None else limit.unit,
    }


def credit_limit_body(row: Row) -> dict[str, Any] | None:
    if row["credit_limit"] is None:
        return None
    return money(row["credit_limit"], row["credit_limit_currency"])


def financial_href(uid: Any) -> str:
    return f"{BASE}/financialAccount/{uid}"


def financial_ref(row: Row) -> dict[str, Any]:
    return ref("FinancialAccount", financial_href(row["id"]), row["id"], row["name"])


def link_financial(
    conn: psycopg.Connection, link: FinancialAccountRef, party_id: UUID, current: UUID | None
) -> UUID:
    """The financial account a party account may be linked to: it exists, has the same
    owner, and - unless the link is unchanged - is not Closed."""
    row = accounts.get_financial(conn, link.id, lock=True)
    if row is None:
        raise unprocessable(f"financialAccount {link.id} does not exist")
    if row["party_id"] != party_id:
        raise unprocessable(
            "financialAccount: must be owned by the same party as this account"
            " (for a billing account: the engagedParty of its customer)"
        )
    if row["state"] == "Closed" and row["id"] != current:
        raise unprocessable(f"financialAccount {link.id} is Closed")
    return row["id"]


def relationship(resource_type: str, resource: str, row: Row) -> dict[str, Any]:
    return {
        "relationshipType": "contains",
        "account": ref(
            resource_type,
            f"{BASE}/{resource}/{row['id']}",
            row["id"],
            row["name"],
            ref_type="AccountRef",
        ),
        "@type": "AccountRelationship",
    }


def financial_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    body = {
        "id": str(row["id"]),
        "href": financial_href(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "accountType": row["account_type"],
        "state": row["state"],
        "relatedParty": owner_body(conn, row["party_id"]),
        "creditLimit": credit_limit_body(row),
        "accountBalance": [
            {
                "balanceType": BALANCE_TYPE,
                "amount": money(amount.quantize(CENT), currency),
                "@type": "AccountBalance",
            }
            for currency, amount in accounts.balances(conn, row["id"]).items()
        ],
        "accountRelationship": [
            relationship("BillingAccount", "billingAccount", account)
            for account in accounts.linked_billing_accounts(conn, row["id"])
        ],
        "lastUpdate": utc(row["updated_at"]),
        "@type": "FinancialAccount",
    }
    return without_nulls(body)


@router.get("/financialAccount")
def list_financial_accounts(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "state": ("state", listing.as_str),
            "accountType": ("account_type", listing.as_str),
            "relatedParty.id": ("party_id", listing.as_uuid),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, total = accounts.page_financial(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = [financial_body(conn, row) for row in rows]
    return listing.list_response(items, total, query)


@router.get("/financialAccount/{account_id}")
def get_financial_account(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = accounts.get_financial(conn, parse_id("financialAccount", account_id))
        if row is None:
            raise not_found("financialAccount", account_id)
        body = financial_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post("/financialAccount")
def create_financial_account(body: JsonBody, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "FinancialAccount")
    data = parse(FinancialCreate, body, read_only=("accountBalance", "accountRelationship"))
    with tx() as conn:
        party = owner_party(conn, data.related_party)
        row = accounts.create_financial(
            conn,
            {
                "party_id": party["id"],
                "name": data.name,
                "description": data.description,
                "account_type": data.account_type,
                "state": data.state,
                **credit_limit_columns(data.credit_limit),
            },
        )
        new = financial_body(conn, row)
    return created(listing.select_fields(new, fields))


@router.patch("/financialAccount/{account_id}")
def patch_financial_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "FinancialAccount")
    data = parse(
        FinancialPatch,
        body,
        read_only=("relatedParty", "accountBalance", "accountRelationship", "lastUpdate"),
    )
    not_null(data, "name", "state")
    with tx() as conn:
        uid = parse_id("financialAccount", account_id)
        if accounts.get_financial(conn, uid) is None:
            raise not_found("financialAccount", account_id)
        changes = columns(data, exclude=frozenset({"credit_limit"}))
        if "credit_limit" in data.model_fields_set:  # null removes it
            changes |= credit_limit_columns(data.credit_limit)
        row = accounts.update_financial(conn, uid, changes)
        patched = financial_body(conn, row)
    return TmfJSONResponse(listing.select_fields(patched, fields))
