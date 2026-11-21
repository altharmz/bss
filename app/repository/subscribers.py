"""The Subscribers screen's list: billing accounts with their customer, paged and searched
by prefix on the server (#314; no counts, #312)."""

import re
from contextlib import nullcontext
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all

HEX = re.compile(r"[0-9a-f]{1,8}")
DIGITS = re.compile(r"\+?[0-9]{3,15}")


def like_prefix(text: str) -> str:
    """A LIKE pattern matching `text` as a prefix, its wildcards escaped."""
    return re.sub(r"([\\%_])", r"\\\1", text) + "%"


def id_range(prefix: str) -> tuple[str, str]:
    """The uuids whose text starts with `prefix` (1-8 hex digits): a primary-key range."""
    low = (prefix + "0" * 32)[:32]
    high = (prefix + "f" * 32)[:32]
    return (str(UUID(low)), str(UUID(high)))


MATCH_CAP = 1000


def matching(
    conn: psycopg.Connection,
    *,
    alias: str,
    conds: list[str],
    matches: list[tuple[str, str] | tuple[str, str, str]],
    params: dict[str, Any],
    offset: int,
    limit: int,
) -> tuple[str, int]:
    """The condition that selects a search's page, and the offset left to apply (#330).

    `matches` are (source, condition) pairs, any of which matches the search; a source
    names only the tables its condition needs (`alias` is the searched table). How
    many match is unknown to the planner (a prefix of one letter can match every row), so
    one probe collects up to MATCH_CAP of them, with the filters. Fewer: the page is cut
    from the probe, oldest first, and read by id. More: the page walks the
    `(created_at, id)` index and tests each row, which finds a page of a broad match
    early. A set of every match (`id IN (... UNION ...)`) cost a hash of the whole table:
    20-60 ms for a narrow search on 100k rows, 1-1.6 s for a broad one.

    A match may add a third element, its page condition (#365): the probe reads from the
    matched side (subscriber numbers), the walking page tests each row on its own (a
    correlated EXISTS), which a set of every match would cost a hash of. The probe is read
    through a server-side cursor and stops once it has seen more than MATCH_CAP distinct
    rows: a broad match reads about MATCH_CAP rows, not the MATCH_CAP per match the limit
    allows."""
    where = " AND ".join(conds)  # fixed fragments; every value is a parameter
    probe = " UNION ALL ".join(
        f"(SELECT {alias}.id, {alias}.created_at FROM {m[0]} WHERE {where} AND {m[1]})"  # noqa: S608
        for m in matches
    )
    # A row can match several ways (an account and its customer by the same name), so
    # every way may bring MATCH_CAP rows: a probe within its limit has every match.
    most = MATCH_CAP * len(matches)
    found: set[tuple[Any, Any]] = set()
    read = 0
    # A server-side cursor needs a transaction: a request's connection opens its own.
    scope = conn.transaction() if conn.autocommit else nullcontext()
    with scope, conn.cursor(name="match_probe") as cur:
        cur.execute(f"{probe} LIMIT {most + 1}", params)
        while len(found) <= MATCH_CAP:
            # No more than could still prove the match broad: each read row costs 50-100 µs
            # on the number side.
            want = MATCH_CAP + 1 - len(found)
            batch = cur.fetchmany(want)
            read += len(batch)
            found.update((created_at, id_) for id_, created_at in batch)
            if len(batch) < want:
                break
    if read > most or len(found) > MATCH_CAP:
        return "(" + " OR ".join(m[-1] for m in matches) + ")", offset
    # PostgreSQL orders uuids bytewise, Python by their integer value: the same order.
    ordered = sorted(found)
    params["matched"] = [id_ for _, id_ in ordered[offset : offset + limit + 1]]
    return f"{alias}.id = ANY(%(matched)s)", 0


