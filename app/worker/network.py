"""Send the network commands of order items and gate the orders on the answers.

`process_network` sends each due command through the adapter (app/integrations/network):
confirmed, refused (final) or a temporary error (retried with backoff, `exhausted` after
`network_max_attempts`; an operator then retries it). The order's own command is posted
only when every network command of the order is confirmed (`gate`). A refusal fails the
order and leaves the products as they were before it (`refuse_order`); an activate that
the network refuses ends the product at its start date with its hold released and no
charge posted. See docs/decisions/network.md.
"""

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.config import settings
from app.events import emit
from app.integrations.network import NetworkRefused, NetworkTemporary, adapter
from app.ordering import numbers as number_orders
from app.ordering.states import item_transition, order_transition
from app.repository import funding, orders, products
from app.repository import network_commands as network
from app.repository import prepaid_commands as commands
from app.repository._sql import Row, fetch_all, fetch_one
from app.worker.db import holds_ownership
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

ACTION = {
    "activate": "add",
    "suspend": "modify",
    "restore": "modify",
    "change_profile": "modify",
    "deactivate": "delete",
}
STATE = {
    "activate": "active",
    "suspend": "suspended",
    "restore": "active",
    "deactivate": "inactive",
}


def line_request(conn: psycopg.Connection, cmd: Row) -> dict[str, Any]:
    """The request as the network gets it: line identifiers, the profile, TMF641-shaped."""
    ids: dict[str, dict[str, Any]] = {}
    if cmd["product_id"] is not None:
        rows = fetch_all(
            conn,
            "SELECT n.value, n.iccid, n.resource_specification_id AS kind"
            " FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " WHERE a.product_id = %s AND a.cancelled_at IS NULL ORDER BY a.effective_from DESC",
            (cmd["product_id"],),
        )
        for row in rows:
            ids.setdefault(row["kind"], row)
    msisdn, imsi = ids.get("msisdn"), ids.get("imsi")
    characteristics = [{"name": k, "value": v} for k, v in sorted(cmd["profile"].items())]
    return {
        "externalId": str(cmd["id"]),
        "command": cmd["kind"],
        "msisdn": None if msisdn is None else msisdn["value"],
        "imsi": None if imsi is None else imsi["value"],
        "iccid": None if imsi is None else imsi["iccid"],
        "profile": cmd["profile"],
        "serviceOrderItem": {
            "action": ACTION[cmd["kind"]],
            "service": {
                "state": STATE.get(cmd["kind"]),
                "serviceCharacteristic": characteristics,
            },
        },
    }


