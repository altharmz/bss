"""The self-care API's reads and writes for a signed-in customer (#376,
docs/decisions/self-care.md "The self-care API").

Every request is scoped to the session's customer (`CareActor`, app/repository/self_care.py):
another customer's object is a 404. The reads are built on the internal API's own views (the
first-charge quote, the usage summary, the transaction ledger); the writes go through the same
services as the operator's, with the customer as actor: a card top-up is a gateway command, a
card is saved from a gateway token only (never a number), and an add-on is a TMF622 order.
Each write is audited (`customer:<login id>`, verification `session`), counted against the
session's limit of 30 a minute, and the POSTs take an `Idempotency-Key` (the P4b journal).
"""

import copy
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api import ordering as tmf_ordering
from app.api import payment_method as cards
from app.api.common import Money, money, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, bad_request, not_found, unprocessable
from app.api.listing import page_response, paging_headers, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.billing import entitlement
from app.care.deps import CareActor, CareSession
from app.config import settings
from app.events import emit
from app.integrations.payment import CardRejected
from app.integrations.payment import adapter as gateway
from app.internal import accounts as views
from app.internal import payments
from app.internal import usage as usage_views
from app.internal.context import no_query
from app.internal.users import limit_rate
from app.network import kind as offering_kind
from app.ordering.process import ItemRequest, OrderRefused, OrderRequest, place_order
from app.repository import billing, buckets, idempotency, orders, products, security
from app.repository import payment_methods as methods
from app.repository import self_care as scope
from app.repository._sql import Row
from app.security import guard

router = APIRouter(prefix="/self-care/v1")

ROLE = "customer"
VERIFICATION = "session"
WRITES_PER_MINUTE = 30
KINDS = (offering_kind.PLAN, offering_kind.ADD_ON)
# a line is what a customer has a number on: plans and pay-as-you-go, not their add-on packs
NOT_LIVE = ("terminated", "cancelled", "aborted")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Id = Annotated[str, StringConstraints(max_length=64)]


class TopUp(Body):
    amount: Money
    paymentMethodId: Id


class NewCard(Body):
    token: str = Field(min_length=1, max_length=255)
    billingAccountId: Id | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)


class AutoTopUp(Body):
    enabled: bool
    threshold: Money | None = None
    amount: Money | None = None
    billingAccountId: Id | None = None


class AddOn(Body):
    lineId: Id
    offeringId: Id


def actor_name(actor: CareSession) -> str:
    """Who acts, in the audit, the order and the idempotency journal."""
    return f"customer:{actor.login_id}"


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _json(body: Any, status_code: int = 200) -> Response:
    return _no_store(TmfJSONResponse(body, status_code=status_code))


def _list(items: list[dict[str, Any]]) -> Response:
    """A whole list (these are never long): X-Result-Count, like every list (#312)."""
    return _no_store(TmfJSONResponse(items, headers=paging_headers(len(items), False, None)))


def _uuid(name: str, raw: str) -> UUID:
    return parse_id(name, raw)


def _own_account(
    conn: psycopg.Connection, actor: CareSession, raw: str | None, *, lock: bool = False
) -> Row:
    """The named account of the customer, else their only prepaid account (a 422 when they
    have several or none: name billingAccountId)."""
    if raw is not None:
        row = scope.account(conn, actor.customer_id, _uuid("billingAccount", raw), lock=lock)
        if row is None:
            raise not_found("billingAccount", raw)
        return row
    prepaid = [a for a in scope.accounts(conn, actor.customer_id) if a["rating_type"] == "prepaid"]
    if len(prepaid) != 1:
        raise unprocessable(
            f"billingAccountId is required: the customer has {len(prepaid)} prepaid accounts"
        )
    if not lock:
        return prepaid[0]
    row = scope.account(conn, actor.customer_id, prepaid[0]["id"], lock=True)
    assert row is not None
    return row


