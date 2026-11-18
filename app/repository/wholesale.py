"""Wholesale invoices, their lines and the reconciliation (docs/decisions/wholesale.md).

Every function runs a fixed number of statements, whatever the number of invoice lines:
the lines go in with one INSERT (matched to their product in the same statement), and each
discrepancy kind is one INSERT ... SELECT.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from app.integrations.network.wholesale import InvoiceLines
from app.repository._sql import Row, fetch_all, fetch_one, returning

KINDS = ("billedNotActive", "activeNotBilled", "usageMismatch", "duplicateCharge")
# product statuses that mean the line was live at some point (created and pendingActive never
# went live; cancelled and aborted never will)
LIVE = ("active", "suspended", "pendingTerminate", "terminated")
# usage_record units per invoice unit, and the absolute tolerance of a usage mismatch
USAGE = {"data": (1024 * 1024, 10), "voice": (60, 10), "sms": (1, 10)}
RELATIVE_TOLERANCE = Decimal("0.02")


def insert_invoice(
    conn: psycopg.Connection,
    *,
    invoice_number: str,
    lines: InvoiceLines,
    operator: str,
    now: datetime,
) -> Row:
    start, end = lines.period
    return returning(
        conn,
        "INSERT INTO wholesale_invoice (id, invoice_number, period, currency, total, line_count,"
        " state, imported_at, imported_by)"
        " VALUES (%s, %s, daterange(%s, %s, '[)'), %s, %s, %s, 'imported', %s, %s) RETURNING *",
        (
            uuid4(),
            invoice_number,
            start,
            end,
            lines.currency,
            lines.total,
            len(lines.lines),
            now,
            operator,
        ),
    )


# The product that held a number on a day: the MSISDN's assignment, else the SIM's (by ICCID),
# from number assignment history with cancelled claims ignored - as usage matching does.
_HOLDER = """
    coalesce(
        (SELECT a.product_id FROM number_assignment a
         JOIN subscriber_number n ON n.id = a.subscriber_number_id
         WHERE n.resource_specification_id = 'msisdn' AND n.value = {msisdn}
           AND a.cancelled_at IS NULL AND a.valid_for @> {day}
         ORDER BY a.effective_from DESC LIMIT 1),
        (SELECT a.product_id FROM number_assignment a
         JOIN subscriber_number n ON n.id = a.subscriber_number_id
         WHERE n.resource_specification_id = 'imsi' AND n.iccid = {iccid}
           AND a.cancelled_at IS NULL AND a.valid_for @> {day}
         ORDER BY a.effective_from DESC LIMIT 1))
