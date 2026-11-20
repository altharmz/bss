"""TMF670 Payment Method Management v4: a saved card as a gateway token (tokenizedCard).

The BSS never receives a card number: the card is tokenized by the gateway's client-side
form and only the token, brand, last four digits and expiry arrive here (a card number or
security code is a 400). Only `tokenizedCard` methods exist; `account` is the one billing
account that owns the card. `isPreferred` is the account's default card (the one a card
top-up without a method and auto top-up use). DELETE removes the card for good from the
API (the row is kept, so charges already made keep their reference). The token is never
returned. Create, AttributeValueChange and Delete events go to the TMF670 hub.
docs/decisions/payments.md.
"""

import re
from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import AwareDatetime, Field

from app.api import listing
from app.api.account import account_href
from app.api.common import (
    Ref,
    TmfModel,
    check_type,
    created,
    parse,
    parse_id,
    ref,
    utc,
)
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.events import emit
from app.repository import parties
from app.repository import payment_methods as repo
from app.repository._sql import Row, fetch_one, rows_by_id

API = "TMF670"
BASE = "/tmf-api/paymentMethod/v4"
RESOURCE = "paymentMethod"
router = APIRouter(prefix=BASE)

TYPE = "tokenizedCard"
ATTRIBUTES = (
    "name",
    "description",
    "isPreferred",
    "status",
    "statusDate",
    "account",
    "brand",
    "cardType",
    "lastFourDigits",
    "expirationDate",
)
# What a card is never allowed to carry here (PCI): refused by name before anything else.
CARD_DATA = ("cardNumber", "number", "pan", "cvv", "cvc", "cvv2", "securityCode", "nameOnCard")
LOOKS_LIKE_A_NUMBER = re.compile(r"^[\d\s-]{13,23}$")


class AccountRef(Ref):
    referred_type = "BillingAccount"
    description: str | None = None


class PaymentMethodCreate(TmfModel):
    name: str = Field(min_length=1)
    description: str | None = None
    is_preferred: bool | None = None
    account: list[AccountRef] = Field(min_length=1, max_length=1)
    brand: str | None = Field(default=None, min_length=1)
    card_type: str | None = Field(default=None, min_length=1)
    last_four_digits: str | None = Field(default=None, pattern=r"^\d{4}$")
    expiration_date: AwareDatetime | None = None
    token: str = Field(min_length=1, max_length=255)


