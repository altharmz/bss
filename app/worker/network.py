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

from app.billing import data_pool
from app.clock import Clock
from app.config import settings
from app.events import emit
from app.integrations.network import NetworkRefused, NetworkTemporary, adapter
from app.network import e911
from app.network import usage as usage_rules
from app.ordering import numbers as number_orders
from app.ordering.process import ItemRequest
from app.ordering.states import item_transition, order_transition
from app.repository import data_pool as pool_repo
from app.repository import funding, orders, products, sims
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
    "swap_sim": "modify",
    "update_e911": "modify",
    "deactivate": "delete",
}
STATE = {
    "activate": "active",
    "suspend": "suspended",
    "restore": "active",
    "swap_sim": "active",
    "deactivate": "inactive",
}


def line_request(
    conn: psycopg.Connection, cmd: Row, data_pool: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The request as the network gets it: line identifiers, the profile, TMF641-shaped;
    a pool member's activate or change_profile carries the pool (`dataPoolId`,
    `dataPoolMb`)."""
    ids: dict[str, dict[str, Any]] = {}
    if cmd["product_id"] is not None:
        rows = fetch_all(
            conn,
            "SELECT n.value, n.iccid, n.sim_type, n.origin, n.resource_specification_id AS kind"
            " FROM number_assignment a JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            " WHERE a.product_id = %s AND a.cancelled_at IS NULL ORDER BY a.effective_from DESC",
            (cmd["product_id"],),
        )
        for row in rows:
            ids.setdefault(row["kind"], row)
    msisdn, imsi = ids.get("msisdn"), ids.get("imsi")
    characteristics = [{"name": k, "value": v} for k, v in sorted(cmd["profile"].items())]
    address = e911_address(conn, cmd)
    if address is not None:
        characteristics.append({"name": "e911Address", "value": address})
    if data_pool is not None:
        characteristics += [
            {"name": "dataPoolId", "value": data_pool["id"]},
            {"name": "dataPoolMb", "value": data_pool["mb"]},
        ]
    new = None
    if cmd["kind"] == "swap_sim":
        new = sims.reserved_of(conn, cmd["product_id"])
    request = {
        "externalId": str(cmd["id"]),
        "command": cmd["kind"],
        "msisdn": None if msisdn is None else msisdn["value"],
        "imsi": None if imsi is None else imsi["value"],
        "iccid": None if imsi is None else imsi["iccid"],
        "simType": None if imsi is None else imsi["sim_type"],
        "portedIn": msisdn is not None and msisdn["origin"] == "port_in",
        "profile": cmd["profile"],
        "serviceOrderItem": {
            "action": ACTION[cmd["kind"]],
            "service": {
                "state": STATE.get(cmd["kind"]),
                "serviceCharacteristic": characteristics,
            },
        },
    }
    if address is not None:
        request["e911Address"] = address
    if data_pool is not None:
        request |= {"dataPoolId": data_pool["id"], "dataPoolMb": data_pool["mb"]}
    if new is not None:  # the SIM the line moves to
        request |= {
            "newImsi": new["value"],
            "newIccid": new["iccid"],
            "newSimType": new["sim_type"],
        }
    return request


def e911_address(conn: psycopg.Connection, cmd: Row) -> dict[str, Any] | None:
    """The E911 address the command carries: update_e911 the item's new one; activate and
    change_profile to a Wi-Fi calling profile the line's own (docs/decisions/network.md)."""
    if cmd["kind"] == "update_e911":
        item = fetch_one(
            conn,
            "SELECT details FROM product_order_item WHERE id = %s",
            (cmd["product_order_item_id"],),
        )
        return e911.requested(item["details"])
    if cmd["kind"] not in ("activate", "change_profile") or cmd["product_id"] is None:
        return None
    if not e911.wifi_calling(cmd["profile"]):
        return None
    product = products.get_product(conn, cmd["product_id"])
    return None if product is None else product["e911_address"]


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
    end_passes(conn, now.astimezone(tz).date(), now)
    sync_pools(conn, now.astimezone(tz).date(), now)
    for due in network.due(conn, now.astimezone(tz).date(), now):
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            cmd = network.get(conn, due["id"], lock=True)
            if cmd["state"] != "pending":
                continue
            request = cmd["request"]
            if request is None:
                pool = cmd["data_pool"] or member_pool(conn, cmd)
                request = line_request(conn, cmd, pool)
                network.freeze_request(conn, cmd["id"], request, pool)
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
                if cmd["kind"] == "activate" and cmd["product_id"] is not None:
                    sims.activate_reserved(conn, cmd["product_id"], now)
                if cmd["kind"] == "update_e911":  # the line takes what the network has
                    products.set_e911_address(conn, cmd["product_id"], request["e911Address"])
        attempted.append(cmd["id"])
        if heartbeat is not None:
            heartbeat()
    return attempted