"""


def insert_lines(conn: psycopg.Connection, invoice_id: UUID, lines: InvoiceLines) -> int:
    """Every line in one statement, each matched to the product that held its number on its
    period start."""
    rows = lines.lines
    holder = sql.SQL(_HOLDER).format(
        msisdn=sql.SQL("i.msisdn"), iccid=sql.SQL("i.iccid"), day=sql.SQL("i.period_start")
    )
    query = sql.SQL(
        "INSERT INTO wholesale_invoice_line (id, invoice_id, line_number, msisdn, iccid, period,"
        " charge_type, quantity, unit, amount, product_id)"
        " SELECT gen_random_uuid(), %s, i.line_number, i.msisdn, i.iccid,"
        " daterange(i.period_start, i.period_end, '[)'), i.charge_type, i.quantity, i.unit,"
        " i.amount, {holder}"
        " FROM unnest(%s::int[], %s::text[], %s::text[], %s::date[], %s::date[], %s::text[],"
        " %s::numeric[], %s::text[], %s::numeric[]) AS i(line_number, msisdn, iccid,"
        " period_start, period_end, charge_type, quantity, unit, amount)"
    ).format(holder=holder)
    return conn.execute(
        query,
        (
            invoice_id,
            [r.line_number for r in rows],
            [r.msisdn for r in rows],
            [r.iccid for r in rows],
            [r.period_start for r in rows],
            [r.period_end for r in rows],
            [r.charge_type for r in rows],
            [r.quantity for r in rows],
            [r.unit for r in rows],
            [r.amount for r in rows],
        ),
    ).rowcount


def _live_days(column: str) -> sql.Composed:
    """Days product `p` was live within the range `column` (0 when it never was)."""
    r = sql.SQL(column)
    return sql.SQL(
        "CASE WHEN p.id IS NULL OR p.start_date IS NULL OR p.status::text <> ALL(%(live)s)"
        " THEN 0 ELSE greatest(0, least(upper({r}), coalesce(p.termination_date, upper({r})))"
        " - greatest(lower({r}), p.start_date)) END"
    ).format(r=r)


def reconcile(conn: psycopg.Connection, invoice_id: UUID) -> None:
    """Find the discrepancies of an imported invoice (one statement per kind) and mark it
    reconciled."""
    params: dict[str, Any] = {"invoice": invoice_id, "live": list(LIVE)}
    # a line charge for a number no product held on the period start, or held 0 days of it
    conn.execute(
        sql.SQL(
            "INSERT INTO wholesale_discrepancy (id, invoice_id, kind, product_id, msisdn,"
            " invoice_line_id, expected, invoiced, detail)"
            " SELECT gen_random_uuid(), l.invoice_id, 'billedNotActive', l.product_id, l.msisdn,"
            " l.id, d.days, upper(l.period) - lower(l.period),"
            " jsonb_build_object('lineNumber', l.line_number, 'iccid', l.iccid, 'amount', l.amount)"
            " FROM wholesale_invoice_line l LEFT JOIN product p ON p.id = l.product_id"
            " CROSS JOIN LATERAL (SELECT {days} AS days) d"
            " WHERE l.invoice_id = %(invoice)s AND l.charge_type = 'line' AND d.days = 0"
        ).format(days=_live_days("l.period")),
        params,
    )
    # a prepaid line (not an add-on) live 1 day or more of the invoice period, no line charge
    conn.execute(
        sql.SQL(
            "INSERT INTO wholesale_discrepancy (id, invoice_id, kind, product_id, msisdn, expected,"
            " invoiced, detail)"
            " SELECT gen_random_uuid(), w.id, 'activeNotBilled', p.id,"
            " (SELECT n.value FROM number_assignment a"
            "  JOIN subscriber_number n ON n.id = a.subscriber_number_id"
            "  WHERE a.product_id = p.id AND n.resource_specification_id = 'msisdn'"
            "  AND a.cancelled_at IS NULL AND a.valid_for && w.period"
            "  ORDER BY a.effective_from DESC LIMIT 1),"
            " d.days, 0, jsonb_build_object('billingAccount', p.billing_account_id)"
            " FROM wholesale_invoice w"
            " JOIN product p ON p.start_date < upper(w.period)"
            "  AND (p.termination_date IS NULL OR p.termination_date > lower(w.period))"
            " JOIN billing_account b ON b.id = p.billing_account_id AND b.rating_type = 'prepaid'"
            " CROSS JOIN LATERAL (SELECT {days} AS days) d"
            " WHERE w.id = %(invoice)s AND p.parent_product_id IS NULL AND d.days >= 1"
            " AND NOT EXISTS (SELECT 1 FROM wholesale_invoice_line l WHERE l.invoice_id = w.id"
            "  AND l.product_id = p.id AND l.charge_type = 'line')"
        ).format(days=_live_days("w.period")),
        params,
    )
    # invoiced data, voice or SMS off the BSS's usage of the matched products by more than 2%
    # of the BSS figure and more than the absolute tolerance (10 MB, 10 minutes, 10 messages)
    conn.execute(
        "WITH w AS (SELECT id, period FROM wholesale_invoice WHERE id = %(invoice)s),"
        " matched AS (SELECT DISTINCT product_id FROM wholesale_invoice_line"
        "  WHERE invoice_id = %(invoice)s AND product_id IS NOT NULL),"
        " billed AS (SELECT product_id, charge_type AS kind, sum(quantity) AS quantity"
        "  FROM wholesale_invoice_line WHERE invoice_id = %(invoice)s AND product_id IS NOT NULL"
        "  AND charge_type IN ('data', 'voice', 'sms') GROUP BY 1, 2),"
        " used AS (SELECT u.product_id, u.kind, sum(u.quantity) AS raw"
        "  FROM usage_record u JOIN matched m ON m.product_id = u.product_id, w"
        "  WHERE u.status <> 'rejected' AND w.period @> u.event_date GROUP BY 1, 2),"
        " compared AS (SELECT coalesce(b.product_id, u.product_id) AS product_id,"
        "  coalesce(b.kind, u.kind) AS kind, coalesce(b.quantity, 0) AS invoiced,"
        "  round(coalesce(u.raw, 0)::numeric / CASE coalesce(b.kind, u.kind)"
        "   WHEN 'data' THEN %(mb)s WHEN 'voice' THEN %(minute)s ELSE 1 END, 3) AS expected"
        "  FROM billed b FULL JOIN used u ON u.product_id = b.product_id AND u.kind = b.kind)"
        " INSERT INTO wholesale_discrepancy (id, invoice_id, kind, product_id, msisdn,"
        " usage_kind, expected, invoiced, detail)"
        " SELECT gen_random_uuid(), %(invoice)s, 'usageMismatch', c.product_id,"
        " (SELECT min(l.msisdn) FROM wholesale_invoice_line l WHERE l.invoice_id = %(invoice)s"
        "  AND l.product_id = c.product_id),"
        " c.kind, c.expected, c.invoiced,"
        " jsonb_build_object('difference', c.invoiced - c.expected)"
        " FROM compared c"
        " WHERE abs(c.invoiced - c.expected) > %(relative)s * c.expected"
        " AND abs(c.invoiced - c.expected) > %(absolute)s",
        params
        | {
            "mb": USAGE["data"][0],
            "minute": USAGE["voice"][0],
            "relative": RELATIVE_TOLERANCE,
            "absolute": USAGE["data"][1],  # 10 of each unit
        },
    )
    # two line charges of one number in overlapping periods: one entry per later line
    conn.execute(
        "INSERT INTO wholesale_discrepancy (id, invoice_id, kind, product_id, msisdn,"
        " invoice_line_id, expected, invoiced, detail)"
        " SELECT gen_random_uuid(), b.invoice_id, 'duplicateCharge', b.product_id, b.msisdn,"
        " b.id, 1, 2, jsonb_build_object('lineNumber', b.line_number,"
        " 'duplicateOf', a.line_number, 'amount', b.amount)"
        " FROM wholesale_invoice_line a JOIN wholesale_invoice_line b"
        " ON b.invoice_id = a.invoice_id AND b.msisdn = a.msisdn AND b.charge_type = 'line'"
        " AND b.line_number > a.line_number AND b.period && a.period"
        " WHERE a.invoice_id = %(invoice)s AND a.charge_type = 'line'"
        " AND NOT EXISTS (SELECT 1 FROM wholesale_invoice_line c WHERE c.invoice_id = a.invoice_id"
        "  AND c.msisdn = a.msisdn AND c.charge_type = 'line' AND c.line_number < a.line_number"
        "  AND c.period && b.period)",
        params,
    )
    conn.execute("UPDATE wholesale_invoice SET state = 'reconciled' WHERE id = %(invoice)s", params)


# --- reads ---


def get(conn: psycopg.Connection, invoice_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM wholesale_invoice WHERE id = %s", (invoice_id,))


def page(conn: psycopg.Connection, offset: int, limit: int) -> tuple[list[Row], bool]:
    rows = fetch_all(
        conn,
        "SELECT * FROM wholesale_invoice ORDER BY imported_at DESC, id OFFSET %s LIMIT %s",
        (offset, limit + 1),
    )
    return rows[:limit], len(rows) > limit


def discrepancy_counts(conn: psycopg.Connection, invoice_id: UUID) -> dict[str, int]:
    rows = fetch_all(
        conn,
        "SELECT kind, count(*) AS n FROM wholesale_discrepancy WHERE invoice_id = %s GROUP BY kind",
        (invoice_id,),
    )
    found = {row["kind"]: row["n"] for row in rows}
    return {kind: found.get(kind, 0) for kind in KINDS}


def discrepancies(
    conn: psycopg.Connection, invoice_id: UUID, kind: str | None, offset: int, limit: int
) -> tuple[list[Row], bool]:
    rows = fetch_all(
        conn,
        "SELECT d.*, l.line_number FROM wholesale_discrepancy d"
        " LEFT JOIN wholesale_invoice_line l ON l.id = d.invoice_line_id"
        " WHERE d.invoice_id = %s AND (%s::text IS NULL OR d.kind = %s)"
        " ORDER BY array_position(%s::text[], d.kind), l.line_number NULLS LAST, d.msisdn, d.id"
        " OFFSET %s LIMIT %s",
        (invoice_id, kind, kind, list(KINDS), offset, limit + 1),
    )
    return rows[:limit], len(rows) > limit


# Cost and revenue per line of an invoice: one row per matched product, and one per number
# (MSISDN, else ICCID) no product held. Revenue: the product's ABR amounts before tax, in the
# invoice's currency, whose period overlaps the invoice's, prorated by the overlapping days.
_LINES = """
    WITH w AS (SELECT id, period, currency FROM wholesale_invoice WHERE id = %(invoice)s),
    cost AS (
        SELECT product_id, CASE WHEN product_id IS NULL THEN coalesce(msisdn, iccid) END AS number,
               min(msisdn) AS msisdn, min(iccid) AS iccid, sum(amount) AS cost,
               count(*) AS lines
        FROM wholesale_invoice_line WHERE invoice_id = %(invoice)s
        GROUP BY 1, 2),
    revenue AS (
        SELECT a.product_id,
               sum(a.amount * (upper(a.period * w.period) - lower(a.period * w.period))::numeric
                   / (upper(a.period) - lower(a.period))) AS revenue
        FROM applied_customer_billing_rate a, w
        WHERE a.product_id IN (SELECT product_id FROM cost WHERE product_id IS NOT NULL)
          AND a.currency = w.currency AND a.period && w.period
        GROUP BY a.product_id),
    joined AS (
        SELECT c.product_id, c.msisdn, c.iccid, c.lines, c.cost,
               coalesce(r.revenue, 0) AS revenue, coalesce(r.revenue, 0) - c.cost AS margin
        FROM cost c LEFT JOIN revenue r ON r.product_id = c.product_id)