def _audit(
    conn: psycopg.Connection,
    actor: CareSession,
    now,
    action: str,
    account: Row,
    *,
    product_id: UUID | None = None,
    outcome: str = "done",
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    security.record(
        conn,
        now=now,
        operator=actor_name(actor),
        role=ROLE,
        action=action,
        outcome=outcome,
        verification=VERIFICATION,
        reason=reason,
        customer_id=actor.customer_id,
        billing_account_id=account["id"],
        product_id=product_id,
        detail={"login": str(actor.login_id), **(detail or {})},
    )


class Write:
    """A self-care write: the session's rate limit, then (POST) the Idempotency-Key journal in
    the request's transaction: `replay` is the first answer to a repeated key, `keep` stores a
    success."""

    def __init__(self, request: Request, tx: Tx, actor: CareSession, now, body: Any = None):
        limit_rate(tx, f"care-write|{actor.token_hash}", now, WRITES_PER_MINUTE, "self-care writes")
        self.request, self.actor, self.now = request, actor, now
        self.key = idempotency.header(request)
        self.digest = (
            None
            if self.key is None
            else idempotency.request_hash(list(request.query_params.multi_items()), body)
        )

    def replay(self, conn: psycopg.Connection) -> Response | None:
        if self.key is None:
            return None
        found = idempotency.begin(
            conn, actor_name(self.actor), "POST", self.request.url.path, self.key, self.digest
        )
        return None if found is None else _no_store(idempotency.replayed(found))

    def keep(self, conn: psycopg.Connection, body: dict[str, Any], status_code: int) -> Response:
        if self.key is not None:
            idempotency.store(
                conn, actor_name(self.actor), "POST", self.request.url.path, self.key,
                self.digest, status_code=status_code, response=body, location=None, now=self.now,
            )  # fmt: skip
        return _json(body, status_code)


# --- views ---


def _ref(uid: Any) -> dict[str, str]:
    return {"id": str(uid)}


def _card(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "billingAccount": _ref(row["billing_account_id"]),
        "name": row["name"],
        "brand": row["brand"],
        "lastFourDigits": row["last_four"],
        "expirationDate": None if row["expiration_date"] is None else utc(row["expiration_date"]),
        "isDefault": row["is_default"],
    }


def _auto(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    body = payments.auto_body(conn, row)
    default = body["defaultPaymentMethod"]
    return {
        "enabled": body["enabled"],
        "threshold": body["threshold"],
        "amount": body["amount"],
        "defaultPaymentMethod": None if default is None else _ref(default["id"]),
    }


def _account(conn: psycopg.Connection, row: Row) -> dict[str, Any]:
    prepaid = row["rating_type"] == "prepaid"
    balance = None
    if prepaid:
        figures = buckets.figures(conn, row["id"])
        balance = {
            "spendable": money(figures["ledger"] - figures["reserved"], row["currency"]),
            "held": money(figures["reserved"], row["currency"]),
        }
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "state": row["state"],
        "ratingType": row["rating_type"],
        "currency": row["currency"],
        "balance": balance,
        "autoTopUp": _auto(conn, row) if prepaid else None,
        "serviceAddress": row["service_address"],
    }


def _command(row: Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "billingAccount": _ref(row["billing_account_id"]),
        "paymentMethod": _ref(row["payment_method_id"]),
        "amount": money(row["amount"], row["currency"]),
        "state": row["state"],
        "declineReason": row["decline_reason"],
        "createdAt": utc(row["created_at"]),
        "confirmedAt": None if row["confirmed_at"] is None else utc(row["confirmed_at"]),
    }


# --- reads ---


@router.get("/account")
def get_care_accounts(request: Request, tx: Tx, actor: CareActor):
    """The customer's billing accounts: balance (spendable, held) and auto top-up of the
    prepaid ones, and the service address."""
    no_query(request)
    with tx() as conn:
        items = [_account(conn, row) for row in scope.accounts(conn, actor.customer_id)]
    return _list(items)


@router.get("/line")
def get_care_lines(request: Request, tx: Tx, clock: ClockDep, actor: CareActor):
    """The prepaid lines (plans and pay-as-you-go, not their packs) that are not ended:
    number, plan, status, paid-through date, renewal mode and the add-on packs live today."""
    no_query(request)
    today = clock.today()
    items = []
    with tx() as conn:
        for account in scope.accounts(conn, actor.customer_id):
            if account["rating_type"] != "prepaid":
                continue
            rows = [
                p
                for p in products.list_open_products(conn, account["id"], today)
                if p["parent_product_id"] is None
            ]
            if not rows:
                continue
            numbers = {
                n["product_id"]: n["name"]
                for n in buckets.numbers(conn, [p["id"] for p in rows], today)
            }
            card = account["auto_topup_enabled"] and methods.default_of(conn, account["id"])
            for p in rows:
                view = entitlement.view(conn, p, account, today)
                items.append(
                    {
                        "id": str(p["id"]),
                        "billingAccount": _ref(account["id"]),
                        "number": numbers.get(p["id"]),
                        "plan": {"id": str(p["offering_id"]), "name": p["offering_name"]},
                        "offeringKind": offering_kind.kind_of(p["offering_details"]),
                        "status": view["status"],
                        "startDate": p["start_date"],
                        "paidThrough": view["paidThrough"],
                        "renewalMode": "balanceThenCard" if card else "balance",
                        "addOns": [
                            {
                                "id": str(a["id"]),
                                "name": a["name"],
                                "endDate": a["termination_date"],
                            }
                            for a in products.live_add_ons(conn, p["id"], today)
                        ],
                    }
                )
    return _list(items)


@router.get("/line/{line_id}/usage")
def get_care_line_usage(line_id: str, request: Request, tx: Tx, clock: ClockDep, actor: CareActor):
    """The line's usage in the plan period covering today, against its allowance."""
    no_query(request)
    today = clock.today()
    with tx() as conn:
        line = scope.product(conn, actor.customer_id, _uuid("line", line_id))
        if line is None:
            raise not_found("line", line_id)
        body = usage_views.summary(conn, line["id"], today)
    payg = body["payAsYouGo"]
    return _json(
        {
            "line": _ref(line["id"]),
            "date": today,
            "period": body["period"],
            "allowances": body["allowances"],
            "throttled": body["throttled"],
            "payAsYouGo": None
            if payg is None
            else {
                "balance": payg["balance"],
                "barred": payg["barred"],
                "unratedRecords": payg["unratedRecords"],
            },
        }
    )


@router.get("/history")
def get_care_history(request: Request, tx: Tx, actor: CareActor):
    """The account's transaction ledger (top-ups, renewals, charges, transfers), newest
    first, paged with offset and limit."""
    offset, limit = parse_page(request, extra=("billingAccountId",))
    raw = request.query_params.get("billingAccountId")
    if len(request.query_params.getlist("billingAccountId")) > 1:
        raise bad_request("billingAccountId given more than once")
    with tx() as conn:
        account = _own_account(conn, actor, raw)
        rows, more = billing.page_account_transactions(
            conn, account["id"], tz=settings.billing_tz, offset=offset, limit=limit
        )
    items = [{"id": str(t["id"]), **views.transaction_body(t, account["currency"])} for t in rows]
    return _no_store(page_response(request, items, more, offset, limit))


@router.get("/offering")
def get_care_offerings(request: Request, tx: Tx, clock: ClockDep, actor: CareActor):
    """The offerings of a kind (`addOn` or `plan`) on sale today, each with what buying it
    today would charge first on the account (the operator's firstCharge quote)."""
    for name in request.query_params:
        if name not in ("kind", "billingAccountId"):
            raise bad_request(f"unknown query parameter {name}")
    kind = request.query_params.get("kind")
    if kind not in KINDS:
        raise bad_request(f"kind must be one of {', '.join(KINDS)}")
    raw = request.query_params.get("billingAccountId")
    today = clock.today()
    items = []
    with tx() as conn:
        account = _own_account(conn, actor, raw)
        if account["rating_type"] != "prepaid":
            raise TmfError(409, "self-care sells plans and add-ons to prepaid accounts only")
        for offering in scope.offerings(conn, clock.now()):
            if offering_kind.kind_of(offering["details"]) != kind:
                continue
            try:
                quote = views.quote(conn, account, offering, today, today)
            except TmfError as exc:
                if exc.status != 422:
                    raise
                continue  # no price in the account's currency today: not on sale to it
            items.append(
                {
                    "id": str(offering["id"]),
                    "name": offering["name"],
                    "description": offering["description"],
                    "kind": kind,
                    "billingAccount": _ref(account["id"]),
                    "firstCharge": {
                        "amount": quote["amount"],
                        "period": quote["period"],
                        "available": quote["available"],
                        "shortfall": quote["shortfall"],
                    },
                }
            )
    return _list(items)


@router.get("/paymentMethod")
def get_care_payment_methods(request: Request, tx: Tx, actor: CareActor):
    """The saved cards of the customer's accounts (never a token or number)."""
    no_query(request)
    with tx() as conn:
        items = [_card(row) for row in scope.payment_methods(conn, actor.customer_id)]
    return _list(items)


@router.get("/topUp/{command_id}")
def get_care_top_up(command_id: str, request: Request, tx: Tx, actor: CareActor):
    """A card top-up: pending or sent until the gateway answers, then confirmed (the balance
    rose) or declined (with the gateway's reason)."""
    no_query(request)
    with tx() as conn:
        row = scope.payment_command(conn, actor.customer_id, _uuid("topUp", command_id))
    if row is None:
        raise not_found("topUp", command_id)
    return _json(_command(row))


# --- writes ---


@router.post("/topUp", status_code=202)
def post_care_top_up(request: Request, body: JsonBody, tx: Tx, clock: ClockDep, actor: CareActor):
    """Charge a saved card for the amount: the gateway command of the operator's card top-up.
    The balance rises when the gateway confirms (GET /topUp/{id})."""
    no_query(request)
    cards.refuse_card_data(body)
    write = Write(request, tx, actor, clock.now(), copy.deepcopy(body))
    data = parse(TopUp, body)
    with tx() as conn:
        if (replay := write.replay(conn)) is not None:
            return replay
        method = scope.payment_method(
            conn, actor.customer_id, _uuid("paymentMethod", data.paymentMethodId)
        )
        if method is None:
            raise not_found("paymentMethod", data.paymentMethodId)
        account = scope.account(conn, actor.customer_id, method["billing_account_id"], lock=True)
        assert account is not None
        row, _ = payments.queue_card_topup(
            conn, account, data.amount, method["id"], f"care-{uuid4()}", write.now
        )
        _audit(
            conn,
            actor,
            write.now,
            "care_top_up",
            account,
            detail={"paymentCommand": str(row["id"]), "amount": str(data.amount.value)},
        )
        return write.keep(conn, _command(row), 202)


@router.post("/paymentMethod", status_code=201)
def post_care_payment_method(
    request: Request, body: JsonBody, tx: Tx, clock: ClockDep, actor: CareActor
):
    """Save a card from the gateway's token (its client-side form): the gateway says which
    card it is. A card number or security code is a 400 (payments.md, PCI)."""
    no_query(request)
    cards.refuse_card_data(body)
    write = Write(request, tx, actor, clock.now(), copy.deepcopy(body))
    data = parse(NewCard, body)
    try:
        card = gateway().card(data.token)
    except CardRejected as exc:
        raise TmfError(
            422, f"the gateway did not accept the card: {exc.reason}", code="cardRejected"
        ) from None
    if card.expires is not None and card.expires <= write.now:
        raise unprocessable("the card has expired")
    with tx() as conn:
        if (replay := write.replay(conn)) is not None:
            return replay
        account = _own_account(conn, actor, data.billingAccountId, lock=True)
        if account["rating_type"] != "prepaid":
            raise TmfError(409, "cards are saved on prepaid accounts only")
        brand = card.brand or "card"
        row = cards.save_card(
            conn,
            account,
            name=data.name or f"{brand} ending {card.last_four or '????'}",
            description=None,
            brand=card.brand,
            card_type=None,
            last_four=card.last_four,
            expiration_date=card.expires,
            token=data.token,
            preferred=None,
            now=write.now,
        )
        _audit(
            conn,
            actor,
            write.now,
            "care_card_saved",
            account,
            detail={"paymentMethod": str(row["id"])},
        )
        return write.keep(conn, _card(row), 201)


@router.delete("/paymentMethod/{method_id}", status_code=204)
def delete_care_payment_method(
    method_id: str, request: Request, tx: Tx, clock: ClockDep, actor: CareActor
):
    """Remove a saved card; 409 while a charge with it is in progress."""
    no_query(request)
    write = Write(request, tx, actor, clock.now())
    with tx() as conn:
        uid = _uuid("paymentMethod", method_id)
        found = scope.payment_method(conn, actor.customer_id, uid)
        if found is None:
            raise not_found("paymentMethod", method_id)
        account = scope.account(conn, actor.customer_id, found["billing_account_id"], lock=True)
        found = scope.payment_method(conn, actor.customer_id, uid, lock=True)
        if account is None or found is None:
            raise not_found("paymentMethod", method_id)
        cards.remove_card(conn, found, write.now)
        _audit(
            conn,
            actor,
            write.now,
            "care_card_removed",
            account,
            detail={"paymentMethod": method_id},
        )
    return Response(status_code=204)


@router.put("/autoTopUp")
def put_care_auto_top_up(
    request: Request, body: JsonBody, tx: Tx, clock: ClockDep, actor: CareActor
):
    """Turn auto top-up on (threshold and amount, the amount at least the threshold) or off:
    below the threshold the default card is charged for the amount."""
    no_query(request)
    write = Write(request, tx, actor, clock.now())
    data = parse(AutoTopUp, body)
    with tx() as conn:
        account = _own_account(conn, actor, data.billingAccountId, lock=True)
        payments.set_auto_topup(
            conn,
            account,
            payments.AutoTopup(enabled=data.enabled, threshold=data.threshold, amount=data.amount),
            write.now,
        )
        row = scope.account(conn, actor.customer_id, account["id"])
        assert row is not None
        view = _auto(conn, row)
        _audit(
            conn,
            actor,
            write.now,
            "care_auto_top_up",
            account,
            detail={
                "enabled": data.enabled,
                "threshold": None if view["threshold"] is None else str(view["threshold"]["value"]),
                "amount": None if view["amount"] is None else str(view["amount"]["value"]),
            },
        )
    return _json(view)


def _refusal(errors: list[dict[str, Any]]) -> TmfError:
    first = errors[0] if errors else {"code": "orderRejected", "reason": "the order was rejected"}
    return TmfError(
        422,
        "; ".join(f"{e['code']}: {e['reason']}" for e in errors) or str(first["reason"]),
        code=first["code"],
    )


@router.post("/addOn", status_code=201)
def post_care_add_on(request: Request, body: JsonBody, tx: Tx, clock: ClockDep, actor: CareActor):
    """Buy an add-on pack for a line: a TMF622 order with the operator's acceptance rules.
    Accepted: the order (inProgress until the network confirms the new allowance; the fee is
    held now and charged then). Refused: 422 with the order's first TMF622 error code."""
    no_query(request)
    write = Write(request, tx, actor, clock.now(), copy.deepcopy(body))
    data = parse(AddOn, body)
    line_id = _uuid("line", data.lineId)
    offering_id = _uuid("offering", data.offeringId)
    refused = None
    with tx() as conn:
        if (replay := write.replay(conn)) is not None:
            return replay
        line = scope.product(conn, actor.customer_id, line_id)
        if line is None:
            raise not_found("line", data.lineId)
        offering = conn.execute(
            "SELECT details FROM product_offering WHERE id = %s", (offering_id,)
        ).fetchone()
        if offering is None or offering_kind.kind_of(offering[0]) != offering_kind.ADD_ON:
            raise not_found("offering", data.offeringId)
        account = scope.account(conn, actor.customer_id, line["billing_account_id"])
        assert account is not None
        request_ = OrderRequest(
            billing_account_id=account["id"],
            items=(
                ItemRequest(
                    item_id="1",
                    action="add",
                    product_offering_id=offering_id,
                    billing_account_id=account["id"],
                    details={
                        "product": {
                            "@type": "ProductRefOrValue",
                            "productRelationship": [
                                {
                                    "@type": "ProductRelationship",
                                    "relationshipType": "addOn",
                                    "id": str(line_id),
                                }
                            ],
                        }
                    },
                ),
            ),
            actor=guard.Actor(actor_name(actor), ROLE),
        )
        try:
            order_id = place_order(conn, request_, now=write.now, today=clock.today())
        except OrderRefused as exc:
            raise unprocessable(str(exc)) from None
        row = orders.get_order(conn, order_id)
        assert row is not None
        tmf = tmf_ordering.order_body(conn, row)
        emit.created(conn, tmf_ordering.API, "productOrder", tmf, at=write.now)
        errors = [
            {"code": e["code"], "reason": e.get("message") or e["reason"]}
            for e in tmf.get("productOrderErrorMessage", [])
        ]
        detail = {"productOrder": str(order_id), "offering": str(offering_id)}
        if tmf["state"] in ("rejected", "failed"):
            refused = _refusal(errors)
            # the rejected order stays, as on TMF622; the audit says why
            _audit(
                conn, actor, write.now, "care_add_on", account, product_id=line_id,
                outcome="refused", reason=refused.code, detail=detail,
            )  # fmt: skip
        else:
            _audit(
                conn, actor, write.now, "care_add_on", account, product_id=line_id, detail=detail
            )
            return write.keep(
                conn,
                {
                    "id": str(order_id),
                    "state": tmf["state"],
                    "line": _ref(line_id),
                    "offering": _ref(offering_id),
                },
                201,
            )
    raise refused
