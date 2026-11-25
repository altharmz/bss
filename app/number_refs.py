"""Find and remove subscriber-number references no claim backs (#477).

Before #477 an order for an offering whose specification lists no msisdn/imsi, or a product
PATCH, could store a `realizingResource` naming a subscriber number or IMSI without claiming
it: the Products list showed the digits, the number stayed free in the inventory, and the card,
the billing account list and its search (which read claims) found nothing.

    python -m app.number_refs            # report only
    python -m app.number_refs --apply    # remove those references (other documents stay)

A reference is unbacked when the product holds no claim (live or not) on that number. Each
removal is an ordinary product change: the TMF637 attribute-value-change event is emitted.
"""

import argparse
import json
from datetime import date
from typing import Any

import psycopg

from app import db
from app.clock import SystemClock
from app.events import emit
from app.repository import products
from app.repository._sql import fetch_all

UNBACKED = """
SELECT p.id AS product_id, p.billing_account_id, p.status, (ref ->> 'id') AS number_id, n.value,
       n.resource_specification_id AS kind
FROM product p
CROSS JOIN LATERAL jsonb_array_elements(
    CASE jsonb_typeof(p.details -> 'realizingResource')
    WHEN 'array' THEN p.details -> 'realizingResource' ELSE '[]'::jsonb END
) AS ref
JOIN subscriber_number n ON n.id::text = ref ->> 'id'
WHERE NOT EXISTS (
    SELECT FROM number_assignment na
    WHERE na.product_id = p.id AND na.subscriber_number_id = n.id AND na.cancelled_at IS NULL
)
ORDER BY p.id, n.value
"""


def unbacked(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Each product's stored number reference that no claim of that product backs."""
    return [dict(row) for row in fetch_all(conn, UNBACKED, {})]


def remove(conn: psycopg.Connection, found: list[dict[str, Any]], now, today: date) -> int:
    """Drop the unbacked references from each product's documents; returns the products."""
    from app.api.inventory import API, product_body  # the TMF637 body, as a PATCH emits it

    by_product: dict[Any, set[str]] = {}
    for row in found:
        by_product.setdefault(row["product_id"], set()).add(row["number_id"])
    for product_id, number_ids in by_product.items():
        current = products.get_product(conn, product_id)
        assert current is not None
        before = product_body(conn, current, today)
        old = emit.before(conn, API, "product", lambda before=before: before)
        details = dict(current["details"])
        kept = [r for r in details.get("realizingResource") or [] if r.get("id") not in number_ids]
        if kept:
            details["realizingResource"] = kept
        else:
            details.pop("realizingResource", None)
        row = products.describe(conn, product_id, name=current["name"], details=details)
        emit.changed(conn, API, "product", old, product_body(conn, row, today), at=now)
    return len(by_product)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="remove the references found")
    args = parser.parse_args()
    clock = SystemClock()
    with db.connection() as conn, conn.transaction():
        found = unbacked(conn)
        for row in found:
            print(json.dumps({k: str(v) for k, v in row.items()}))
        if args.apply and found:
            changed = remove(conn, found, clock.now(), clock.today())
            print(f"removed {len(found)} reference(s) from {changed} product(s)")
        elif found:
            print(f"{len(found)} unbacked reference(s); run with --apply to remove them")
        else:
            print("no unbacked number references")


if __name__ == "__main__":
    main()