"""
SORTS = {
    "margin": "margin, msisdn NULLS LAST, iccid, product_id",
    "-margin": "margin DESC, msisdn NULLS LAST, iccid, product_id",
    "msisdn": "msisdn NULLS LAST, iccid, product_id",
}


def line_margins(
    conn: psycopg.Connection, invoice_id: UUID, sort: str, offset: int, limit: int
) -> tuple[list[Row], bool]:
    rows = fetch_all(
        conn,
        sql.SQL(_LINES)
        + sql.SQL(" SELECT * FROM joined ORDER BY {} OFFSET %(offset)s LIMIT %(limit)s").format(
            sql.SQL(SORTS[sort])
        ),
        {"invoice": invoice_id, "offset": offset, "limit": limit + 1},
    )
    return rows[:limit], len(rows) > limit


def totals(conn: psycopg.Connection, invoice_id: UUID) -> Row:
    row = fetch_one(
        conn,
        sql.SQL(_LINES)
        + sql.SQL(
            " SELECT coalesce(sum(cost), 0) AS cost, coalesce(sum(revenue), 0) AS revenue,"
            " count(*) FILTER (WHERE product_id IS NOT NULL) AS matched,"
            " count(*) FILTER (WHERE product_id IS NULL) AS unmatched FROM joined"
        ),
        {"invoice": invoice_id},
    )
    assert row is not None
    return row
