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
from app.repository import catalog as catalog_repo
from app.repository import network_commands, orders, products


@dataclass(frozen=True)
class Planned:
    item_id: str
    kind: str
    profile: dict[str, Any]
    product_id: UUID | None


def offering_profile(conn, offering_id: UUID | None) -> dict[str, Any] | None:
    """The effective profile of a network line's offering; None for any other."""
    if offering_id is None:
        return None
    offering = catalog_repo.get_product_offering(conn, offering_id)
    if offering is None or offering["product_specification_id"] is None:
        return None
    spec = catalog_repo.get_product_specification(conn, offering["product_specification_id"])
    if spec is None or not profile.is_line(spec["details"]):
        return None
    return profile.effective(spec["details"], offering["details"])


def plan(conn, request) -> list[Planned]:
    """The commands the order's items need: add -> activate, delete -> deactivate,
    offering change -> change_profile (the new offering's profile), status ->
    suspend / restore. A number-only modify sends none."""
    planned = []
    for item in request.items:
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


def record(conn, order_id: UUID, planned: list[Planned], start: date, now: datetime) -> None:
    """Insert the planned commands, due on the order's date (an add's product exists now)."""
    if not planned:
        return
    rows = {row["item_id"]: row for row in orders.items_of(conn, order_id)}
    for entry in planned:
        row = rows[entry.item_id]
        network_commands.insert(
            conn,
            order_id=order_id,
            order_item_id=row["id"],
            product_id=row["product_id"],
            kind=entry.kind,
            due_date=start,
            profile=entry.profile,
            now=now,
        )