def process_network(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> list[UUID]:
    """Send every due command once. Returns the ids attempted."""
    ensure_ownership(conn)
    now = clock.now()
    attempted = []
    for due in network.due(conn, now.astimezone(tz).date(), now):
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            cmd = network.get(conn, due["id"], lock=True)
            if cmd["state"] != "pending":
                continue
            request = cmd["request"]
            if request is None:
                request = line_request(conn, cmd)
                network.freeze_request(conn, cmd["id"], request)
            try:
                adapter().send(str(cmd["id"]), cmd["kind"], request)
            except NetworkRefused as refused:
                network.refuse(conn, cmd["id"], code=refused.code, reason=refused.reason, now=now)
            except NetworkTemporary as error:
                network.temporary(
                    conn,
                    cmd["id"],
                    error=str(error) or "temporary network error",
                    max_attempts=settings.network_max_attempts,
                    now=now,
                )
            except Exception as error:  # noqa: BLE001 - unknown outcome: send again later
                log.exception("network command %s", cmd["id"])
                network.temporary(
                    conn,
                    cmd["id"],
                    error=str(error) or type(error).__name__,
                    max_attempts=settings.network_max_attempts,
                    now=now,
                )
            else:
                network.confirm(conn, cmd["id"], now=now)
        attempted.append(cmd["id"])
        if heartbeat is not None:
            heartbeat()
    return attempted


def gate(conn: psycopg.Connection, cmd: Row) -> tuple[str, Row | None]:
    """`go` (no network command, or all confirmed), `wait`, or `refused` with the command."""
    order_id = (cmd["request"] or {}).get("order_id")
    if cmd["kind"] != "order" or order_id is None:
        return "go", None
    rows = [r for r in network.of_order(conn, UUID(order_id)) if r["state"] != "cancelled"]
    refused = next((r for r in rows if r["state"] == "refused"), None)
    if refused is not None:
        return "refused", refused
    if all(r["state"] == "confirmed" for r in rows):
        return "go", None
    return "wait", None


def waiting_for_profile(conn: psycopg.Connection, product_id: UUID) -> bool:
    """A change of offering whose change_profile is unanswered, or refused and not yet
    withdrawn by the order's command: the renewal must not apply it before then."""
    return (
        fetch_one(
            conn,
            "SELECT 1 FROM network_command n WHERE n.product_id = %s AND n.kind = 'change_profile'"
            " AND n.state IN ('pending', 'exhausted', 'refused') AND EXISTS ("
            "SELECT FROM prepaid_command c WHERE c.id = n.product_order_id"
            " AND c.state <> 'completed') LIMIT 1",
            (product_id,),
        )
        is not None
    )


def post_profile_change(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """The network confirmed change_profile of an offering change at a renewal: the
    items and the order complete (the change itself is applied by the funded renewal)."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        orders.lock_order(conn, UUID(cmd["request"]["order_id"]))
        order = orders.get_order(conn, UUID(cmd["request"]["order_id"]))
        if order is None or order["state"] != "inProgress":
            raise ValueError("profile command must match its pending order")
        for item in order["items"]:
            orders.set_order_item_state(
                conn, item["id"], item_transition(item["state"], "completed")
            )
            emit.product(conn, item["product_id"], "AttributeValueChange", at=now, today=today)
        orders.set_order_state(
            conn, order["id"], order_transition(order["state"], "completed"), completion_date=now
        )
        _order_event(conn, order["id"], now)
        return commands.complete(conn, command_id, {"outcome": "profileChanged"}, now=now)["result"]


def refuse_order(
    conn: psycopg.Connection, cmd: Row, refused: Row, *, now: datetime, today: date
) -> dict:
    """The network refused: undo what acceptance did, fail the items and the order.

    The caller holds the command identity and the account lock. Nothing was posted yet
    (posting waits for the network), so only acceptance is undone: the hold is released,
    an add's product ends at its start date (zero billable days) and its number claim is
    cancelled, a termination, status or offering change is withdrawn.
    """
    request = cmd["request"]
    order_id = UUID(request["order_id"])
    orders.lock_order(conn, order_id)
    order = orders.get_order(conn, order_id)
    items = orders.items_of(conn, order_id)
    locked = products.lock_products(conn, [i["product_id"] for i in items if i["product_id"]])
    numbers = number_orders.lock(conn, list(locked))
    old_numbers = number_orders.before(conn, numbers, today)
    if "terminations" in request:
        number_orders.cancel(conn, items, now)
        for item in items:
            started = locked[item["product_id"]]["start_date"] <= today
            status = (
                products.status_after_changes(conn, item["product_id"]) if started else "created"
            )
            products.restore(conn, item["product_id"], status)
            emit.product(conn, item["product_id"], "StateChange", at=now, today=today)
    elif "status_changes" in request:
        for item in items:
            products.undo_status_change(conn, item["id"])
    elif "profile_changes" in request:
        for item in items:
            products.undo_change(conn, item["id"])
    else:  # a purchase, or a mid-period offering change holding money
        _release_hold(conn, cmd, now)
        number_orders.cancel(conn, items, now)
        for item in items:
            if "offering_changes" in request:
                products.undo_change(conn, item["id"])
                emit.product(conn, item["product_id"], "AttributeValueChange", at=now, today=today)
            else:
                products.cancel_unstarted(conn, item["product_id"])
                emit.product(conn, item["product_id"], "StateChange", at=now, today=today)
    for item in items:
        if item["id"] == refused["product_order_item_id"]:
            orders.add_error_message(
                conn,
                order_id=order_id,
                order_item_id=item["id"],
                code="networkRefused",
                reason=refused["last_error"] or "the network refused the command",
                message=refused["refusal_code"],
                occurred_at=now,
            )
        orders.set_order_item_state(conn, item["id"], item_transition(item["state"], "failed"))
    orders.set_order_state(
        conn, order_id, order_transition(order["state"], "failed"), completion_date=now
    )
    number_orders.changed(conn, numbers, old_numbers, now, today)
    _order_event(conn, order_id, now)
    network.cancel_open(conn, order_id)
    return commands.complete(
        conn, cmd["id"], {"outcome": "failed", "reason": "networkRefused"}, now=now
    )["result"]


def _release_hold(conn, cmd, now) -> None:
    hold = fetch_one(
        conn, "SELECT hold_id FROM funding_command_hold WHERE command_id = %s", (cmd["id"],)
    )
    if hold:
        funding.release(
            conn, cmd["billing_account_id"], hold["hold_id"], released_at=now, command_id=cmd["id"]
        )


def _order_event(conn, order_id, now) -> None:
    from app.api.ordering import order_body

    emit.emit(
        conn,
        "TMF622",
        "productOrder",
        "StateChange",
        lambda: order_body(conn, orders.get_order(conn, order_id)),
        at=now,
    )
