"""What happened to one product (#458): its status changes, offering changes and number and
SIM assignments as one list, newest first (docs/decisions/frontend.md, "Product page").

Read-only: the three tables are written by orders and the worker. Each branch reads its
table by product id (an index each), so a page costs one statement whatever the history's
length. Cancelled assignment versions are audit of a voided order, not history, and are
left out; a cancelled order deletes its status and offering changes.
"""

from uuid import UUID

import psycopg

from app.repository._sql import Row, fetch_all

HISTORY = """
SELECT * FROM (
  SELECT 'statusChange' AS kind, c.change_date AS day, NULL::date AS end_day,
         c.status::text AS status, c.cause, NULL::uuid AS offering_id,
         NULL::text AS offering_name, NULL::uuid AS number_id, NULL::text AS number_kind,
         NULL::text AS number_value, NULL::text AS iccid, i.product_order_id,
         c.created_at AS at, c.id
    FROM product_status_change c
    JOIN product_order_item i ON i.id = c.changed_by_item_id
   WHERE c.product_id = %(product)s
  UNION ALL
  SELECT 'offeringChange', o.change_date, NULL, NULL, NULL, o.product_offering_id, po.name,
         NULL, NULL, NULL, NULL, i.product_order_id, o.created_at, o.id
    FROM product_offering_change o
    JOIN product_offering po ON po.id = o.product_offering_id
    JOIN product_order_item i ON i.id = o.changed_by_item_id
   WHERE o.product_id = %(product)s
  UNION ALL
  SELECT 'numberAssignment', a.effective_from, a.released_on, NULL, NULL, NULL, NULL,
         a.subscriber_number_id, a.resource_specification_id, n.value, n.iccid,
         i.product_order_id, a.created_at, a.id
    FROM number_assignment a
    JOIN subscriber_number n ON n.id = a.subscriber_number_id
    JOIN product_order_item i ON i.id = a.assigned_by_item_id
   WHERE a.product_id = %(product)s AND a.cancelled_at IS NULL
) h
ORDER BY day DESC, at DESC, id DESC
OFFSET %(offset)s LIMIT %(limit)s
"""


def history(conn: psycopg.Connection, product_id: UUID, offset: int, limit: int) -> list[Row]:
    """`limit` entries from `offset`, newest effective date first (ties: newest recorded)."""
    return fetch_all(conn, HISTORY, {"product": product_id, "offset": offset, "limit": limit})
