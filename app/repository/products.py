"""Product inventory (TMF637). Products are only ever created or terminated by an order
item - hence the mandatory `created_by_item_id` / `terminated_by_item_id`."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all, fetch_one, jsonb, returning, select_page


@dataclass(frozen=True)
class NewProduct:
    """What a client chose for a product its POST /product creates: its id, TMF meta
    attributes, name (None: the offering's) and documents."""

    id: UUID | None = None
    base_type: str | None = None
    schema_location: str | None = None
    name: str | None = None
    details: dict[str, Any] | None = None


def create_product(
    conn: psycopg.Connection,
    *,
    billing_account_id: UUID,
    product_offering_id: UUID,
    created_by_item_id: UUID,
    start_date: date,
    status: str = "active",
    new: NewProduct | None = None,
) -> Row:
    """Without a `name`, the product is named after its offering at creation (the
    database's `product_default_name`); the name is its own from then on. Name and
    documents are written only when given: tests create products on schemas before
    0028."""
    new = new or NewProduct()
    row = returning(
        conn,
        """
        INSERT INTO product
            (id, billing_account_id, product_offering_id, status, start_date,
             created_by_item_id, base_type, schema_location)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            new.id,
            billing_account_id,
            product_offering_id,
            status,
            start_date,
            created_by_item_id,
            new.base_type,
            new.schema_location,
        ),
    )
    if new.name is None and not new.details:
        return row
    return describe(conn, row["id"], name=new.name or row["name"], details=new.details or {})


def describe(
    conn: psycopg.Connection, product_id: UUID, *, name: str, details: dict[str, Any]
) -> Row:
    """A product's name and documents (PATCH: they neither create nor end it)."""
    return returning(
        conn,
        "UPDATE product SET name = %s, details = %s WHERE id = %s RETURNING *",
        (name, jsonb(details), product_id),
    )


def get_product(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    return fetch_one(conn, "SELECT * FROM product WHERE id = %s", (product_id,))


def set_product_status(conn: psycopg.Connection, product_id: UUID, status: str) -> Row:
    return returning(
        conn, "UPDATE product SET status = %s WHERE id = %s RETURNING *", (status, product_id)
    )


def terminate_product(
    conn: psycopg.Connection,
    product_id: UUID,
    *,
    termination_date: date,
    terminated_by_item_id: UUID,
    status: str = "terminated",
) -> Row:
    """`termination_date` is exclusive: the product is no longer billable on that day."""
    return returning(
        conn,
        """
        UPDATE product
        SET status = %s, termination_date = %s, terminated_by_item_id = %s
        WHERE id = %s RETURNING *
        """,
        (status, termination_date, terminated_by_item_id, product_id),
    )


def cancel_unstarted(conn: psycopg.Connection, product_id: UUID) -> Row:
    """Undo an add item: the product never starts (zero billable days, as a delete of a
    product that has not started). No terminating item - its creating order is cancelled."""
    return returning(
        conn,
        "UPDATE product SET status = 'cancelled', termination_date = start_date"
        " WHERE id = %s RETURNING *",
        (product_id,),
    )


def restore(conn: psycopg.Connection, product_id: UUID, status: str) -> Row:
    """Undo a delete item: the product is open-ended again, in `status`."""
    return returning(
        conn,
        """
        UPDATE product SET status = %s, termination_date = NULL, terminated_by_item_id = NULL
        WHERE id = %s RETURNING *
        """,
        (status, product_id),
    )


def charged_from(conn: psycopg.Connection, product_id: UUID, day: date) -> bool:
    """Whether billing wrote any ABR of the product for a period starting on `day` or
    later (a charge's or refund's range starts inside its cycle period)."""
    row = fetch_one(
        conn,
        "SELECT EXISTS (SELECT 1 FROM applied_customer_billing_rate"
        " WHERE product_id = %s AND lower(period) >= %s) AS charged",
        (product_id, day),
    )
    assert row is not None
    return row["charged"]


def list_active_products(conn: psycopg.Connection, billing_account_id: UUID, on: date) -> list[Row]:
    """Products of the account that are billable on `on`.

    Driven by dates, not by lifecycle status: a future-dated product is billable from its
    start day whether or not activation has run yet. Only products that never came into
    being (cancelled, aborted) are excluded by status.
    """
    return fetch_all(
        conn,
        """
        SELECT * FROM product
        WHERE billing_account_id = %s
          AND start_date <= %s
          AND (termination_date IS NULL OR %s < termination_date)
          AND status NOT IN ('cancelled', 'aborted')
        ORDER BY start_date, id
        """,
        (billing_account_id, on, on),
    )


def list_products(
    conn: psycopg.Connection, *, where: dict[str, Any], offset: int, limit: int
) -> tuple[list[Row], int]:
    return select_page(conn, "product", where=where, offset=offset, limit=limit)


def lock_products(conn: psycopg.Connection, product_ids: list[UUID]) -> dict[UUID, Row]:
    """Lock these product rows until the transaction ends (SELECT ... FOR UPDATE), in id
    order so two orders locking overlapping sets cannot deadlock. A concurrent order that
    wants the same products waits here, then sees their committed state."""
    rows = fetch_all(
        conn,
        "SELECT * FROM product WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
        (sorted(set(product_ids)),),
    )
    return {row["id"]: row for row in rows}


def flip_statuses(
    conn: psycopg.Connection,
    run_date: date,
    *,
    visible_before: datetime,
    flipped: list[UUID] | None = None,
) -> dict[str, int]:
    """The status flips of the billing run of `run_date` (docs/stage4-design.md section
    5): created / pendingActive -> active once start_date <= run_date, pendingTerminate ->
    terminated once termination_date <= run_date - only for products whose order (the one
    that created them, resp. the one that terminates them) completed before
    `visible_before` (00:00 of the run date): what the run sees, so a replay flips the same
    products the daily run did. Statuses follow dates, not billing: an account blocked by
    a failure is flipped too. cancelled / aborted are never touched.

    The due products are locked in id order first, like lock_products (an order deleting
    several products locks them the same way), so the flips and an order cannot deadlock
    (Q8). Idempotent: a second call flips nothing. Returns the count per new status;
    `flipped`, if given, receives the ids of the products flipped (for their events)."""
    params = {"day": run_date, "before": visible_before}
    due = fetch_all(
        conn,
        """
        SELECT id FROM product WHERE id IN (
            SELECT p.id FROM product p
              JOIN product_order_item ci ON ci.id = p.created_by_item_id
              JOIN product_order c ON c.id = ci.product_order_id
             WHERE p.status IN ('created', 'pendingActive') AND p.start_date <= %(day)s
               AND c.completion_date < %(before)s
            UNION
            SELECT p.id FROM product p
              JOIN product_order_item ti ON ti.id = p.terminated_by_item_id
              JOIN product_order t ON t.id = ti.product_order_id
             WHERE p.status = 'pendingTerminate' AND p.termination_date <= %(day)s
               AND t.completion_date < %(before)s)
         ORDER BY id FOR UPDATE
        """,
        params,
    )
    if not due:
        return {}
    rows = fetch_all(
        conn,
        """
        UPDATE product SET status = CASE WHEN status = 'pendingTerminate'
                                         THEN 'terminated' ELSE 'active' END::product_status_type
         WHERE id = ANY(%(ids)s)
           AND ((status IN ('created', 'pendingActive') AND start_date <= %(day)s)
                OR (status = 'pendingTerminate' AND termination_date <= %(day)s))
        RETURNING id, status
        """,
        {**params, "ids": [row["id"] for row in due]},
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if flipped is not None:
        flipped.extend(sorted(row["id"] for row in rows))
    return counts


# Products that are still to be billed or are being billed: not ended. What the admin
# UI lists as an account's products.
OPEN_STATUSES = ("created", "pendingActive", "active", "pendingTerminate", "suspended")


def list_open_products(conn: psycopg.Connection, billing_account_id: UUID, on: date) -> list[Row]:
    """The account's open products with their offering on `on` (on the start date for a
    product that starts later: `offering_id`, name, code) and its specification (name),
    oldest start first."""
    return fetch_all(
        conn,
        """
        SELECT p.*, o.id AS offering_id, o.name AS offering_name, o.code AS offering_code,
               s.id AS specification_id, s.name AS specification_name
        FROM product p
        JOIN product_offering o
          ON o.id = product_offering_on(p.id, greatest(p.start_date, %s::date))
        LEFT JOIN product_specification s ON s.id = o.product_specification_id
        WHERE p.billing_account_id = %s AND p.status = ANY(%s)
        ORDER BY p.start_date, p.created_at, p.id
        """,
        (on, billing_account_id, list(OPEN_STATUSES)),
    )


def changes_of(conn: psycopg.Connection, product_id: UUID) -> list[Row]:
    """The product's offering changes, by change day."""
    return fetch_all(
        conn,
        "SELECT * FROM product_offering_change WHERE product_id = %s ORDER BY change_date",
        (product_id,),
    )


def change_items(conn: psycopg.Connection, product_id: UUID) -> list[UUID]:
    """The modify items that changed the product's offering, by change day."""
    return [row["changed_by_item_id"] for row in changes_of(conn, product_id)]


def change_billed(
    conn: psycopg.Connection, product_id: UUID, day: date, *, mid_period: bool
) -> bool:
    """Whether billing reflects an offering change on `day`: an ABR of the product from
    that day on, or (`mid_period`: the day is not a cycle day) a charge ending on it."""
    row = fetch_one(
        conn,
        """
        SELECT EXISTS (
            SELECT 1 FROM applied_customer_billing_rate
            WHERE product_id = %(product)s
              AND (lower(period) >= %(day)s
                   OR (%(mid)s AND charge_type = 'recurring' AND upper(period) = %(day)s))
        ) AS billed
        """,
        {"product": product_id, "day": day, "mid": mid_period},
    )
    assert row is not None
    return row["billed"]


def pending_change(conn: psycopg.Connection, product_id: UUID, today: date) -> Row | None:
    """The product's offering change that has not taken effect (after `today`), if any."""
    return fetch_one(
        conn,
        "SELECT * FROM product_offering_change WHERE product_id = %s AND change_date > %s",
        (product_id, today),
    )


def last_change(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    return fetch_one(
        conn,
        "SELECT * FROM product_offering_change WHERE product_id = %s"
        " ORDER BY change_date DESC LIMIT 1",
        (product_id,),
    )


def change_offering(
    conn: psycopg.Connection,
    product_id: UUID,
    *,
    change_date: date,
    product_offering_id: UUID,
    changed_by_item_id: UUID,
    announced_at: datetime | None = None,
) -> Row:
    """The product is on `product_offering_id` from `change_date` on (a modify item).
    `announced_at`: the change takes effect now and its event was emitted."""
    return returning(
        conn,
        """
        INSERT INTO product_offering_change
            (product_id, change_date, product_offering_id, changed_by_item_id,
             effect_announced_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (product_id, change_date, product_offering_id, changed_by_item_id, announced_at),
    )


def announce_changes(
    conn: psycopg.Connection, run_date: date, *, visible_before: datetime, at: datetime
) -> list[UUID]:
    """Mark the offering changes in effect on `run_date` whose event is still due - those
    the run sees (their order completed before `visible_before`) - and return their
    products. Idempotent: a retried run marks nothing twice."""
    rows = fetch_all(
        conn,
        """
        UPDATE product_offering_change c SET effect_announced_at = %(at)s
        FROM product_order_item i JOIN product_order o ON o.id = i.product_order_id
        WHERE i.id = c.changed_by_item_id AND c.effect_announced_at IS NULL
          AND c.change_date <= %(day)s AND o.completion_date < %(before)s
        RETURNING c.product_id
        """,
        {"at": at, "day": run_date, "before": visible_before},
    )
    return sorted({row["product_id"] for row in rows})


def undo_change(conn: psycopg.Connection, changed_by_item_id: UUID) -> Row:
    """Undo a modify item's change (its order is cancelled before the change day)."""
    return returning(
        conn,
        "DELETE FROM product_offering_change WHERE changed_by_item_id = %s RETURNING *",
        (changed_by_item_id,),
    )


# --- status changes: suspension and resumption (TMF622 modify items with product.status;
# docs/decisions/tmf-api.md#suspension-2026-10-19). Billing does not read them. ---


def status_changes_of(conn: psycopg.Connection, product_id: UUID) -> list[Row]:
    """The product's status changes, by change day."""
    return fetch_all(
        conn,
        "SELECT * FROM product_status_change WHERE product_id = %s ORDER BY change_date",
        (product_id,),
    )


def change_status(
    conn: psycopg.Connection,
    product_id: UUID,
    *,
    change_date: date,
    status: str,
    changed_by_item_id: UUID,
    applied_at: datetime | None = None,
) -> Row:
    """The product is `status` (suspended or active) from `change_date` on.
    `applied_at`: the change takes effect now - the product's status is set here; else
    the worker's run of `change_date` sets it (`apply_status_changes`)."""
    change = returning(
        conn,
        """
        INSERT INTO product_status_change
            (product_id, change_date, status, changed_by_item_id, applied_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (product_id, change_date, status, changed_by_item_id, applied_at),
    )
    if applied_at is not None:
        set_product_status(conn, product_id, status)
    return change


def pending_status_change(conn: psycopg.Connection, product_id: UUID) -> Row | None:
    """The product's status change that has not taken effect, if any."""
    return fetch_one(
        conn,
        "SELECT * FROM product_status_change WHERE product_id = %s AND applied_at IS NULL",
        (product_id,),
    )


def status_after_changes(conn: psycopg.Connection, product_id: UUID) -> str:
    """What the product's status changes leave it in: the latest one's status, else
    active (a product is active unless suspended)."""
    row = fetch_one(
        conn,
        "SELECT status FROM product_status_change WHERE product_id = %s"
        " ORDER BY change_date DESC LIMIT 1",
        (product_id,),
    )
    return row["status"] if row else "active"


def undo_status_change(conn: psycopg.Connection, changed_by_item_id: UUID) -> Row:
    """Undo a status change that has not taken effect (its order is cancelled)."""
    return returning(
        conn,
        "DELETE FROM product_status_change WHERE changed_by_item_id = %s AND applied_at IS NULL"
        " RETURNING *",
        (changed_by_item_id,),
    )


def apply_status_changes(
    conn: psycopg.Connection, run_date: date, *, visible_before: datetime, at: datetime
) -> list[UUID]:
    """The status changes in effect on `run_date` that the run sees (their order
    completed before `visible_before`) and that have not taken effect: the product's
    status is set, the change marked applied. Products are locked in id order first, as
    `flip_statuses` does. Idempotent. Returns the products changed (for their events)."""
    params = {"day": run_date, "before": visible_before, "at": at}
    due = fetch_all(
        conn,
        """
        SELECT id FROM product WHERE id IN (
            SELECT c.product_id FROM product_status_change c
              JOIN product_order_item i ON i.id = c.changed_by_item_id
              JOIN product_order o ON o.id = i.product_order_id
             WHERE c.applied_at IS NULL AND c.change_date <= %(day)s
               AND o.completion_date < %(before)s)
         ORDER BY id FOR UPDATE
        """,
        params,
    )
    if not due:
        return []
    rows = fetch_all(
        conn,
        """
        WITH applied AS (
            UPDATE product_status_change c SET applied_at = %(at)s
             WHERE c.product_id = ANY(%(ids)s) AND c.applied_at IS NULL
               AND c.change_date <= %(day)s
            RETURNING c.product_id, c.status)
        UPDATE product p SET status = a.status FROM applied a
         WHERE p.id = a.product_id
        RETURNING p.id
        """,
        {**params, "ids": [row["id"] for row in due]},
    )
    return sorted(row["id"] for row in rows)


def offering_on(conn: psycopg.Connection, product_id: UUID, day: date) -> UUID:
    """The product's offering on `day` (the latest change on or before it, else the
    initial one)."""
    row = fetch_one(conn, "SELECT product_offering_on(%s, %s) AS id", (product_id, day))
    assert row is not None and row["id"] is not None
    return row["id"]
