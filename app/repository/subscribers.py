"""The Subscribers screen's list: billing accounts with their customer, paged and searched
by prefix on the server (#314; no counts, #312)."""

import re
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all

HEX = re.compile(r"[0-9a-f]{1,8}")
DIGITS = re.compile(r"\+?[0-9]{3,15}")


def _like_prefix(text: str) -> str:
    """A LIKE pattern matching `text` as a prefix, its wildcards escaped."""
    return re.sub(r"([\\%_])", r"\\\1", text) + "%"


def _id_range(prefix: str) -> tuple[str, str]:
    """The uuids whose text starts with `prefix` (1-8 hex digits): a primary-key range."""
    low = (prefix + "0" * 32)[:32]
    high = (prefix + "f" * 32)[:32]
    return (str(UUID(low)), str(UUID(high)))


def search(
    conn: psycopg.Connection,
    *,
    q: str | None,
    state: str | None,
    rating_type: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    """Accounts oldest first, each joined to its customer's name. `q` matches by prefix: a
    customer or account name (case-insensitive), a subscriber number on a line today, or
    a short id (a prefix of the account or customer id, `BA-` optional); a full uuid is
    the account. Returns the page and whether more rows follow (limit + 1 read)."""
    conds = ["true"]
    params: dict[str, Any] = {"offset": offset, "limit": limit + 1}
    if state is not None:
        conds.append("a.state::text = %(state)s")
        params["state"] = state
    if rating_type is not None:
        conds.append("a.rating_type = %(rating)s")
        params["rating"] = rating_type
    term = (q or "").strip()
    if term:
        matches = [
            "SELECT id FROM billing_account WHERE lower(name) LIKE %(name)s",
            "SELECT b.id FROM billing_account b JOIN customer c ON c.id = b.customer_id"
            " WHERE lower(c.name) LIKE %(name)s",
        ]
        params["name"] = _like_prefix(term.lower())
        bare = re.sub(r"(?i)^ba-", "", term.replace(" ", "")).lower()
        try:
            params["uuid"] = str(UUID(bare))
            matches.append("SELECT %(uuid)s::uuid")
        except ValueError:
            pass
        if HEX.fullmatch(bare):
            params["low"], params["high"] = _id_range(bare)
            matches.append(
                "SELECT id FROM billing_account WHERE id BETWEEN %(low)s::uuid AND %(high)s::uuid"
            )
            matches.append(
                "SELECT b.id FROM billing_account b WHERE b.customer_id"
                " BETWEEN %(low)s::uuid AND %(high)s::uuid"
            )
        if DIGITS.fullmatch(term):
            params["number"] = _like_prefix(term.lstrip("+"))
            matches.append(
                "SELECT p.billing_account_id FROM subscriber_number n"
                " JOIN number_assignment na ON na.subscriber_number_id = n.id"
                " JOIN product p ON p.id = na.product_id"
                " WHERE n.resource_specification_id = 'msisdn' AND n.value LIKE %(number)s"
                " AND na.cancelled_at IS NULL AND upper_inf(na.valid_for)"
            )
        conds.append("a.id IN (" + " UNION ".join(matches) + ")")
    where = " AND ".join(conds)  # fixed fragments; every value is a parameter
    query = (
        "SELECT a.*, c.name AS customer_name FROM billing_account a"  # noqa: S608
        f" JOIN customer c ON c.id = a.customer_id WHERE {where}"
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