def search(
    conn: psycopg.Connection,
    *,
    q: str | None,
    state: str | None,
    rating_type: str | None,
    offset: int,
    limit: int,
    service_address_missing: bool = False,
) -> tuple[list[Row], bool]:
    """Accounts oldest first, each joined to its customer's name. `q` matches by prefix: a
    customer or account name (case-insensitive), a subscriber number on a line today, or
    a short id (a prefix of the account or customer id, `BA-` optional); a full uuid is
    the account. Returns the page and whether more rows follow (limit + 1 read).

    The filters compare the enum, not its text: as text they had no statistics and planned
    a full scan (#330). Without a search the page is cut from billing_account alone and
    only its rows are joined to their customer; a search goes through `matching`.

    `service_address_missing` keeps the prepaid accounts with a live line (not terminated,
    cancelled or aborted) and no service address (#207): their new lines are refused and
    their charges taxed at the flat rate (docs/decisions/taxes.md)."""
    conds = ["true"]
    params: dict[str, Any] = {"limit": limit + 1}
    if state is not None:
        conds.append("a.state = %(state)s::account_state")
        params["state"] = state
    if rating_type is not None:
        conds.append("a.rating_type = %(rating)s")
        params["rating"] = rating_type
    if service_address_missing:
        conds.append(
            "a.rating_type = 'prepaid' AND a.service_address IS NULL AND EXISTS"
            " (SELECT 1 FROM product p WHERE p.billing_account_id = a.id"
            " AND p.status NOT IN ('terminated', 'cancelled', 'aborted'))"
        )
    term = (q or "").strip()
    if not term:
        params["offset"] = offset
        where = " AND ".join(conds)  # fixed fragments; every value is a parameter
        query = (
            "SELECT a.*, c.name AS customer_name"  # noqa: S608
            f" FROM (SELECT * FROM billing_account a WHERE {where}"
            " ORDER BY a.created_at, a.id OFFSET %(offset)s LIMIT %(limit)s) a"
            " JOIN customer c ON c.id = a.customer_id ORDER BY a.created_at, a.id"
        )
        rows = fetch_all(conn, query, params)
        return rows[:limit], len(rows) > limit
    source = "billing_account a JOIN customer c ON c.id = a.customer_id"
    matches = [
        ("billing_account a", "lower(a.name) LIKE %(name)s"),
        (source, "lower(c.name) LIKE %(name)s"),
    ]
    params["name"] = like_prefix(term.lower())
    bare = re.sub(r"(?i)^ba-", "", term.replace(" ", "")).lower()
    try:
        params["uuid"] = str(UUID(bare))
        matches.append(("billing_account a", "a.id = %(uuid)s::uuid"))
    except ValueError:
        pass
    if HEX.fullmatch(bare):
        params["low"], params["high"] = id_range(bare)
        matches.append(("billing_account a", "a.id BETWEEN %(low)s::uuid AND %(high)s::uuid"))
        matches.append(
            ("billing_account a", "a.customer_id BETWEEN %(low)s::uuid AND %(high)s::uuid")
        )
    if DIGITS.fullmatch(term):
        params["number"] = like_prefix(term.lstrip("+"))
        # `IS TRUE`: the partial GiST exclusion indexes (cancelled_at IS NULL) took 40-55 µs
        # a lookup, the btree indexes led by subscriber_number_id and product_id 13-16 µs.
        live = "(na.cancelled_at IS NULL) IS TRUE AND upper_inf(na.valid_for)"
        number = "n.resource_specification_id = 'msisdn' AND n.value LIKE %(number)s"
        # The probe walks the matching numbers to their accounts, a nested loop from the
        # first number (OFFSET 0 keeps the lateral from being flattened into a hash join
        # of every match); the page tests each account (#365).
        matches.append(
            (
                "subscriber_number n CROSS JOIN LATERAL (SELECT a.* FROM number_assignment na"  # noqa: S608
                " JOIN product p ON p.id = na.product_id"
                " JOIN billing_account a ON a.id = p.billing_account_id"
                f" WHERE na.subscriber_number_id = n.id AND {live} OFFSET 0) a",
                number,
                "EXISTS (SELECT FROM product p JOIN number_assignment na"
                f" ON na.product_id = p.id AND {live}"
                " JOIN subscriber_number n ON n.id = na.subscriber_number_id"
                f" WHERE p.billing_account_id = a.id AND {number})",
            )
        )
    match, params["offset"] = matching(
        conn,
        alias="a",
        conds=conds,
        matches=matches,
        params=params,
        offset=offset,
        limit=limit,
    )
    where = " AND ".join([*conds, match])  # fixed fragments; every value is a parameter
    query = (
        f"SELECT a.*, c.name AS customer_name FROM {source} WHERE {where}"  # noqa: S608
        " ORDER BY a.created_at, a.id OFFSET %(offset)s LIMIT %(limit)s"
    )
    rows = fetch_all(conn, query, params)
    return rows[:limit], len(rows) > limit


def active_products(conn: psycopg.Connection, account_ids: list[UUID]) -> dict[UUID, int]:
    """Active products per account, for a page of accounts."""
    return {
        account: n
        for account, n in conn.execute(
            "SELECT billing_account_id, count(*) FROM product WHERE billing_account_id = ANY(%s)"
            " AND status = 'active' GROUP BY 1",
            (account_ids,),
        )
    }


def customer_accounts(conn: psycopg.Connection, customer_ids: list[UUID]) -> dict[UUID, int]:
    """How many accounts each customer of a page has (the screen names the account when
    a customer has more than one)."""
    return {
        customer: n
        for customer, n in conn.execute(
            "SELECT customer_id, count(*) FROM billing_account WHERE customer_id = ANY(%s)"
            " GROUP BY 1",
            (customer_ids,),
        )
    }