def end_passes(conn: psycopg.Connection, today: date, now: datetime) -> list[UUID]:
    """A pass (an add-on with passDays) that ended before its plan period gets one
    change_profile on its end date: the plan's profile with the add-ons still live then. The
    only revert command; an add-on ending with the period needs none (docs/decisions/prepaid.md,
    #200). Due today, sent by the same wake-up."""
    from app.ordering.network import offering_profile, with_add_ons

    made = []
    for row in network.ended_passes(conn, today):
        with conn.transaction():
            plan_offering = products.offering_on(
                conn, row["parent_product_id"], row["termination_date"]
            )
            if offering_profile(conn, plan_offering) is None:
                continue  # not a network line
            cmd = network.insert(
                conn,
                order_id=row["product_order_id"],
                order_item_id=row["created_by_item_id"],
                product_id=row["parent_product_id"],
                kind="change_profile",
                due_date=row["termination_date"],
                profile=with_add_ons(conn, row["parent_product_id"], row["termination_date"]),
                now=now,
                purpose="passEnd",
            )
            made.append(cmd["id"])
    return made


def pool_fields(pool: data_pool.Pool, day: date) -> dict[str, Any]:
    return {"id": str(pool.account_id), "mb": pool.size_mb(day), "periodStart": str(pool.start)}


def member_pool(conn: psycopg.Connection, cmd: Row) -> dict[str, Any] | None:
    """The pool an activate or change_profile carries: its line's account pool on the due
    date, when the line is a member (docs/decisions/network.md, "Data pool commands")."""
    if cmd["kind"] not in ("activate", "change_profile") or cmd["product_id"] is None:
        return None
    product = products.get_product(conn, cmd["product_id"])
    if product is None:
        return None
    pool = data_pool.pool_on(
        conn, product["billing_account_id"], cmd["due_date"], usage_rules.profiles(conn)
    )
    if pool is None or cmd["product_id"] not in pool.members:
        return None
    return pool_fields(pool, cmd["due_date"])


def sync_pools(conn: psycopg.Connection, today: date, now: datetime) -> list[UUID]:
    """Tell every live pool member the pool size when it changed: a line joined (or an
    add-on added data), or a new pool period started. One change_profile per member with
    its current profile, due today and sent by the same wake-up; nothing for a member whose
    own activate or change_profile is due and unsent (it carries the size when sent), or
    that was told this size in this period already. A line leaving keeps its share until the
    period ends, so it changes nothing."""
    from app.ordering.network import with_add_ons

    profile_of = usage_rules.profiles(conn)
    accounts = pool_repo.accounts_with_lines(conn, data_pool.pooled_offerings(conn), today)
    if not accounts:
        return []
    keys, pools = data_pool.pools(conn, [(account, today) for account in accounts], profile_of)
    made = []
    for account in accounts:
        pool = pools.get(keys.get((account, today)))
        if pool is None:
            continue
        wanted = pool_fields(pool, today)
        live = pool.live(today)
        told = network.pool_told(conn, live, today)
        on_network = network.activated(conn, live)
        for product_id in live:
            last = told.get(product_id)
            if last is None and product_id not in on_network:
                continue  # not on the network yet: its activate carries the pool
            known = None if last is None else last["data_pool"]
            if last is not None and known is None:
                continue  # its own command is due and unsent: it carries the pool
            if known is not None and (known["periodStart"], known["mb"]) == (
                wanted["periodStart"],
                wanted["mb"],
            ):
                continue
            with conn.transaction():
                cmd = network.insert_pool(
                    conn,
                    product_id=product_id,
                    due_date=today,
                    profile=with_add_ons(conn, product_id, today),
                    data_pool=wanted,
                    now=now,
                )
                if cmd is not None:
                    network.cancel_superseded_pool(conn, product_id, cmd["id"])
                    made.append(cmd["id"])
    return made


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


