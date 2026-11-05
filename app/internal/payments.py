"""Card top-ups, auto top-up settings and payment commands for operators, under
/internal/v1. Ours, not TMF (docs/decisions/payments.md).

`POST /billingAccount/{id}/cardTopup` queues one charge of a saved card; **the API never
charges and never changes the balance**: the billing worker submits it to the gateway and
the balance rises only when the gateway confirms (the TMF676 payment and TMF654 top-up are
then recorded). The same `idempotencyKey` on the same account is the same charge (200 with
the first command; 409 if its amount or card differ). `PATCH /billingAccount/{id}/autoTopup`
sets the opt-in rule: below the threshold, charge the default card for the amount.
`/payment/command` reads the charges (state, decline reason, last error) and retries an
exhausted one.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field

from app.api.account import account_href
from app.api.common import Money, Ref, TmfModel, money, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, MergePatchBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.listing import parse_page
from app.api.payment_method import method_href
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query
from app.repository import parties
from app.repository import payment_commands as repo
from app.repository import payment_methods as methods
from app.repository._sql import Row
from app.worker.payments import charge_request, usable

ACCOUNT_BASE = "/internal/v1/billingAccount"
COMMAND_BASE = "/internal/v1/payment/command"
accounts = APIRouter(prefix=ACCOUNT_BASE)
router = APIRouter(prefix=COMMAND_BASE)

STATES = ("pending", "sent", "confirmed", "declined", "exhausted", "cancelled")


class CardTopup(TmfModel):
    amount: Money
    payment_method: Ref | None = None
    idempotency_key: str = Field(min_length=1, max_length=64)


class AutoTopup(TmfModel):
    enabled: bool
    threshold: Money | None = None
    amount: Money | None = None


def command_href(command_id: Any) -> str:
    return f"{COMMAND_BASE}/{command_id}"


def command_body(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "href": command_href(row["id"]),
        "kind": row["kind"],
        "purpose": row["purpose"],
        "state": row["state"],
        "billingAccount": {
            "id": str(row["billing_account_id"]),
            "href": account_href(row["billing_account_id"]),
        },
        "paymentMethod": {
            "id": str(row["payment_method_id"]),
            "href": method_href(row["payment_method_id"]),
        },
        "amount": money(row["amount"], row["currency"]),
        "idempotencyKey": row["client_key"],
        "attempts": row["attempts"],
        "nextAttemptAt": None if row["next_attempt_at"] is None else utc(row["next_attempt_at"]),
        "lastError": row["last_error"],
        "declineReason": row["decline_reason"],
        "gatewayReference": row["gateway_ref"],
        "payment": None
        if row["payment_id"] is None
        else {
            "id": str(row["payment_id"]),
            "href": f"/tmf-api/payment/v4/payment/{row['payment_id']}",
        },
        "topup": None
        if row["topup_id"] is None
        else {
            "id": str(row["topup_id"]),
            "href": f"/tmf-api/prepayBalanceManagement/v4/topupBalance/{row['topup_id']}",
        },
        "createdAt": utc(row["created_at"]),
        "sentAt": None if row["sent_at"] is None else utc(row["sent_at"]),
        "confirmedAt": None if row["confirmed_at"] is None else utc(row["confirmed_at"]),
    }


def _account(conn, raw_id: str, lock: bool = False) -> Row:
    uid = parse_id("billingAccount", raw_id)
    row = parties.get_billing_account(conn, uid, "update" if lock else None)
    if row is None:
        raise not_found("billingAccount", raw_id)
    return row


def _prepaid(account: Row) -> None:
    if account["rating_type"] != "prepaid":
        raise conflict("card top-ups and auto top-up are for prepaid accounts only")


@accounts.post("/{account_id}/cardTopup")
def card_topup(
    account_id: str, body: JsonBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    data = parse(CardTopup, body)
    if data.amount.value <= 0:
        raise unprocessable("amount.value must be positive")
    with tx() as conn:
        account = _account(conn, account_id, lock=True)
        _prepaid(account)
        if data.amount.unit != account["currency"]:
            raise unprocessable(f"amount.unit must be the account's currency {account['currency']}")
        method = (
            methods.active(conn, data.payment_method.id)
            if data.payment_method is not None
            else methods.default_of(conn, account["id"])
        )
        if data.payment_method is None and method is None:
            raise conflict("the account has no default card: save one or name paymentMethod")
        if method is None or method["billing_account_id"] != account["id"]:
            raise unprocessable("paymentMethod is not an active card of this account")
        existing = repo.by_client_key(conn, account["id"], data.idempotency_key)
        if existing is not None:
            if (
                existing["amount"] != data.amount.value
                or existing["payment_method_id"] != method["id"]
            ):
                raise conflict("this idempotencyKey was used for a different top-up")
            return TmfJSONResponse(command_body(existing))
        if account["state"] == "Closed":
            raise conflict("the billing account is closed")
        now = clock.now()
        reason = usable(method, now)
        if reason is not None:
            raise conflict(reason)
        row = repo.insert(
            conn,
            account_id=account["id"],
            method_id=method["id"],
            purpose="topup",
            amount=data.amount.value,
            currency=account["currency"],
            client_key=data.idempotency_key,
            request=charge_request(method, data.amount.value, account["currency"], "topup"),
            now=now,
        )
    return TmfJSONResponse(command_body(row), status_code=202)


@accounts.get("/{account_id}/cardTopup")
def list_card_topups(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request)
    with tx() as conn:
        account = _account(conn, account_id)
        rows, total = repo.page(
            conn, account_id=account["id"], state=None, offset=offset, limit=limit
        )
    items = [command_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )


def auto_body(conn, account: Row) -> dict[str, Any]:
    default = methods.default_of(conn, account["id"])
    cur = account["currency"]
    return {
        "enabled": account["auto_topup_enabled"],
        "threshold": None
        if account["auto_topup_threshold"] is None
        else money(account["auto_topup_threshold"], cur),
        "amount": None
        if account["auto_topup_amount"] is None
        else money(account["auto_topup_amount"], cur),
        "defaultPaymentMethod": None
        if default is None
        else {"id": str(default["id"]), "href": method_href(default["id"])},
        "changedAt": None
        if account["auto_topup_changed_at"] is None
        else utc(account["auto_topup_changed_at"]),
    }


@accounts.get("/{account_id}/autoTopup")
def get_auto_topup(account_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(auto_body(conn, _account(conn, account_id)))


@accounts.patch("/{account_id}/autoTopup")
def patch_auto_topup(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Turn the rule on (threshold and amount both given; the amount at least the
    threshold, so one top-up always ends the shortfall) or off (they are kept)."""
    no_query(request)
    data = parse(AutoTopup, body)
    with tx() as conn:
        account = _account(conn, account_id, lock=True)
        _prepaid(account)
        threshold = account["auto_topup_threshold"]
        amount = account["auto_topup_amount"]
        for name, given in (("threshold", data.threshold), ("amount", data.amount)):
            if given is not None and given.unit != account["currency"]:
                raise unprocessable(
                    f"{name}.unit must be the account's currency {account['currency']}"
                )
        threshold = data.threshold.value if data.threshold is not None else threshold
        amount = data.amount.value if data.amount is not None else amount
        if data.enabled:
            if threshold is None or amount is None:
                raise unprocessable("threshold and amount are required to turn auto top-up on")
            if threshold <= 0:
                raise unprocessable("threshold must be positive")
            if amount < threshold:
                raise unprocessable(
                    "amount must be at least the threshold: one top-up must end the shortfall"
                )
        conn.execute(
            "UPDATE billing_account SET auto_topup_enabled = %s, auto_topup_threshold = %s,"
            " auto_topup_amount = %s, auto_topup_changed_at = %s WHERE id = %s",
            (data.enabled, threshold, amount, clock.now(), account["id"]),
        )
        return TmfJSONResponse(auto_body(conn, _account(conn, account_id)))


