"""The Parties screen's lists and the account owner picker: parties and customers, paged
and searched by prefix on the server (#314; no counts, #312), with what each row shows
(its customers, their billing accounts) computed for the page only."""

import re
from collections import defaultdict
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all
from app.repository.subscribers import HEX, id_range, like_prefix


def _id_matches(term: str, table: str, params: dict[str, Any]) -> list[str]:
    """A full uuid, or a prefix of the id (1-8 hex digits, served by the primary key)."""
    bare = term.replace(" ", "").lower()
    matches = []
    try:
        params["uuid"] = str(UUID(bare))
        matches.append("SELECT %(uuid)s::uuid")
    except ValueError:
        pass
    if HEX.fullmatch(bare):
        params["low"], params["high"] = id_range(bare)
        matches.append(
            f"SELECT id FROM {table} WHERE id BETWEEN %(low)s::uuid AND %(high)s::uuid"  # noqa: S608
        )
    return matches


def _page(conn: psycopg.Connection, query: str, params: dict[str, Any], limit: int):
    rows = fetch_all(conn, query, params)
    return rows[:limit], len(rows) > limit


def search_parties(
    conn: psycopg.Connection,
    *,
    kind: str | None,
    q: str | None,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    """Parties oldest first. `q` matches by prefix, case-insensitive: an individual's given
    name, "given family" or family name, an organization's name; or a short or full id."""
    conds = ["true"]
    params: dict[str, Any] = {"offset": offset, "limit": limit + 1}
    if kind is not None:
        conds.append("p.party_type::text = %(kind)s")
        params["kind"] = kind
    if status is not None:
        conds.append("p.status = %(status)s")
        params["status"] = status
    term = (q or "").strip()
    if term:
        params["name"] = like_prefix(re.sub(r"\s+", " ", term.lower()))
        matches = [
            "SELECT id FROM party WHERE party_type = 'Individual'"
            " AND lower(coalesce(given_name, '') || ' ' || family_name) LIKE %(name)s",
            "SELECT id FROM party WHERE party_type = 'Individual'"
            " AND lower(family_name) LIKE %(name)s",
            "SELECT id FROM party WHERE party_type = 'Organization'"
            " AND lower(organization_name) LIKE %(name)s",
            *_id_matches(term, "party", params),
        ]
        conds.append("p.id IN (" + " UNION ".join(matches) + ")")
    where = " AND ".join(conds)  # fixed fragments; every value is a parameter
    query = (
        f"SELECT p.* FROM party p WHERE {where}"  # noqa: S608
        " ORDER BY p.created_at, p.id OFFSET %(offset)s LIMIT %(limit)s"
    )
    return _page(conn, query, params, limit)


def search_customers(
    conn: psycopg.Connection,
    *,
    q: str | None,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[list[Row], bool]:
    """Customers oldest first, each with its party's type and name. `q` matches the
    customer name by prefix (case-insensitive), or a short or full id."""
    conds = ["true"]
    params: dict[str, Any] = {"offset": offset, "limit": limit + 1}
    if status is not None:
        conds.append("c.status::text = %(status)s")
        params["status"] = status
    term = (q or "").strip()
    if term:
        params["name"] = like_prefix(term.lower())
        matches = [
            "SELECT id FROM customer WHERE lower(name) LIKE %(name)s",
            *_id_matches(term, "customer", params),
        ]
        conds.append("c.id IN (" + " UNION ".join(matches) + ")")
    where = " AND ".join(conds)  # fixed fragments; every value is a parameter
    query = (
        "SELECT c.*, p.party_type::text AS party_kind,"  # noqa: S608
        " coalesce(p.organization_name, concat_ws(' ', p.given_name, p.family_name))"
        " AS party_name"
        f" FROM customer c JOIN party p ON p.id = c.party_id WHERE {where}"
        " ORDER BY c.created_at, c.id OFFSET %(offset)s LIMIT %(limit)s"
    )
    return _page(conn, query, params, limit)


def customers_of(conn: psycopg.Connection, party_ids: list[UUID]) -> dict[UUID, list[Row]]:
    """The customers engaging each party of a page, oldest first."""
    grouped: dict[UUID, list[Row]] = defaultdict(list)
    for row in fetch_all(
        conn,
        "SELECT id, name, party_id FROM customer WHERE party_id = ANY(%s) ORDER BY created_at, id",
        (party_ids,),
    ):
        grouped[row["party_id"]].append(row)
    return grouped


def accounts_of(conn: psycopg.Connection, customer_ids: list[UUID]) -> dict[UUID, list[Row]]:
    """The billing accounts of each customer of a page, oldest first."""
    grouped: dict[UUID, list[Row]] = defaultdict(list)
    for row in fetch_all(
        conn,
        "SELECT id, name, customer_id FROM billing_account WHERE customer_id = ANY(%s)"
        " ORDER BY created_at, id",
        (customer_ids,),
    ):
        grouped[row["customer_id"]].append(row)
    return grouped


def account_owners(conn: psycopg.Connection, party_ids: list[UUID]) -> set[UUID]:
    """The parties of a page that own a financial, party or settlement account."""
    return {
        row[0]
        for row in conn.execute(
            "SELECT party_id FROM financial_account WHERE party_id = ANY(%(ids)s)"
            " UNION SELECT party_id FROM party_account WHERE party_id = ANY(%(ids)s)",
            {"ids": party_ids},
        )
    }