def post_sim_swap(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """The network confirmed swap_sim: the claims move to the new SIM on the ordered date,
    the old SIM is retired, the new one is active and the items and the order complete."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        order_id = UUID(cmd["request"]["order_id"])
        orders.lock_order(conn, order_id)
        order = orders.get_order(conn, order_id)
        if order is None or order["state"] != "inProgress":
            raise ValueError("sim swap command must match its pending order")
        swaps = cmd["request"]["sim_swaps"]
        product_ids = [UUID(s["product_id"]) for s in swaps]
        products.lock_products(conn, product_ids)
        refs = [[{"id": s["subscriber_number_id"]}] for s in swaps]
        locked = number_orders.lock(conn, product_ids, refs)
        old = number_orders.before(conn, locked, today)
        for swap in swaps:
            item = ItemRequest(
                item_id=swap["item_id"],
                action="modify",
                product_id=UUID(swap["product_id"]),
                details={"product": {"realizingResource": [{"id": swap["subscriber_number_id"]}]}},
            )
            number_orders.execute(
                conn, item, item.product_id, UUID(swap["item_id"]), cmd["due_date"], now
            )
        number_orders.changed(conn, locked, old, now, today)
        for row in order["items"]:
            orders.set_order_item_state(conn, row["id"], item_transition(row["state"], "completed"))
            emit.product(conn, row["product_id"], "AttributeValueChange", at=now, today=today)
        orders.set_order_state(
            conn, order["id"], order_transition(order["state"], "completed"), completion_date=now
        )
        _order_event(conn, order["id"], now)
        return commands.complete(conn, command_id, {"outcome": "simSwapped"}, now=now)["result"]


def post_e911_change(
    conn: psycopg.Connection, command_id: UUID, *, now: datetime, tz: ZoneInfo
) -> dict:
    """The network confirmed every update_e911 of the order (each stored the new address
    when confirmed): the items and the order complete."""
    if not holds_ownership(conn):
        raise ValueError("prepaid posting requires billing ownership")
    today = now.astimezone(tz).date()
    with conn.transaction():
        cmd = commands.ready(conn, command_id, now=now, today=today)
        if cmd["state"] == "completed":
            return cmd["result"]
        order_id = UUID(cmd["request"]["order_id"])
        orders.lock_order(conn, order_id)
        order = orders.get_order(conn, order_id)
        if order is None or order["state"] != "inProgress":
            raise ValueError("E911 command must match its pending order")
        for row in order["items"]:
            orders.set_order_item_state(conn, row["id"], item_transition(row["state"], "completed"))
            emit.product(conn, row["product_id"], "AttributeValueChange", at=now, today=today)
        orders.set_order_state(
            conn, order["id"], order_transition(order["state"], "completed"), completion_date=now
        )
        _order_event(conn, order["id"], now)
        return commands.complete(conn, command_id, {"outcome": "e911Changed"}, now=now)["result"]


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
    elif "e911_changes" in request:
        pass  # the worker stores an address only when confirmed: the old one stays
    elif "sim_swaps" in request:  # nothing moved: the reserved SIMs go back in stock
        for swap in request["sim_swaps"]:
            sims.release_hold(conn, UUID(swap["subscriber_number_id"]), now)
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
