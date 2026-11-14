"""TMF666 Account Management v5: financialAccount, partyAccount, settlementAccount.

A financial account is owned by a party (Individual or Organization: the `relatedParty`
with role `owner`) and aggregates the billing accounts of that party's customers and the
party's own party and settlement accounts. The link is set on the contained account
(`financialAccount`); here it is the derived, read-only `accountRelationship[]`
(`contains`). `accountBalance[]` is one `currentBalance` entry per currency of the linked
billing accounts - their balances summed, positive = in credit - and empty without
them: party and settlement accounts have no ledger (their `accountBalance` is always
empty). Billing never looks at any of these.

`relatedParty` is a list of parties in any role, as the spec's examples have it (owner,
bill receiver, contact, ...): exactly one has the account's anchor role (`owner` here,
`customer` on a billing account) and is resolved to our party; the others are stored as
sent, checked against the spec. The owner changes only while nothing depends on it.
Every other attribute without behaviour here (contact, taxExemption, paymentPlan,
paymentStatus, ...) is a document: stored as sent, checked against the spec.

Party and settlement accounts share one table and one shape; each has its own path and
`@type`, and neither list contains the other (no polymorphic lists).
docs/decisions/accounts.md.
"""

from decimal import Decimal
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, ValidationError

from app.api import listing, specschema
from app.api.common import (
    CENT,
    Money,
    Ref,
    TmfModel,
    check_referred_type,
    check_server_set,
    check_type,
    claim_id,
    columns,
    created,
    documents_field,
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
from app.api.deletes import delete_row, refuse_if_referenced
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import Problem, conflict, not_found, unprocessable, validation_problems
from app.api.party import party_href, party_name
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import accounts, parties
from app.repository._sql import Row, count_references, rows_by_id, select_by_id

API = "TMF666"
BASE = "/tmf-api/accountManagement/v5"
router = APIRouter(prefix=BASE)

AccountState = Literal["Active", "Suspended", "Closed"]
OWNER_ROLE = "owner"
BALANCE_TYPE = "currentBalance"


def balance_id(currency: str) -> str:
    """An AccountBalance's id within the account's list: one current balance per
    currency. It has no validFor: it is the balance now."""
    return f"{BALANCE_TYPE}-{currency}"


# Documents to us: stored as sent (checked against the spec), no behaviour.
# docs/decisions/accounts.md, "Account attributes".
FINANCIAL_DOCUMENTS = ("contact", "taxExemption")
PARTY_DOCUMENTS = (
    "accountRelationship",
    "billStructure",
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
    "state",
    "relatedParty",
    "creditLimit",
    "accountBalance",
    "accountRelationship",
    "lastUpdate",
    *FINANCIAL_DOCUMENTS,
)


PARTY_ATTRIBUTES = (
    "name",
    "description",
    "accountType",
    "state",
    "relatedParty",
    "creditLimit",
    "financialAccount",
    "accountBalance",
    "lastUpdate",
    *PARTY_DOCUMENTS,
)
# @type (and party_account.kind) -> resource path segment
RESOURCE = {
    "BillingAccount": "billingAccount",
    "PartyAccount": "partyAccount",
    "SettlementAccount": "settlementAccount",
}


class FinancialAccountRef(Ref):
    referred_type = "FinancialAccount"


class OwnerParty(TmfModel):
    """The relatedParty entry with role `owner`."""

    role: Literal["owner"]
    party_or_party_role: Ref  # Individual or Organization: checked against the party


def related_party_field(role: str, *, required: bool) -> Any:
    description = (
        f'TMF666 relatedParty: exactly one party with role "{role}" (resolved, it must'
        " exist); parties in other roles are stored as sent"
    )
    if required:
        return Field(min_length=1, description=description)
    return Field(default=None, description=description)


def related_parties[A: BaseModel](
    body: dict[str, Any], role: str, anchor: type[A], schema: str
) -> tuple[A | None, list[Any] | None, list[Problem]]:
    """The anchor entry of a body's relatedParty (the one with `role`, validated by
    `anchor`), the list to store (the others normalized as the spec's `schema` checks
    them), and the problems found. Nothing when the body has no list: the request model
    reports that."""
    sent = body.get("relatedParty")
    if not isinstance(sent, list):
        return None, None, []
    found: list[Problem] = []
    anchors: list[A] = []
    stored: list[Any] = []
    for i, entry in enumerate(sent):
        path = f"relatedParty.{i}"
        if isinstance(entry, dict) and entry.get("role") == role:
            try:
                anchors.append(anchor.model_validate(entry))
            except ValidationError as exc:
                errors = exc.errors(include_url=False, include_context=False)
                found += [
                    (f"{path}.{where}", message, unknown)
                    for where, message, unknown in validation_problems(errors)
                ]
            stored.append(entry)
            continue
        value, problems = specschema.check_element(API, schema, "relatedParty", entry, path)
        found += problems
        stored.append(value)
    roles = [entry.get("role") if isinstance(entry, dict) else None for entry in sent]
    if roles.count(role) != 1:
        others = sorted({repr(r) for r in roles if isinstance(r, str) and r != role})
        counted = f"{roles.count(role)}" + (
            f" (other roles: {', '.join(others)})" if others else ""
        )
        found.append(
            (
                "relatedParty",
                f'exactly one party with role "{role}" is required, not {counted}',
                False,
            )
        )
    return (anchors[0] if roles.count(role) == 1 and anchors else None), stored, found


def related_body(row: Row, role: str, anchor: dict[str, Any]) -> list[dict[str, Any]]:
    """relatedParty as stored, the anchor entry answered as the resource it resolves to;
    an account stored before 0023 has only its anchor."""
    stored = row["details"].get("relatedParty")
    if stored is None:
        return [anchor]
    return [anchor if entry.get("role") == role else entry for entry in stored]


def details_body(row: Row) -> dict[str, Any]:
    """The documents stored, relatedParty aside (`related_body` answers it)."""
    return {name: value for name, value in row["details"].items() if name != "relatedParty"}


def document_columns(documents: tuple[str, ...]) -> frozenset[str]:
    """Model fields that are not columns: the documents and the relatedParty list."""
    return frozenset({"related_party", *map(documents_field, documents)})


def owner_party(conn: psycopg.Connection, owner: OwnerParty) -> Row:
    """The owner: an existing Individual or Organization."""
    # FOR KEY SHARE until commit: a concurrent delete of the party waits for this account
    row = parties.get_party(conn, owner.party_or_party_role.id, "key share")
    if row is None:
        raise unprocessable(f"relatedParty: party {owner.party_or_party_role.id} does not exist")
    check_referred_type(
        owner.party_or_party_role, row["party_type"], "relatedParty.partyOrPartyRole"
    )
    return row


def owner_body(row: Row, party: Row) -> list[dict[str, Any]]:
    anchor = {
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
    return related_body(row, OWNER_ROLE, anchor)


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


def keep_link(conn: psycopg.Connection, financial_id: UUID | None, party_id: UUID) -> None:
    """After an owner change, a link the request did not change must still be to a
    financial account of the new owner (unlink it in the same request otherwise)."""
    if financial_id is None:
        return
    row = accounts.get_financial(conn, financial_id, lock=True)
    assert row is not None  # foreign key
    if row["party_id"] != party_id:
        raise unprocessable(
            "financialAccount: must be owned by the same party as this account - unlink it"
            " (financialAccount null) or link one of the new owner's"
        )


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


FinancialDocuments = documents_model("FinancialDocuments", API, FINANCIAL_DOCUMENTS)
PartyDocuments = documents_model("PartyDocuments", API, PARTY_DOCUMENTS)


class AccountFields(TmfModel):
    name: str = Field(min_length=1)
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    state: AccountState = "Active"
    credit_limit: Money | None = None
    last_update: Any = None


class AccountPatchFields(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    account_type: str | None = Field(default=None, min_length=1)
    state: AccountState | None = None
    credit_limit: Money | None = None
    related_party: list[Any] | None = related_party_field(OWNER_ROLE, required=False)


class FinancialCreate(AccountFields, FinancialDocuments):
    related_party: list[Any] = related_party_field(OWNER_ROLE, required=True)


class FinancialPatch(AccountPatchFields, FinancialDocuments):
    pass


class PartyAccountCreate(AccountFields, PartyDocuments):
    related_party: list[Any] = related_party_field(OWNER_ROLE, required=True)
    financial_account: FinancialAccountRef | None = None


class PartyAccountPatch(AccountPatchFields, PartyDocuments):
    financial_account: FinancialAccountRef | None = None


def financial_bodies(conn: psycopg.Connection, rows: list[Row]) -> list[dict[str, Any]]:
    """The bodies of a page of financial accounts: owners, linked accounts and balances
    are read for the whole page at once, so a list costs the same queries for 1 or 100
    rows (#303)."""
    ids = [row["id"] for row in rows]
    owners = rows_by_id(conn, "party", [row["party_id"] for row in rows])
    linked = accounts.linked_accounts_of(conn, ids)
    balances = accounts.balances_of(conn, ids)
    return [
        _financial_body(row, owners[row["party_id"]], linked[row["id"]], balances[row["id"]])
        for row in rows
    ]


def financial_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return financial_bodies(conn, [row])[0]


def _financial_body(
    row: Row, owner: Row, linked: list[Row], balances: dict[str, Decimal]
) -> dict[str, Any]:
    body = {
        "id": str(row["id"]),
        "href": financial_href(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "accountType": row["account_type"],
        "state": row["state"],
        "relatedParty": owner_body(row, owner),
        "creditLimit": credit_limit_body(row),
        "accountBalance": [
            {
                "id": balance_id(currency),
                "balanceType": BALANCE_TYPE,
                "amount": money(amount.quantize(CENT), currency),
                "@type": "AccountBalance",
            }
            for currency, amount in balances.items()
        ],
        "accountRelationship": [
            relationship(account["kind"], RESOURCE[account["kind"]], account) for account in linked
        ],
        **details_body(row),
        "lastUpdate": utc(row["updated_at"]),
        "@type": "FinancialAccount",
    }
    return without_nulls(body) | meta_body(row)


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
        rows, more = accounts.page_financial(
            conn, where=query.where, offset=query.offset, limit=query.limit
        )
        items = financial_bodies(conn, rows)
    return listing.list_response(items, more, query)


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
def create_financial_account(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "FinancialAccount")
    uid = take_id(body)
    server_set = take_server_set(body, ("accountBalance", "accountRelationship"))
    owner, related, found = related_parties(body, OWNER_ROLE, OwnerParty, "FinancialAccount_FVO")
    data, documents = parse_documented(
        FinancialCreate, body, FINANCIAL_DOCUMENTS, API, "FinancialAccount_FVO", found
    )
    assert owner is not None and related is not None  # else found a problem
    with tx() as conn:
        party = owner_party(conn, owner)
        claim_id(conn, accounts.FINANCIAL, uid)
        row = accounts.create_financial(
            conn,
            {
                **({} if uid is None else {"id": uid}),
                "party_id": party["id"],
                "name": data.name,
                "description": data.description,
                "account_type": data.account_type,
                "state": data.state,
                **credit_limit_columns(data.credit_limit),
                "details": merge_documents({"relatedParty": related}, documents),
            },
        )
        new = financial_body(conn, with_meta(conn, accounts.FINANCIAL, row, data))
        check_server_set(server_set, new)
        emit.created(conn, API, "financialAccount", new, at=clock.now())
    return created(listing.select_fields(new, fields))


@router.patch("/financialAccount/{account_id}")
def patch_financial_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    check_type(body, "FinancialAccount")
    server_set = take_server_set(body, ("accountBalance", "accountRelationship", "lastUpdate"))
    owner, related, found = related_parties(body, OWNER_ROLE, OwnerParty, "FinancialAccount_MVO")
    data, documents = parse_documented(
        FinancialPatch, body, FINANCIAL_DOCUMENTS, API, "FinancialAccount_MVO", found
    )
    not_null(data, "name", "state", "related_party")
    with tx() as conn:
        uid = parse_id("financialAccount", account_id)
        # FOR UPDATE: a link made meanwhile (it takes the account FOR SHARE) waits, so
        # the owner check below cannot miss it
        current = select_by_id(conn, accounts.FINANCIAL, uid, "update")
        if current is None:
            raise not_found("financialAccount", account_id)
        before = financial_body(conn, current)
        check_server_set(server_set, before)
        old = emit.before(conn, API, "financialAccount", lambda: before)
        changes = columns(data, exclude=document_columns(FINANCIAL_DOCUMENTS) | {"credit_limit"})
        if "credit_limit" in data.model_fields_set:  # null removes it
            changes |= credit_limit_columns(data.credit_limit)
        if owner is not None and related is not None:
            party = owner_party(conn, owner)
            if party["id"] != current["party_id"]:
                if accounts.linked_accounts(conn, uid):
                    raise conflict(
                        "relatedParty: the owner cannot change while accounts link to this"
                        " financial account (they belong to the owner) - unlink them first"
                    )
                changes["party_id"] = party["id"]
            documents["relatedParty"] = related
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        row = accounts.update_financial(conn, uid, changes)
        patched = financial_body(conn, with_meta(conn, accounts.FINANCIAL, row, data))
        emit.changed(conn, API, "financialAccount", old, patched, at=clock.now(), state="state")
    return TmfJSONResponse(listing.select_fields(patched, fields))


@router.delete("/financialAccount/{account_id}", status_code=204, response_class=Response)
def delete_financial_account(
    account_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    """Only while no account links to it; links take it FOR SHARE (`link_financial`)."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id("financialAccount", account_id)
        row = select_by_id(conn, accounts.FINANCIAL, uid, "update")
        if row is None:
            raise not_found("financialAccount", account_id)
        counts = count_references(
            conn, uid, {"billingAccount": ("billing_account", "financial_account_id")}
        )
        for kind, n in accounts.count_party_accounts(conn, "financial_account_id", uid).items():
            counts[RESOURCE[kind]] = n
        refuse_if_referenced("financialAccount", uid, counts, "unlink those accounts first")
        emit.deleted(
            conn, API, "financialAccount", lambda: financial_body(conn, row), at=clock.now()
        )
        delete_row(conn, accounts.FINANCIAL, uid)
    return Response(status_code=204)


# --- partyAccount and settlementAccount: one shape, `kind` = @type ---


def party_account_bodies(conn: psycopg.Connection, rows: list[Row]) -> list[dict[str, Any]]:
    """The bodies of a page of party or settlement accounts: owners and financial
    accounts are read for the whole page at once (#303)."""
    owners = rows_by_id(conn, "party", [row["party_id"] for row in rows])
    financials = rows_by_id(
        conn,
        accounts.FINANCIAL,
        [r["financial_account_id"] for r in rows if r["financial_account_id"]],
    )
    return [
        _party_account_body(
            row, owners[row["party_id"]], financials.get(row["financial_account_id"])
        )
        for row in rows
    ]


def party_account_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return party_account_bodies(conn, [row])[0]


def _party_account_body(row: Row, owner: Row, financial: Row | None) -> dict[str, Any]:
    body = {
        "id": str(row["id"]),
        "href": f"{BASE}/{RESOURCE[row['kind']]}/{row['id']}",
        "name": row["name"],
        "description": row["description"],
        "accountType": row["account_type"],
        "state": row["state"],
        "relatedParty": owner_body(row, owner),
        "creditLimit": credit_limit_body(row),
        "financialAccount": None if financial is None else financial_ref(financial),
        # no ledger: never a balance
        "accountBalance": [],
        **details_body(row),
        "lastUpdate": utc(row["updated_at"]),
        "@type": row["kind"],
    }
    return without_nulls(body) | meta_body(row)


def _list_party_accounts(request: Request, tx: Tx, kind: str) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "name": ("name", listing.as_str),
            "state": ("state", listing.as_str),
            "accountType": ("account_type", listing.as_str),
            "relatedParty.id": ("party_id", listing.as_uuid),
            "financialAccount.id": ("financial_account_id", listing.as_uuid),
        },
        attributes=PARTY_ATTRIBUTES,
    )
    with tx() as conn:
        rows, more = accounts.page_party_accounts(
            conn, kind, where=query.where, offset=query.offset, limit=query.limit
        )
        items = party_account_bodies(conn, rows)
    return listing.list_response(items, more, query)


def _get_party_account(account_id: str, request: Request, tx: Tx, kind: str) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PARTY_ATTRIBUTES)
    with tx() as conn:
        row = accounts.get_party_account(conn, kind, parse_id(RESOURCE[kind], account_id))
        if row is None:
            raise not_found(RESOURCE[kind], account_id)
        body = party_account_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


CREATE_SERVER_SET = ("accountBalance",)
PATCH_SERVER_SET = ("accountBalance", "lastUpdate")


class Sent[M: BaseModel](NamedTuple):
    """A party/settlement account POST or PATCH as its handler parsed it."""

    data: M
    documents: dict[str, Any]
    owner: OwnerParty | None
    related: list[Any] | None
    server_set: dict[str, Any]


def _take(body: dict[str, Any], kind: str, names: tuple[str, ...], schema: str):
    """The attributes taken out before the model parses the rest: server-set ones and
    relatedParty (checked here, its problems reported with the model's)."""
    check_type(body, kind)
    server_set = take_server_set(body, names)
    owner, related, found = related_parties(body, OWNER_ROLE, OwnerParty, schema)
    return server_set, owner, related, found


def _create_party_account(
    sent: Sent[PartyAccountCreate],
    uid: UUID | None,
    request: Request,
    tx: Tx,
    clock: ClockDep,
    kind: str,
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PARTY_ATTRIBUTES)
    data, documents, owner, related, server_set = sent
    assert owner is not None and related is not None  # else parsing found a problem
    with tx() as conn:
        party = owner_party(conn, owner)
        financial_id = (
            None
            if data.financial_account is None
            else link_financial(conn, data.financial_account, party["id"], None)
        )
        claim_id(conn, "party_account", uid)
        row = accounts.create_party_account(
            conn,
            {
                **({} if uid is None else {"id": uid}),
                "kind": kind,
                "party_id": party["id"],
                "name": data.name,
                "description": data.description,
                "account_type": data.account_type,
                "state": data.state,
                "financial_account_id": financial_id,
                **credit_limit_columns(data.credit_limit),
                "details": merge_documents({"relatedParty": related}, documents),
            },
        )
        new = party_account_body(conn, with_meta(conn, "party_account", row, data))
        check_server_set(server_set, new)
        emit.created(conn, API, RESOURCE[kind], new, at=clock.now())

    return created(listing.select_fields(new, fields))


def _patch_party_account(
    account_id: str,
    sent: Sent[PartyAccountPatch],
    request: Request,
    tx: Tx,
    clock: ClockDep,
    kind: str,
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, PARTY_ATTRIBUTES)
    data, documents, owner, related, server_set = sent
    not_null(data, "name", "state", "related_party")
    with tx() as conn:
        uid = parse_id(RESOURCE[kind], account_id)
        current = accounts.get_party_account(conn, kind, uid, lock=True)
        if current is None:
            raise not_found(RESOURCE[kind], account_id)
        before = party_account_body(conn, current)
        check_server_set(server_set, before)
        old = emit.before(conn, API, RESOURCE[kind], lambda: before)
        changes = columns(
            data,
            exclude=document_columns(PARTY_DOCUMENTS) | {"credit_limit", "financial_account"},
        )
        if "credit_limit" in data.model_fields_set:  # null removes it
            changes |= credit_limit_columns(data.credit_limit)
        party_id = current["party_id"]
        if owner is not None and related is not None:
            party_id = owner_party(conn, owner)["id"]
            changes["party_id"] = party_id
            documents["relatedParty"] = related
        if "financial_account" in data.model_fields_set:  # null unlinks
            changes["financial_account_id"] = (
                None
                if data.financial_account is None
                else link_financial(
                    conn, data.financial_account, party_id, current["financial_account_id"]
                )
            )
        elif party_id != current["party_id"]:
            keep_link(conn, current["financial_account_id"], party_id)
        if documents:
            changes["details"] = merge_documents(current["details"], documents)
        row = accounts.update_party_account(conn, uid, changes)
        patched = party_account_body(conn, with_meta(conn, "party_account", row, data))
        emit.changed(conn, API, RESOURCE[kind], old, patched, at=clock.now(), state="state")
    return TmfJSONResponse(listing.select_fields(patched, fields))


def _delete_party_account(
    account_id: str, request: Request, tx: Tx, clock: ClockDep, kind: str
) -> Response:
    """Nothing refers to a party or settlement account: records only, never billed."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE[kind], account_id)
        row = accounts.get_party_account(conn, kind, uid)
        if row is None:
            raise not_found(RESOURCE[kind], account_id)
        emit.deleted(
            conn, API, RESOURCE[kind], lambda: party_account_body(conn, row), at=clock.now()
        )
        delete_row(conn, "party_account", uid)
    return Response(status_code=204)


@router.get("/partyAccount")
def list_party_accounts(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list_party_accounts(request, tx, "PartyAccount")


@router.get("/partyAccount/{account_id}")
def get_party_account(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get_party_account(account_id, request, tx, "PartyAccount")


@router.post("/partyAccount")
def create_party_account(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    uid = take_id(body)
    server_set, owner, related, found = _take(
        body, "PartyAccount", CREATE_SERVER_SET, "PartyAccount_FVO"
    )
    data, documents = parse_documented(
        PartyAccountCreate, body, PARTY_DOCUMENTS, API, "PartyAccount_FVO", found
    )
    sent = Sent(data, documents, owner, related, server_set)
    return _create_party_account(sent, uid, request, tx, clock, "PartyAccount")


@router.patch("/partyAccount/{account_id}")
def patch_party_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    server_set, owner, related, found = _take(
        body, "PartyAccount", PATCH_SERVER_SET, "PartyAccount_MVO"
    )
    data, documents = parse_documented(
        PartyAccountPatch, body, PARTY_DOCUMENTS, API, "PartyAccount_MVO", found
    )
    sent = Sent(data, documents, owner, related, server_set)
    return _patch_party_account(account_id, sent, request, tx, clock, "PartyAccount")


@router.delete("/partyAccount/{account_id}", status_code=204, response_class=Response)
def delete_party_account(account_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    return _delete_party_account(account_id, request, tx, clock, "PartyAccount")


@router.get("/settlementAccount")
def list_settlement_accounts(request: Request, tx: Tx) -> TmfJSONResponse:
    return _list_party_accounts(request, tx, "SettlementAccount")


@router.get("/settlementAccount/{account_id}")
def get_settlement_account(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    return _get_party_account(account_id, request, tx, "SettlementAccount")


@router.post("/settlementAccount")
def create_settlement_account(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    uid = take_id(body)
    server_set, owner, related, found = _take(
        body, "SettlementAccount", CREATE_SERVER_SET, "SettlementAccount_FVO"
    )
    data, documents = parse_documented(
        PartyAccountCreate, body, PARTY_DOCUMENTS, API, "SettlementAccount_FVO", found
    )
    sent = Sent(data, documents, owner, related, server_set)
    return _create_party_account(sent, uid, request, tx, clock, "SettlementAccount")


@router.patch("/settlementAccount/{account_id}")
def patch_settlement_account(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    server_set, owner, related, found = _take(
        body, "SettlementAccount", PATCH_SERVER_SET, "SettlementAccount_MVO"
    )
    data, documents = parse_documented(
        PartyAccountPatch, body, PARTY_DOCUMENTS, API, "SettlementAccount_MVO", found
    )
    sent = Sent(data, documents, owner, related, server_set)
    return _patch_party_account(account_id, sent, request, tx, clock, "SettlementAccount")


@router.delete("/settlementAccount/{account_id}", status_code=204, response_class=Response)
def delete_settlement_account(
    account_id: str, request: Request, tx: Tx, clock: ClockDep
) -> Response:
    return _delete_party_account(account_id, request, tx, clock, "SettlementAccount")
