"""Usage from the network: storing a record once, matching it to a line, and following
the plan allowance (docs/decisions/usage.md).

Pure rules and the one `ingest` path the worker's feed and TMF635 POST share. The BSS
rates nothing here and enforces nothing: the network caps and throttles, the BSS shows
usage against what the network was told and notes the 80% and 100% marks.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4
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
    international: bool = False  # a voice or SMS record to a non-US destination (#200)


# the profile characteristic of each zone's allowance, and its size in the record unit
ALLOWANCES: dict[tuple[str, str], tuple[str, int]] = {
    ("domestic", "data"): ("dataAllowanceMb", MIB),
    ("domestic", "voice"): ("voiceMinutes", 60),
    ("domestic", "sms"): ("smsCount", 1),
    ("roaming", "data"): ("roamingDataMb", MIB),
    ("roaming", "voice"): ("roamingMinutes", 60),
    ("roaming", "sms"): ("roamingSmsCount", 1),
    ("international", "voice"): ("internationalMinutes", 60),
}


def included(profile: dict[str, Any] | None) -> dict[tuple[str, str], int]:
    """The allowance of a profile per (zone, kind) in the record unit (0: nothing included,
    no threshold). Roaming usage counts only against the roaming allowances, international
    calls only against internationalMinutes (international SMS has none)."""
    profile = profile or {}
    out = repo.empty_totals()
    for key, (name, unit) in ALLOWANCES.items():
        out[key] = int(profile.get(name) or 0) * unit
    return out


def zone(row: Row) -> str:
    return repo.zone_of(row["kind"], row["roaming"], row.get("international", False))


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
        international=incoming.international,
    )
    if row is None:
        stored = repo.by_network_id(conn, incoming.network_record_id)
        assert stored is not None
        return stored, False
    if product is not None:
        check_thresholds(conn, row, now=now)
    return row, True


def ingest_batch(
    conn: psycopg.Connection, batch: list[Incoming], *, now: datetime, tz: ZoneInfo
) -> int:
    """The feed's path (#281): store a batch as `ingest` would store its records one after
    another, with a few statements for the batch instead of several per record. Returns how
    many records were new. A network id seen before (stored, or earlier in the batch) is a
    replay; the matching, the plan periods, the allowances and the thresholds are those of
    `ingest`, a threshold being noted at the record that reached it."""
    first: dict[str, Incoming] = {}
    for incoming in batch:
        first.setdefault(incoming.network_record_id, incoming)
    known = repo.known_network_ids(conn, list(first))
    fresh = [incoming for key, incoming in first.items() if key not in known]
    if not fresh:
        return 0
    days = [incoming.event_at.astimezone(tz).date() for incoming in fresh]
    lines = repo.products_on(
        conn,
        [(incoming.msisdn, incoming.imsi, day) for incoming, day in zip(fresh, days, strict=True)],
    )
    stored = repo.insert_many(
        conn,
        [
            {
                "id": uuid4(),
                "network_record_id": incoming.network_record_id,
                "kind": incoming.kind,
                "quantity": incoming.quantity,
                "roaming": incoming.roaming,
                "throttled": incoming.throttled,
                "msisdn": incoming.msisdn,
                "imsi": incoming.imsi,
                "event_at": incoming.event_at,
                "event_date": day,
                "product_id": None if line is None else line["product_id"],
                "billing_account_id": None if line is None else line["billing_account_id"],
                "status": "rejected" if line is None else "notRated",
                "rejection_reason": "noLine" if line is None else None,
                "international": incoming.international,
            }
            for incoming, day, line in zip(fresh, days, lines, strict=True)
        ],
        now,
    )
    _check_thresholds_of(conn, [row for row in stored if row["product_id"] is not None], now=now)
    return len(stored)


def _check_thresholds_of(conn: psycopg.Connection, records: list[Row], *, now: datetime) -> None:
    """`check_thresholds` for the batch's stored records, in their order."""
    places = sorted({(row["product_id"], row["event_date"]) for row in records})
    if not places:
        return
    periods = repo.periods_of(conn, places)
    places = [place for place in places if place in periods]
    told = repo.told_profiles(conn, sorted({product_id for product_id, _ in places}))
    untold = [place for place in places if place[0] not in told]
    offerings = repo.offerings_on(conn, untold) if untold else {}
    by_offering = {
        offering_id: network_orders.offering_profile(conn, offering_id)
        for offering_id in set(offerings.values())
    }
    allowance = {
        place: included(told[place[0]] if place[0] in told else by_offering[offerings[place]])
        for place in places
    }
    # only the records whose kind has an allowance in their period can reach a threshold
    counted = []
    for row in records:
        place = (row["product_id"], row["event_date"])
        if place in periods and allowance[place][(zone(row), row["kind"])] > 0:
            period = periods[place]
            counted.append((row, (row["product_id"], period["period_start"], period["period_end"])))
    if not counted:
        return
    totals = repo.used_many(conn, sorted({key for _, key in counted}))
    # the totals hold the whole batch: walking it backwards gives each record the total
    # that `ingest` would have seen right after storing it
    reached = []
    for row, key in reversed(counted):
        pool = (zone(row), row["kind"])
        total = totals[key][pool]
        totals[key][pool] -= row["quantity"]
        limit = allowance[(row["product_id"], row["event_date"])][pool]
        reached.append((row, key, total, limit))
    for row, key, total, limit in reversed(reached):
        for pct in THRESHOLDS:
            if total * 100 >= limit * pct:
                repo.insert_threshold(
                    conn,
                    product_id=row["product_id"],
                    period_start=key[1],
                    period_end=key[2],
                    allowance=row["kind"],
                    threshold=pct,
                    record_id=row["id"],
                    used_amount=total,
                    included=limit,
                    now=now,
                    zone=zone(row),
                )


