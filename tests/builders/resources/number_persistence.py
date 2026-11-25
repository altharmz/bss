"""Builders other test modules share, from tests/resources/test_number_persistence.py (#423)."""

from datetime import date

from tests.factories import NOW


def number(conn, value="12025550123", kind=None, iccid=None):
    # Only the columns asked for: migration tests build numbers on schemas older than
    # resource_specification_id and iccid.
    extra = {"resource_specification_id": kind, "iccid": iccid}
    extra = {column: v for column, v in extra.items() if v is not None}
    columns = "".join(f", {column}" for column in extra)
    marks = ", %s" * len(extra)
    return conn.execute(
        f"INSERT INTO subscriber_number (value, name, created_at, updated_at{columns})"  # noqa: S608
        f" VALUES (%s, 'Number', %s, %s{marks}) RETURNING id",
        (value, NOW, NOW, *extra.values()),
    ).fetchone()[0]


def assignment(conn, number_id, prod, start=date(2026, 1, 1), end=None, cancelled=False, kind=None):
    # The kind column only when asked for: migration tests run on schemas older than it.
    column, mark = (", resource_specification_id", ", %s") if kind else ("", "")
    return conn.execute(
        f"""INSERT INTO number_assignment
            (subscriber_number_id, product_id, assigned_by_item_id, effective_from,
             released_on, ended_by_item_id, quarantine_days, created_at, updated_at,
             cancelled_at, cancelled_by_item_id{column})
           VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s{mark}) RETURNING id""",  # noqa: S608
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
            *([kind] if kind else []),
        ),
    ).fetchone()[0]
