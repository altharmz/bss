"""The prepaid KPI report's two aggregates (issue #198, docs/decisions/reporting.md): line
movements and revenue by month for the prepaid accounts of one currency. Read-only."""

from datetime import date
from decimal import Decimal

import psycopg


def line_movements(
    conn: psycopg.Connection, currency: str, first: date, last: date
) -> tuple[int, dict[tuple[str, date], int]]:
    """The lines live on the day before `first`, and per (kind, month) the lines that
    started (`add`) or ended (`disconnect`, their termination date) from `first` to `last`.
    A line is a prepaid account's product that is not an add-on and was ever live (half-open
    [start_date, termination_date); cancelled and aborted products never were)."""
    rows = conn.execute(
        """
        WITH line AS (
            SELECT p.start_date, p.termination_date
              FROM product p
              JOIN billing_account a ON a.id = p.billing_account_id
             WHERE a.rating_type = 'prepaid' AND a.currency = %(currency)s
               AND p.parent_product_id IS NULL
               AND p.status NOT IN ('cancelled', 'aborted')
               AND (p.termination_date IS NULL OR p.termination_date > p.start_date)
               AND p.start_date <= %(last)s
               AND (p.termination_date IS NULL OR p.termination_date >= %(first)s)
        )
        SELECT 'opening', NULL::date, count(*) FROM line WHERE start_date < %(first)s
        UNION ALL
        SELECT 'add', date_trunc('month', start_date)::date, count(*)
          FROM line WHERE start_date >= %(first)s GROUP BY 2
        UNION ALL
        SELECT 'disconnect', date_trunc('month', termination_date)::date, count(*)
          FROM line WHERE termination_date <= %(last)s GROUP BY 2
        """,
        {"currency": currency, "first": first, "last": last},
    ).fetchall()
    opening = 0
    moves: dict[tuple[str, date], int] = {}
    for kind, month, count in rows:
        if kind == "opening":
            opening = count
        else:
            moves[(kind, month)] = count
    return opening, moves


def revenue(
    conn: psycopg.Connection, currency: str, first: date, last: date
) -> dict[tuple[str, date], Decimal]:
    """Tax-excluded ABR amounts per (source, month) by their billing run's date: `usage`
    (rated pay-as-you-go usage), `plans` (recurring charges and their refunds), `addOns`
    (one-time fees of add-on products) and `fees` (every other one-time fee)."""
    rows = conn.execute(
        """
        SELECT CASE WHEN r.usage_command_id IS NOT NULL THEN 'usage'
                    WHEN r.charge_type <> 'oneTime' THEN 'plans'
                    WHEN p.parent_product_id IS NOT NULL THEN 'addOns'
                    ELSE 'fees' END,
               date_trunc('month', run.run_date)::date,
               sum(r.amount)
          FROM applied_customer_billing_rate r
          JOIN billing_run run ON run.id = r.billing_run_id
          JOIN billing_account a ON a.id = r.billing_account_id
          LEFT JOIN product p ON p.id = r.product_id
         WHERE a.rating_type = 'prepaid' AND a.currency = %(currency)s
           AND run.run_date BETWEEN %(first)s AND %(last)s
         GROUP BY 1, 2
        """,
        {"currency": currency, "first": first, "last": last},
    ).fetchall()
    return {(source, month): amount for source, month, amount in rows}