def check_thresholds(conn: psycopg.Connection, record: Row, *, now: datetime) -> list[int]:
    """Note the 80% and 100% marks the record's kind has now reached in its plan period.
    Each is stored once per product, period and kind; the thresholds reached now are
    returned."""
    period = repo.period_of(conn, record["product_id"], record["event_date"])
    if period is None:
        return []
    allowance = included(line_profile(conn, record["product_id"], record["event_date"]))
    kind = record["kind"]
    pool = (zone(record), kind)
    limit = allowance[pool]
    if limit <= 0:
        return []  # no allowance in the record's zone: stored and counted, no threshold
    total = repo.used(conn, record["product_id"], period["period_start"], period["period_end"])
    reached = []
    for pct in THRESHOLDS:
        if total[pool] * 100 >= limit * pct and repo.insert_threshold(
            conn,
            product_id=record["product_id"],
            period_start=period["period_start"],
            period_end=period["period_end"],
            allowance=kind,
            threshold=pct,
            record_id=record["id"],
            used_amount=total[pool],
            included=limit,
            now=now,
            zone=pool[0],
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
    marks: dict[tuple[str, str], list[int]] = {}
    for row in repo.thresholds(conn, product_id, start):
        marks.setdefault((row["zone"], row["allowance"]), []).append(row["threshold"])
    allowances = []
    for pool_zone in repo.ZONES:
        for kind, unit in UNITS.items():
            pool = (pool_zone, kind)
            limit, total_used = limits[pool], total[pool]
            if pool_zone != "domestic" and limit <= 0 and total_used <= 0:
                continue  # a roaming or international pool is listed when it has something
            allowances.append(
                {
                    "kind": kind,
                    "zone": pool_zone,
                    "unit": unit,
                    "used": total_used,
                    "included": limit,
                    "remaining": max(limit - total_used, 0),
                    "percent": None if limit <= 0 else total_used * 100 // limit,
                    "thresholds": marks.get(pool, []),
                }
            )
    cap = int(profile.get("dataCapMb") or 0) * MIB
    reported = repo.latest_throttled(conn, product_id, start, end)
    return {
        "period": {"startDate": start, "endDate": end},
        "allowances": allowances,
        "throttled": bool(reported) or (cap > 0 and total[("domestic", "data")] >= cap),
    }
