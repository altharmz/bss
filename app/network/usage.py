"""Usage from the network: storing a record once, matching it to a line, and following
the plan allowance (docs/decisions/usage.md).

Pure rules and the one `ingest` path the worker's feed and TMF635 POST share. The BSS
rates nothing here and enforces nothing: the network caps and throttles, the BSS shows
usage against what the network was told and notes the 80% and 100% marks.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.ordering import network as network_orders
from app.repository import products
from app.repository import usage as repo
from app.repository._sql import Row

MIB = 1024 * 1024
THRESHOLDS = (80, 100)
UNITS = {"data": "bytes", "voice": "seconds", "sms": "messages"}


@dataclass(frozen=True)
class Incoming:
    network_record_id: str
    kind: str
    quantity: int
    event_at: datetime
    msisdn: str | None = None
    imsi: str | None = None
    roaming: bool = False
    throttled: bool = False
    product_id: UUID | None = None  # named by the caller (TMF635 POST): no matching


def included(profile: dict[str, Any] | None) -> dict[str, int]:
    """The allowance of a profile per kind in the record unit (0: nothing included)."""
    profile = profile or {}
    return {
        "data": int(profile.get("dataAllowanceMb") or 0) * MIB,
        "voice": int(profile.get("voiceMinutes") or 0) * 60,
        "sms": int(profile.get("smsCount") or 0),
    }


def line_profile(conn: psycopg.Connection, product_id: UUID, day: date) -> dict[str, Any] | None:
    """What the network was last told for the line, else the offering's effective profile
    (a product that never got a command)."""
    told = repo.told_profile(conn, product_id)
    if told is not None:
        return told
    return network_orders.offering_profile(conn, products.offering_on(conn, product_id, day))


def ingest(
    conn: psycopg.Connection, incoming: Incoming, *, now: datetime, tz: ZoneInfo
) -> tuple[Row, bool]:
    """Store the record once. Returns (row, created); a known network id returns the stored
    row untouched. A matched record is checked against the plan allowance.

    The unique key is (network_record_id, event_date), so a resend with another date is
    found first by the id alone and treated as a replay. Two concurrent first inserts of one
    id with different dates can both be stored: an accepted race (usage.md, "Partitions")."""
    stored = repo.by_network_id(conn, incoming.network_record_id)
    if stored is not None:
        return stored, False
    day = incoming.event_at.astimezone(tz).date()
    if incoming.product_id is not None:
        product = repo.product_row(conn, incoming.product_id)
    else:
        product = repo.product_on(conn, incoming.msisdn, incoming.imsi, day)
    row = repo.insert(
        conn,
        network_record_id=incoming.network_record_id,
        kind=incoming.kind,
        quantity=incoming.quantity,
        roaming=incoming.roaming,
        throttled=incoming.throttled,
        msisdn=incoming.msisdn,
        imsi=incoming.imsi,
        event_at=incoming.event_at,
        event_date=day,
        product=product,
        now=now,
    )
    if row is None:
        stored = repo.by_network_id(conn, incoming.network_record_id)
        assert stored is not None
        return stored, False
    if product is not None:
        check_thresholds(conn, row, now=now)
    return row, True


def check_thresholds(conn: psycopg.Connection, record: Row, *, now: datetime) -> list[int]:
    """Note the 80% and 100% marks the record's kind has now reached in its plan period.
    Each is stored once per product, period and kind; the thresholds reached now are
    returned."""
    period = repo.period_of(conn, record["product_id"], record["event_date"])
    if period is None:
        return []
    allowance = included(line_profile(conn, record["product_id"], record["event_date"]))
    kind = record["kind"]
    limit = allowance[kind]
    if limit <= 0:
        return []
    total = repo.used(conn, record["product_id"], period["period_start"], period["period_end"])
    reached = []
    for pct in THRESHOLDS:
        if total[kind] * 100 >= limit * pct and repo.insert_threshold(
            conn,
            product_id=record["product_id"],
            period_start=period["period_start"],
            period_end=period["period_end"],
            allowance=kind,
            threshold=pct,
            record_id=record["id"],
            used_amount=total[kind],
            included=limit,
            now=now,
        ):
            reached.append(pct)
    return reached


def summary(conn: psycopg.Connection, product_id: UUID, day: date) -> dict[str, Any]:
    """Period totals, allowance and thresholds of a line on `day`. Without a plan period
    (not prepaid, or no grant covers the day) there is no period and no allowance."""
    period = repo.period_of(conn, product_id, day)
    if period is None:
        return {"period": None, "allowances": [], "throttled": False}
    start, end = period["period_start"], period["period_end"]
    profile = line_profile(conn, product_id, day) or {}
    limits = included(profile)
    total = repo.used(conn, product_id, start, end)
    marks: dict[str, list[int]] = {}
    for row in repo.thresholds(conn, product_id, start):
        marks.setdefault(row["allowance"], []).append(row["threshold"])
    allowances = []
    for kind, unit in UNITS.items():
        limit, total_used = limits[kind], total[kind]
        allowances.append(
            {
                "kind": kind,
                "unit": unit,
                "used": total_used,
                "included": limit,
                "remaining": max(limit - total_used, 0),
                "percent": None if limit <= 0 else total_used * 100 // limit,
                "thresholds": marks.get(kind, []),
            }
        )
    cap = int(profile.get("dataCapMb") or 0) * MIB
    reported = repo.latest_throttled(conn, product_id, start, end)
    return {
        "period": {"startDate": start, "endDate": end},
        "allowances": allowances,
        "throttled": bool(reported) or (cap > 0 and total["data"] >= cap),
    }
