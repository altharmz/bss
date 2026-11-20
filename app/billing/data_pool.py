"""The account data pool (docs/decisions/usage.md, "Pooled data", #379).

The plan lines of one billing account whose offering's network profile has `dataPool` 1
share one domestic data allowance per pool period. There is no pool resource: the pool is
derived from the lines.

- **Pool period:** the billing month starting on the account's `bill_cycle_day`
  (`cycle_period`), on business dates.
- **Members:** the plan lines live on some day of the period whose offering on their first
  day in it is pooled. Membership and each line's share are fixed by that day, so an
  offering change counts from the next period.
- **Size on a day:** the `dataAllowanceMb` of every member, and of the add-ons of members,
  that started in the period on or before that day. A line or add-on that ends keeps its
  share until the period ends, so the size never falls inside a period.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from app.billing.core import cycle_period
from app.network import profile
from app.repository import data_pool as repo
from app.repository._sql import Row

MIB = 1024 * 1024


@dataclass(frozen=True)
class Pool:
    account_id: UUID
    start: date
    end: date
    # member plan line -> (first day in the period, termination date)
    members: dict[UUID, tuple[date, date | None]]
    # (first day in the period, MB) of every member and member add-on
    shares: tuple[tuple[date, int], ...]

    @property
    def key(self) -> repo.Key:
        return (self.account_id, self.start, self.end)

    def size_mb(self, day: date) -> int:
        return sum(mb for first, mb in self.shares if first <= day)

    def live(self, day: date) -> list[UUID]:
        """The members live on `day`."""
        return [
            product_id
            for product_id, (first, ended) in self.members.items()
            if first <= day and (ended is None or ended > day)
        ]


def pooled(profile: dict[str, Any] | None) -> bool:
    return bool((profile or {}).get("dataPool"))


def _mb(profile: dict[str, Any] | None) -> int:
    return int((profile or {}).get("dataAllowanceMb") or 0)


def build(
    key: repo.Key, rows: list[Row], profile_of: Callable[[UUID], dict[str, Any] | None]
) -> Pool | None:
    """The pool of an account period from its lines (`repo.lines`); None without members."""
    members = {
        row["product_id"]: (row["first_day"], row["termination_date"])
        for row in rows
        if not row["grantless"] and pooled(profile_of(row["offering_id"]))
    }
    if not members:
        return None
    shares = tuple(
        (row["first_day"], _mb(profile_of(row["offering_id"])))
        for row in rows
        if row["product_id"] in members or row["parent_product_id"] in members
    )
    return Pool(key[0], key[1], key[2], members, shares)


def period_key(account_id: UUID, cycle_day: int, day: date) -> repo.Key:
    period = cycle_period(day, cycle_day)
    return (account_id, period.start, period.end)


def pools(
    conn: psycopg.Connection,
    places: list[tuple[UUID, date]],
    profile_of: Callable[[UUID], dict[str, Any] | None],
) -> tuple[dict[tuple[UUID, date], repo.Key], dict[repo.Key, Pool]]:
    """The pool period of each (account, day), and the pools that have members. Two
    statements for any number of places."""
    days = repo.cycle_days(conn, sorted({account for account, _ in places}))
    keys = {
        place: period_key(place[0], days[place[0]], place[1])
        for place in places
        if place[0] in days
    }
    found: dict[repo.Key, list[Row]] = {key: [] for key in set(keys.values())}
    by_start = {(key[0], key[1]): rows for key, rows in found.items()}
    if found:
        for row in repo.lines(conn, sorted(found)):
            by_start[(row["account_id"], row["period_start"])].append(row)
    built = {key: build(key, rows, profile_of) for key, rows in found.items()}
    return keys, {key: pool for key, pool in built.items() if pool is not None}


def pool_on(
    conn: psycopg.Connection,
    account_id: UUID,
    day: date,
    profile_of: Callable[[UUID], dict[str, Any] | None],
) -> Pool | None:
    keys, found = pools(conn, [(account_id, day)], profile_of)
    key = keys.get((account_id, day))
    return None if key is None else found.get(key)


def pooled_offerings(conn: psycopg.Connection) -> list[UUID]:
    """The offerings whose effective network profile pools data. One statement; with none,
    nothing else about pools needs reading (the feed's batch checks this first)."""
    return [
        row["id"]
        for row in repo.network_offerings(conn)
        if profile.is_line(row["spec_details"])
        and pooled(profile.effective(row["spec_details"], row["offering_details"]))
    ]


def cached(fetch: Callable[[UUID], dict[str, Any] | None]):
    """`fetch` (an offering's effective profile) read once per offering."""
    seen: dict[UUID, dict[str, Any] | None] = {}

    def profile_of(offering_id: UUID) -> dict[str, Any] | None:
        if offering_id not in seen:
            seen[offering_id] = fetch(offering_id)
        return seen[offering_id]

    return profile_of