class PaymentMethodPatch(TmfModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    is_preferred: bool | None = None


def method_href(method_id: Any) -> str:
    return f"{BASE}/{RESOURCE}/{method_id}"


def method_body(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    return method_bodies(conn, [row])[0]


def method_bodies(conn: psycopg.Connection, rows: list[Row]) -> list[dict[str, Any]]:
    accounts = rows_by_id(conn, "billing_account", [row["billing_account_id"] for row in rows])
    return [_method_body(row, accounts[row["billing_account_id"]]) for row in rows]


def _method_body(row: Row, account: Row) -> dict[str, Any]:
    body = {
        "id": str(row["id"]),
        "href": method_href(row["id"]),
        "@type": TYPE,
        "name": row["name"],
        "description": row["description"],
        "isPreferred": row["is_default"],
        "status": "Active" if row["status"] == "active" else "Removed",
        "statusDate": utc(row["status_at"]),
        "brand": row["brand"],
        "cardType": row["card_type"],
        "lastFourDigits": row["last_four"],
        "expirationDate": None if row["expiration_date"] is None else utc(row["expiration_date"]),
        "account": [
            ref(
                "BillingAccount",
                account_href(account["id"]),
                account["id"],
                account["name"],
                ref_type="AccountRef",
            )
        ],
    }
    return {k: v for k, v in body.items() if v is not None}


def refuse_card_data(body: dict[str, Any]) -> None:
    for name in CARD_DATA:
        if name in body:
            raise bad_request(
                f"{name}: a card number or security code is never accepted; send the"
                " gateway's token (tokenizedCard)"
            )
    token = body.get("token")
    if isinstance(token, str) and LOOKS_LIKE_A_NUMBER.match(token):
        raise bad_request(
            "token looks like a card number: send the gateway's token, never a number"
        )


@router.get(f"/{RESOURCE}")
def list_payment_methods(request: Request, tx: Tx) -> TmfJSONResponse:
    query = listing.parse_list_query(
        request,
        filters={
            "id": ("id", listing.as_uuid),
            "account.id": ("billing_account_id", listing.as_uuid),
            "isPreferred": ("is_default", listing.as_bool),
        },
        attributes=ATTRIBUTES,
    )
    with tx() as conn:
        rows, more = repo.page(conn, query.where, query.offset, query.limit)
        items = method_bodies(conn, rows)
    return listing.list_response(items, more, query)


@router.get(f"/{RESOURCE}/{{method_id}}")
def get_payment_method(method_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    with tx() as conn:
        row = repo.active(conn, parse_id(RESOURCE, method_id))
        if row is None:
            raise not_found(RESOURCE, method_id)
        body = method_body(conn, row)
    return TmfJSONResponse(listing.select_fields(body, fields))


@router.post(f"/{RESOURCE}")
def create_payment_method(
    body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    refuse_card_data(body)
    if body.get("@type") != TYPE:
        raise unprocessable(f"@type must be {TYPE} (the only payment method supported)")
    data = parse(PaymentMethodCreate, body, read_only=("status", "statusDate"))
    now = clock.now()
    if data.expiration_date is not None and data.expiration_date <= now:
        raise unprocessable("expirationDate is in the past: the card has expired")
    with tx() as conn:
        # the account's row lock: the default card is decided one request at a time
        account = parties.get_billing_account(conn, data.account[0].id, "update")
        if account is None:
            raise unprocessable(f"account {data.account[0].id} does not exist")
        row = save_card(
            conn,
            account,
            name=data.name,
            description=data.description,
            brand=data.brand,
            card_type=data.card_type,
            last_four=data.last_four_digits,
            expiration_date=data.expiration_date,
            token=data.token,
            preferred=data.is_preferred,
            now=now,
        )
        new = method_body(conn, row)
    return created(listing.select_fields(new, fields))


def save_card(conn: psycopg.Connection, account: Row, *, token: str, now, **card: Any) -> Row:
    """Save a card on the account (locked by the caller) and emit its Create event; self-care
    saves cards with it too. The same token twice on the account is a 409."""
    if fetch_one(
        conn,
        "SELECT 1 FROM payment_method WHERE billing_account_id = %s AND gateway_token = %s"
        " AND status = 'active'",
        (account["id"], token),
    ):
        raise conflict("this card (token) is already saved on the account")
    row = repo.insert(conn, account_id=account["id"], token=token, now=now, **card)
    emit.created(conn, API, RESOURCE, method_body(conn, row), at=now)
    return row


@router.patch(f"/{RESOURCE}/{{method_id}}")
def patch_payment_method(
    method_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Name, description and isPreferred change; a card's token, brand, digits and expiry
    do not (delete it and save the new card)."""
    fields = listing.retrieve_fields(request, ATTRIBUTES)
    refuse_card_data(body)
    check_type(body, "tokenizedCard")
    data = parse(PaymentMethodPatch, body, read_only=("status", "statusDate"))
    for name in ("name", "is_preferred"):
        if name in data.model_fields_set and getattr(data, name) is None:
            raise unprocessable(f"{name} cannot be null")
    with tx() as conn:
        uid = parse_id(RESOURCE, method_id)
        found = repo.active(conn, uid)
        if found is None:
            raise not_found(RESOURCE, method_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        if repo.active(conn, uid) is None:
            raise not_found(RESOURCE, method_id)
        changes = {
            column: getattr(data, column)
            for column in ("name", "description", "is_preferred")
            if column in data.model_fields_set
        }
        if "is_preferred" in changes:
            changes["is_default"] = changes.pop("is_preferred")
        old = emit.before(conn, API, RESOURCE, lambda: method_body(conn, found))
        row = repo.update(conn, uid, changes) if changes else repo.get(conn, uid)
        new = method_body(conn, row)
        emit.changed(conn, API, RESOURCE, old, new, at=clock.now())
    return TmfJSONResponse(listing.select_fields(new, fields))


@router.delete(f"/{RESOURCE}/{{method_id}}", status_code=204, response_class=Response)
def delete_payment_method(method_id: str, request: Request, tx: Tx, clock: ClockDep) -> Response:
    """Refused (409) while a charge with the card is being made. The default card removed
    leaves the account without one."""
    listing.no_query(request)
    with tx() as conn:
        uid = parse_id(RESOURCE, method_id)
        found = repo.active(conn, uid)
        if found is None:
            raise not_found(RESOURCE, method_id)
        parties.get_billing_account(conn, found["billing_account_id"], "update")
        if repo.active(conn, uid, lock=True) is None:
            raise not_found(RESOURCE, method_id)
        remove_card(conn, found, clock.now())
    return Response(status_code=204)


def remove_card(conn: psycopg.Connection, found: Row, now) -> None:
    """Remove a card (locked with its account by the caller) and emit its Delete event; 409
    while a charge with it is in progress. Self-care removes cards with it too."""
    if fetch_one(
        conn,
        "SELECT 1 FROM payment_command WHERE payment_method_id = %s"
        " AND state IN ('pending', 'sent')",
        (found["id"],),
    ):
        raise conflict("a charge with this card is in progress: remove it afterwards")
    emit.deleted(conn, API, RESOURCE, lambda: method_body(conn, found), at=now)
    repo.remove(conn, found["id"], now=now)
