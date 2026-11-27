"""Which prepaid order items send a network command, and recording them at acceptance.

A network line is an offering whose specification declares network profile
characteristics (app/network/profile.py). Items on other offerings send nothing. The
command is created in the acceptance transaction; the billing worker sends it and the
order's own command waits for the confirmation (app/worker/network.py).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.network import profile
from app.ordering import numbers
from app.repository import catalog as catalog_repo
from app.repository import network_commands, orders, products, sims


@dataclass(frozen=True)
class Planned:
    item_id: str
    kind: str
    profile: dict[str, Any]
    product_id: UUID | None
    sim_id: UUID | None = None  # swap_sim: the SIM the line moves to


def offering_profile(conn, offering_id: UUID | None) -> dict[str, Any] | None:
    """The effective profile of a network line's offering; None for any other (and for no
    offering: the lookup of None finds none)."""
    offering = catalog_repo.get_product_offering(conn, offering_id)
    if offering is None or offering["product_specification_id"] is None:
        return None
    spec = catalog_repo.get_product_specification(conn, offering["product_specification_id"])
    if spec is None or not profile.is_line(spec["details"]):
        return None
    return profile.effective(spec["details"], offering["details"])


def with_add_ons(conn, plan_id: UUID, day: date) -> dict[str, Any]:
    """The plan line's profile on `day`: its offering's, combined with each of its add-ons
    live that day (started, not ended, not cancelled), oldest first."""
    base = offering_profile(conn, products.offering_on(conn, plan_id, day)) or {}
    for row in products.live_add_ons(conn, plan_id, day):
        extra = offering_profile(conn, row["product_offering_id"])
        if extra is not None:
            base = profile.combine(base, extra)
    return base


def _line_profile(conn, item):
    product = products.get_product(conn, item.product_id)
    return product, offering_profile(conn, product["product_offering_id"])


def plan(conn, request, start) -> list[Planned]:
    """The commands the order's items need: add -> activate, delete -> deactivate,
    offering change -> change_profile (the new offering's profile), status ->
    suspend / restore, an IMSI change -> swap_sim, a new E911 address on a Wi-Fi calling
    line -> update_e911. A number-only modify sends none."""
    planned = []
    for item in request.items:
        if item.e911.get("send"):  # a new E911 address alone on a Wi-Fi calling line
            planned.append(
                Planned(
                    item.item_id,
                    "update_e911",
                    with_add_ons(conn, item.product_id, start),
                    item.product_id,
                )
            )
            continue
        if (
            item.action == "modify"
            and item.product_offering_id is None
            and (item.product_status is None and numbers.references(item) is not None)
        ):
            product, line = _line_profile(conn, item)
            moved = numbers.changes(conn, item, product, start)
            if line is not None and set(moved) == {"imsi"}:
                planned.append(
                    Planned(item.item_id, "swap_sim", line, item.product_id, moved["imsi"])
                )
            continue
        parent = add_on_parent(item)
        if item.action == "add" and parent is not None:
            # an add-on pack: the plan line gets the plan's profile, the add-ons already live
            # and the pack's allowance (#200: a second pack keeps the first)
            extra = offering_profile(conn, item.product_offering_id)
            if extra is not None:
                planned.append(
                    Planned(
                        item.item_id,
                        "change_profile",
                        profile.combine(with_add_ons(conn, parent, start), extra),
                        parent,
                    )
                )
            continue
        if item.action == "add":
            kind, offering = "activate", item.product_offering_id
        elif item.action == "delete":
            kind = "deactivate"
            offering = products.get_product(conn, item.product_id)["product_offering_id"]
        elif item.action == "modify" and item.product_offering_id is not None:
            kind, offering = "change_profile", item.product_offering_id
        elif item.action == "modify" and item.product_status is not None:
            kind = "suspend" if item.product_status == "suspended" else "restore"
            offering = products.get_product(conn, item.product_id)["product_offering_id"]
        else:
            continue
        found = offering_profile(conn, offering)
        if found is not None:
            planned.append(Planned(item.item_id, kind, found, item.product_id))
    return planned


def add_on_parent(item) -> UUID | None:
    """The plan product an add item names as `productRelationship` of type addOn."""
    relations = ((item.details or {}).get("product") or {}).get("productRelationship") or []
    for relation in relations:
        if relation.get("relationshipType") == "addOn":
            found = relation.get("id")
            try:
                return None if found is None else UUID(str(found))
            except ValueError:
                return None
    return None


def record(
    conn,
    order_id: UUID,
    planned: list[Planned],
    start: date,
    now: datetime,
    today: date | None = None,
) -> None:
    """Insert the planned commands, due on the order's date (an add's product exists now);
    an E911 address change is due today."""
    if not planned:
        return
    rows = {row["item_id"]: row for row in orders.items_of(conn, order_id)}
    for entry in planned:
        row = rows[entry.item_id]
        if entry.kind == "activate" and row["product_id"] is not None:
            sims.reserve_claimed(conn, row["product_id"], now)
        network_commands.insert(
            conn,
            order_id=order_id,
            order_item_id=row["id"],
            product_id=entry.product_id or row["product_id"],
            kind=entry.kind,
            due_date=today if entry.kind == "update_e911" and today else start,
            profile=entry.profile,
            now=now,
        )
