"""Shared builders of tests/test_number_persistence.py, used by other test modules (#423)."""

from datetime import date

from tests.factories import NOW


def number(conn, value="12025550123"):
    return conn.execute(
        "INSERT INTO subscriber_number (value, name, created_at, updated_at)"
        " VALUES (%s, 'Number', %s, %s) RETURNING id",
        (value, NOW, NOW),
    ).fetchone()[0]


def assignment(conn, number_id, prod, start=date(2026, 1, 1), end=None, cancelled=False):
    return conn.execute(
        """INSERT INTO number_assignment
            (subscriber_number_id, product_id, assigned_by_item_id, effective_from,
             released_on, ended_by_item_id, quarantine_days, created_at, updated_at,
             cancelled_at, cancelled_by_item_id)
           VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s) RETURNING id""",
        (
            number_id,
            prod["id"],
            prod["created_by_item_id"],
            start,
            end,
            prod["created_by_item_id"] if end else None,
            NOW,
            NOW,
            NOW if cancelled else None,
            prod["created_by_item_id"] if cancelled else None,
        ),
    ).fetchone()[0]
