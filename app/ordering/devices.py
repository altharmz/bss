"""The device of a prepaid line at activation and on a device change
(docs/decisions/network.md#device-issue-194).

An add item may carry the `imei` characteristic; a modify item whose only content is the
`imei` characteristic changes the device (it completes at acceptance, sends no network
command, and ignores the requested start date). The network is asked while the order is
validated (`check_device` is a read-only question): `incompatible` rejects the order with
the reasons; `compatible` and `unknown` (also when the network does not answer) are
recorded on the product, `unknown` as a warning.
"""

from datetime import datetime
from uuid import UUID

from app.integrations.network import DeviceCheck, NetworkTemporary, adapter
from app.network import device
from app.ordering import network as network_orders
from app.ordering import numbers
from app.repository import devices as repo


def device_only(item) -> bool:
    """A modify item that carries an IMEI and nothing else to change."""
    return (
        item.action == "modify"
        and item.product_offering_id is None
        and item.product_status is None
        and numbers.references(item) is None
        and device.requested(item.details) is not None
    )


def problems(conn, request, account, locked, now: datetime) -> list[tuple[str, str, str]]:
    """(item id, code, reason) for the items that carry an IMEI; fills `device_check` of
    the items that pass."""
    found = []
    for item in request.items:
        imei = device.requested(item.details)
        if imei is None:
            continue
        item_id = item.item_id
        if (why := device.problem(imei)) is not None:
            found.append((item_id, "invalidImei", why))
            continue
        offering = item.product_offering_id
        if item.action == "modify":
            offering = locked[item.product_id]["product_offering_id"]
        if account["rating_type"] != "prepaid" or (
            network_orders.offering_profile(conn, offering) is None
        ):
            found.append(
                (
                    item_id,
                    "deviceNotApplicable",
                    "a device is recorded on prepaid lines of a network offering only",
                )
            )
            continue
        if item.action == "modify" and not device_only(item):
            found.append(
                (
                    item_id,
                    "deviceChangeAlone",
                    "change the device in an item of its own, changing nothing else",
                )
            )
            continue
        verdict = asked(imei)
        if verdict.result == "incompatible":
            found.append(
                (
                    item_id,
                    "deviceIncompatible",
                    "the device does not work on the network: " + "; ".join(verdict.reasons),
                )
            )
            continue
        item.device_check.update(
            result=verdict.result, reasons=list(verdict.reasons), checked_at=now
        )
        _announce(item, verdict)
    return found


def asked(imei: str) -> DeviceCheck:
    """The network's verdict; a network that cannot answer is `unknown`."""
    try:
        return adapter().check_device(imei)
    except NetworkTemporary:
        return DeviceCheck("unknown", ("the network could not check the device",))


def _announce(item, verdict: DeviceCheck) -> None:
    """Echo the verdict on the order item, next to the IMEI that was sent."""
    entries = item.details["product"]["productCharacteristic"]
    entries.append(
        {"name": device.COMPATIBILITY, "value": verdict.result, "@type": "StringCharacteristic"}
    )
    if verdict.reasons:
        entries.append(
            {
                "name": device.REASONS,
                "value": "; ".join(verdict.reasons),
                "@type": "StringCharacteristic",
            }
        )


def execute(conn, item, product_id: UUID, item_id: UUID, now: datetime) -> None:
    """Record the device of an item that passed `problems`: closes the line's current one."""
    imei = device.requested(item.details)
    if imei is None or not item.device_check:
        return
    check = item.device_check
    repo.replace(
        conn,
        product_id=product_id,
        imei=imei,
        compatibility=check["result"],
        reasons=check["reasons"],
        checked_at=check["checked_at"],
        item_id=item_id,
        now=now,
    )
