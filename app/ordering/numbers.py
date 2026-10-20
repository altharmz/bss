"""Order-owned effective-dated MSISDN claims. Caller locks products before numbers."""

from uuid import UUID

from app.config import settings
from app.events import emit
from app.repository import catalog
from app.repository._sql import fetch_all, fetch_one


def required(details):
    return any(r.get("id") == "msisdn" for r in details.get("resourceSpecification", []))


def offering_requires(conn, offering_id):
    offering = catalog.get_product_offering(conn, offering_id)
    uid = offering["product_specification_id"]
    spec = catalog.lock_product_specification(conn, uid, share=True) if uid else None
    return bool(spec and required(spec["details"]))


def references(item):
    if item.new_product is not None:
        return (item.new_product.details or {}).get("realizingResource")
    return (item.details or {}).get("product", {}).get("realizingResource")


def claims(conn, product_ids):
    return fetch_all(
        conn,
        """SELECT * FROM number_assignment WHERE product_id = ANY(%s)
        AND cancelled_at IS NULL ORDER BY effective_from, audit_sequence""",
        (list(product_ids),),
    )


def lock(conn, product_ids, requested=()):
    ids = {r["subscriber_number_id"] for r in claims(conn, product_ids)}
    for refs in requested:
        for reference in refs or []:
            try:
                ids.add(UUID(reference.get("id", "")))
            except (ValueError, TypeError, AttributeError):
                pass
    return {
        r["id"]: r
        for r in fetch_all(
            conn,
            "SELECT * FROM subscriber_number WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (sorted(ids),),
        )
    }


def unavailable(conn, uid, start, end, *, exclude_product=None):
    return (
        fetch_one(
            conn,
            """SELECT id FROM number_assignment
        WHERE subscriber_number_id = %(uid)s AND cancelled_at IS NULL
          AND NOT isempty(valid_for) AND product_id IS DISTINCT FROM %(product)s::uuid
          AND (valid_for && daterange(%(start)s, %(end)s, '[)')
            OR (released_on <= %(start)s AND released_on + %(days)s > %(start)s)
            OR (%(end)s::date IS NOT NULL AND effective_from >= %(end)s::date
                AND effective_from < %(end)s::date + %(days)s)) LIMIT 1""",
            {
                "uid": uid,
                "start": start,
                "end": end,
                "product": exclude_product,
                "days": settings.msisdn_quarantine_days,
            },
        )
        is not None
    )


def validate(conn, request, products, locked, start):
    problems = []
    seen = set()
    for item in request.items:
        product = products.get(item.product_id)
        needs = (
            offering_requires(conn, item.product_offering_id)
            if item.action == "add"
            else bool(product and product.get("requires_msisdn"))
        )
        refs = references(item)
        if not needs and item.action == "modify" and refs is not None:
            problems.append(
                (
                    item.item_id,
                    "numberNotRequired",
                    "edit this product's unchecked resource documents through product inventory",
                )
            )
            continue
        if not needs or item.action not in ("add", "modify"):
            continue
        if refs is None and item.action == "modify":
            continue
        if not refs or len(refs) != 1:
            problems.append(
                (item.item_id, "numberRequired", "exactly one realizingResource is required")
            )
            continue
        try:
            uid = UUID(refs[0].get("id", ""))
        except (ValueError, TypeError, AttributeError):
            uid = None
        number = locked.get(uid)
        end = product["termination_date"] if product else None
        if number is None:
            reason = "subscriber number does not exist"
        elif number["administrative_state"] != "unlocked":
            reason = "subscriber number is locked"
        elif uid in seen or unavailable(conn, uid, start, end):
            reason = "subscriber number is assigned, reserved or quarantined"
        elif product and any(c["effective_from"] > start for c in claims(conn, [product["id"]])):
            reason = "number changes must follow existing assignments"
        elif product and any(
            c["subscriber_number_id"] == uid and start in c["valid_for"]
            for c in claims(conn, [product["id"]])
        ):
            reason = "the product already uses this number"
        else:
            seen.add(uid)
            continue
        problems.append((item.item_id, "numberUnavailable", reason))
    return problems


def before(conn, locked, today):
    from app.api.resource_inventory import resource_body

    return {
        uid: emit.before(
            conn, "TMF639", "resource", lambda row=row: resource_body(conn, row, today)
        )
        for uid, row in locked.items()
    }


def changed(conn, locked, old, now, today):
    from app.api.resource_inventory import resource_body

    for uid, row in locked.items():
        emit.changed(
            conn,
            "TMF639",
            "resource",
            old[uid],
            resource_body(conn, row, today),
            at=now,
            state="usageState",
        )


def execute(conn, item, product_id, item_id, start, now):
    product = fetch_one(conn, "SELECT * FROM product WHERE id = %s", (product_id,))
    if item.action == "add":
        needs = offering_requires(conn, item.product_offering_id)
        conn.execute("UPDATE product SET requires_msisdn = %s WHERE id = %s", (needs, product_id))
    else:
        needs = product["requires_msisdn"]
    if not needs:
        return
    refs = references(item)
    if item.action == "delete":
        end = max(start, product["start_date"])
    elif item.action == "modify" and refs is not None:
        end = start
    elif item.action == "add":
        end = None
    else:
        return
    if item.action != "add":
        conn.execute(
            """UPDATE number_assignment SET released_on = %s, ended_by_item_id = %s, updated_at = %s
            WHERE product_id = %s AND cancelled_at IS NULL AND
            (released_on IS NULL OR released_on > %s)""",
            (end, item_id, now, product_id, end),
        )
    if item.action in ("add", "modify"):
        conn.execute(
            """INSERT INTO number_assignment (subscriber_number_id, product_id, assigned_by_item_id,
            effective_from, released_on, ended_by_item_id, quarantine_days, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                UUID(refs[0]["id"]),
                product_id,
                item_id,
                start,
                product["termination_date"],
                product["terminated_by_item_id"],
                settings.msisdn_quarantine_days,
                now,
                now,
            ),
        )


def cancellation_problem(conn, items):
    item_ids = {i["id"] for i in items}
    for claim in claims(conn, [i["product_id"] for i in items if i["product_id"]]):
        if claim["ended_by_item_id"] in item_ids and claim["assigned_by_item_id"] not in item_ids:
            if fetch_one(
                conn,
                """SELECT id FROM number_assignment
                WHERE subscriber_number_id = %s AND product_id <> %s
                  AND cancelled_at IS NULL AND valid_for && daterange(%s, NULL, '[)')
                LIMIT 1""",
                (claim["subscriber_number_id"], claim["product_id"], claim["released_on"]),
            ):
                return "restoring the number assignment conflicts with another accepted claim"
    return None


def cancel(conn, items, now):
    for item in items:
        conn.execute(
            """UPDATE number_assignment SET cancelled_at = %s,
            cancelled_by_item_id = %s, updated_at = %s
            WHERE assigned_by_item_id = %s AND cancelled_at IS NULL""",
            (now, item["id"], now, item["id"]),
        )
    for item in items:
        conn.execute(
            """UPDATE number_assignment SET released_on = NULL,
            ended_by_item_id = NULL, updated_at = %s
            WHERE ended_by_item_id = %s AND cancelled_at IS NULL""",
            (now, item["id"]),
        )
