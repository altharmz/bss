"""The E911 address of prepaid lines in the order engine
(docs/decisions/network.md#e911-address).

An add item may carry `product.place` (role e911Address); a modify item whose only content is
that place changes the address. A line whose effective profile has `wifiCalling = 1` needs an
address: the one sent, else the line's own, else the billing account's service address (copied,
not linked), else the order is refused `e911AddressRequired`. A line without Wi-Fi calling keeps
an address sent for later (it is sent once a profile enables Wi-Fi calling).

A change on a Wi-Fi calling line is sent to the network (`update_e911`, gated like the other
commands) and the product takes it only when the network confirms; on another line it is stored
at acceptance. `problems` fills `ItemRequest.e911` of the items that pass: the address to store
(`address`), on which product (`product_id`, None: the item's new product) and whether the
network is sent a change (`send`).
"""

from datetime import date
from typing import Any
from uuid import UUID

from app.network import e911
from app.ordering import devices, numbers
from app.ordering import network as network_orders
from app.repository import products

REQUIRED = (
    "a Wi-Fi calling line needs an E911 address: send product.place (role e911Address) or set"
    " the billing account's service address"
)


def address_only(item) -> bool:
    """A modify item that carries an E911 address and nothing else to change."""
    return (
        item.action == "modify"
        and item.product_offering_id is None
        and item.product_status is None
        and numbers.references(item) is None
        and not devices.device_only(item)
        and e911.requested(item.details) is not None
    )


def problems(conn, request, account, locked, start: date) -> list[tuple[str, str, str]]:
    """(item id, code, reason) for the items whose line needs or carries an address."""
    found = []
    prepaid = account["rating_type"] == "prepaid"
    service = account["service_address"]
    default = None if service is None else e911.from_service_address(service)
    for item in request.items:
        given = e911.requested(item.details)
        if item.action == "add" and network_orders.add_on_parent(item) is not None:
            parent = network_orders.add_on_parent(item)
            if given is not None:
                found.append(
                    (item.item_id, "e911NotApplicable", "an add-on's line keeps its own address")
                )
                continue
            extra = network_orders.offering_profile(conn, item.product_offering_id)
            if not prepaid or extra is None or not e911.wifi_calling(extra):
                continue
            current = products.get_product(conn, parent)
            if current is None or current["e911_address"] is not None:
                continue
            if default is None:
                found.append((item.item_id, "e911AddressRequired", REQUIRED))
            else:
                item.e911.update(address=default, product_id=parent, send=False)
            continue
        if item.action == "add":
            line = network_orders.offering_profile(conn, item.product_offering_id)
            current_address = None
            product_id = None
        elif item.action == "modify" and item.product_id in locked:
            product = locked[item.product_id]
            product_id = product["id"]
            current_address = product["e911_address"]
            if item.product_offering_id is not None:
                line = network_orders.offering_profile(conn, item.product_offering_id)
            elif given is not None:
                line = network_orders.offering_profile(conn, product["product_offering_id"])
                if line is not None:
                    line = network_orders.with_add_ons(conn, product_id, start)
            else:
                continue
        else:
            continue
        if given is not None and (not prepaid or line is None):
            found.append(
                (
                    item.item_id,
                    "e911NotApplicable",
                    "an E911 address is recorded on prepaid lines of a network offering only",
                )
            )
            continue
        if (
            given is not None and item.action == "modify" and not address_only(item)
        ):  # pragma: no cover - the API refuses it (422) first
            found.append(
                (
                    item.item_id,
                    "e911ChangeAlone",
                    "change the E911 address in an item of its own, changing nothing else",
                )
            )
            continue
        wifi = prepaid and e911.wifi_calling(line)
        if given is not None:
            send = item.action == "modify" and wifi
            item.e911.update(address=given, product_id=product_id, send=send)
        elif wifi and current_address is None:
            if default is None:
                found.append((item.item_id, "e911AddressRequired", REQUIRED))
            else:
                item.e911.update(address=default, product_id=product_id, send=False)
    return found


def execute(conn, item, product_id: UUID) -> None:
    """Store the address an item that passed `problems` takes at acceptance (one the network
    must confirm first is stored by the worker)."""
    chosen: dict[str, Any] = item.e911
    if not chosen or chosen["send"]:
        return
    products.set_e911_address(conn, chosen["product_id"] or product_id, chosen["address"])


def sent(request) -> list:
    """The items whose address change waits for the network (update_e911)."""
    return [item for item in request.items if item.e911.get("send")]