def _one(conn, raw_id: str) -> Row:
    row = repo.get(conn, parse_id("paymentCommand", raw_id))
    if row is None:
        raise not_found("paymentCommand", raw_id)
    return row


def _uuid(request: Request, name: str) -> UUID | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    try:
        return UUID(given[0]) if given else None
    except ValueError:
        raise bad_request(f"{name} must be a UUID") from None


@router.get("")
def list_payment_commands(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("billingAccount.id", "state"))
    account = _uuid(request, "billingAccount.id")
    state = request.query_params.get("state")
    if state is not None and state not in STATES:
        raise bad_request(f"state must be one of {', '.join(STATES)}")
    with tx() as conn:
        rows, total = repo.page(conn, account_id=account, state=state, offset=offset, limit=limit)
    items = [command_body(row) for row in rows]
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )


@router.get("/{command_id}")
def get_payment_command(command_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(command_body(_one(conn, command_id)))


@router.post("/{command_id}/retry")
def retry_payment_command(command_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = repo.get(conn, _one(conn, command_id)["id"], lock=True)
        if row["state"] != "exhausted":
            raise conflict(
                f"only an exhausted payment command can be retried; this one is {row['state']}"
                + (" (a decline is final: make a new top-up)" if row["state"] == "declined" else "")
            )
        row = repo.retry(conn, row["id"])
    return TmfJSONResponse(command_body(row), status_code=202)
