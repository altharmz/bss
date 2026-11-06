"""Account security in the order engine (docs/decisions/accounts.md): a SIM swap or a number
change on a prepaid line is an agent action that can take the line over, so the order must carry
the customer's PIN (`OrderRequest.actor`, from the X-Account-Pin header), and a SIM swap is also
refused while the line has a SIM swap lock.

`screen` runs before the order is validated, in the order's transaction: a refusal is a problem
of the stored rejected order (and its audit entry and PIN counter commit with it). `record`
writes the audit entry of each action that was accepted. Orders without an actor (the worker's
lifecycle, a port-out) are not agent actions and are not screened for a PIN; a SIM swap lock
still applies.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.ordering import network, numbers
from app.repository import security as repo
from app.repository._sql import Row
from app.security import guard

UNVERIFIED = guard.Actor("system")


@dataclass(frozen=True)
class Sensitive:
    item_id: str
    action: str  # sim_swap | number_change
    product_id: object
    customer_id: object
    account_id: object


def sensitive_items(conn: psycopg.Connection, request, account: Row, locked, start) -> list:
    if account["rating_type"] != "prepaid":
        return []
    found = []
    for item in request.items:
        if item.action != "modify" or item.product_id is None:
            continue
        if numbers.references(item) is None or item.product_id not in locked:
            continue
        product = locked[item.product_id]
        moved = numbers.changes(conn, item, product, start)
        kinds = []
        if "imsi" in moved and network.offering_profile(conn, product["product_offering_id"]):
            kinds.append("sim_swap")
        if "msisdn" in moved:
            kinds.append("number_change")
        found += [
            Sensitive(item.item_id, kind, product["id"], account["customer_id"], account["id"])
            for kind in kinds
        ]
    return found


def screen(
    conn: psycopg.Connection, request, account: Row, locked, start, now: datetime
) -> tuple[list[tuple[str, str, str]], list[tuple[Sensitive, str]]]:
    """(problems, accepted): the problems as (item id, code, reason); accepted pairs each
    sensitive item with its verification mode, to be recorded once the order is placed."""
    items = sensitive_items(conn, request, account, locked, start)
    problems: list[tuple[str, str, str]] = []
    accepted: list[tuple[Sensitive, str]] = []
    actor = request.actor
    mode: str | None = None
    refusal: guard.Refusal | None = None  # one verdict per order: a wrong PIN counts once
    for item in items:
        try:
            if item.action == "sim_swap":
                guard.check_lock(conn, item.product_id, "sim_swap")
            if actor is not None:
                if refusal is not None:
                    raise refusal
                if mode is None:
                    mode = guard.verify(conn, [item.customer_id], actor, now)
            accepted.append((item, mode or "none"))
        except guard.Refusal as exc:
            if exc.code != "simSwapLocked":
                refusal = exc
            who = actor or UNVERIFIED
            repo.record(
                conn,
                now=now,
                operator=who.operator,
                role=who.role,
                action=item.action,
                outcome="refused",
                reason=exc.code,
                verification="none",
                customer_id=item.customer_id,
                billing_account_id=item.account_id,
                product_id=item.product_id,
                detail={"productOrderItem": item.item_id},
            )
            problems.append((item.item_id, exc.code, str(exc)))
    return problems, accepted


def record(conn: psycopg.Connection, request, accepted, order_id, now: datetime) -> None:
    actor = request.actor or UNVERIFIED
    for item, mode in accepted:
        guard.done(
            conn,
            actor,
            now,
            action=item.action,
            verification=mode,
            customer_id=item.customer_id,
            product_id=item.product_id,
            account_id=item.account_id,
            detail={"productOrder": str(order_id), "productOrderItem": item.item_id},
        )
