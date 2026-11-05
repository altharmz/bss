"""Order-owned effective-dated MSISDN claims. Caller locks products before numbers."""

from uuid import UUID

from app.config import settings
from app.events import emit
from app.repository import catalog
from app.repository import sims as sim_stock
from app.repository._sql import fetch_all, fetch_one

KINDS = ["msisdn", "imsi"]
# stored order rejection codes per kind
CODES = {
    "msisdn": ("numberRequired", "numberUnavailable", "numberNotRequired"),
    "imsi": ("imsiRequired", "imsiUnavailable", "imsiNotRequired"),
}
NOUN = {"msisdn": "subscriber number", "imsi": "IMSI"}


def required(details, kind="msisdn"):
    return any(r.get("id") == kind for r in details.get("resourceSpecification", []))


def offering_requires(conn, offering_id, kind="msisdn"):
    offering = catalog.get_product_offering(conn, offering_id)
    uid = offering["product_specification_id"]
    spec = catalog.lock_product_specification(conn, uid, share=True) if uid else None
    return bool(spec and required(spec["details"], kind))


def requirements(conn, offering_id):
    """{kind: bool} for the offering's specification."""
    offering = catalog.get_product_offering(conn, offering_id)
    uid = offering["product_specification_id"]
    spec = catalog.lock_product_specification(conn, uid, share=True) if uid else None
    return {k: bool(spec and required(spec["details"], k)) for k in KINDS}


def product_requirements(product):
    return {k: bool(product and product.get(f"requires_{k}")) for k in KINDS}


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


def unavailable(conn, uid, start, end, *, exclude_product=None, kind="msisdn"):
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
                "days": settings.msisdn_quarantine_days if kind == "msisdn" else 0,
            },
        )
        is not None
    )


def _uuid(reference):
    try:
        return UUID(reference.get("id", ""))
    except (ValueError, TypeError, AttributeError):
        return None


def validate(conn, request, products, locked, start):
    problems = []
    seen = set()
    for item in request.items:
        product = products.get(item.product_id)
        if item.action == "add":
            needs = requirements(conn, item.product_offering_id)
        else:
            needs = product_requirements(product)
        refs = references(item)
        if not any(needs.values()) and item.action == "modify" and refs is not None:
            problems.append(
                (
                    item.item_id,
                    "numberNotRequired",
                    "edit this product's unchecked resource documents through product inventory",
                )
            )
            continue
        if not any(needs.values()) or item.action not in ("add", "modify"):
            continue
        if refs is None and item.action == "modify":
            continue
        problems += _item_problems(conn, item, product, needs, refs or [], locked, start, seen)
    return problems


def _item_problems(conn, item, product, needs, refs, locked, start, seen):
    by_kind = {}
    unknown = []
    for ref in refs:
        row = locked.get(_uuid(ref))
        if row is None:
            unknown.append(ref)
        else:
            by_kind.setdefault(row["resource_specification_id"], []).append(row)
    problems = []
    for kind in KINDS:
        if len(by_kind.get(kind, [])) > 1:
            problems.append(
                (
                    item.item_id,
                    "realizingResourceDuplicate",
                    f"at most one {kind} realizingResource is allowed",
                )
            )
    if problems:
        return problems
    # a reference the inventory does not know fills the first required kind still missing
    for kind in KINDS:
        if needs[kind] and kind not in by_kind and unknown:
            by_kind[kind] = [(unknown.pop(0))]
    end = product["termination_date"] if product else None
    current = {}
    if product:
        for c in claims(conn, [product["id"]]):
            if start in c["valid_for"]:
                current[c["resource_specification_id"]] = c["subscriber_number_id"]
    changed = 0
    for kind in KINDS:
        required_code, unavailable_code, extra_code = CODES[kind]
        found = by_kind.get(kind, [])
        if not needs[kind]:
            if found:
                problems.append((item.item_id, extra_code, f"the product needs no {kind}"))
            continue
        if not found:
            problems.append(
                (
                    item.item_id,
                    required_code,
                    f"exactly one {kind} realizingResource is required",
                )
            )
            continue
        row = found[0]
        if "resource_specification_id" not in row:
            problems.append((item.item_id, unavailable_code, f"{NOUN[kind]} does not exist"))
            continue
        uid = row["id"]
        if item.action == "modify" and current.get(kind) == uid:
            continue  # unchanged member of the complete resulting set
        changed += 1
        if row["administrative_state"] != "unlocked":
            reason = f"{NOUN[kind]} is locked"
        elif kind == "imsi" and row["sim_state"] != "in_stock":
            reason = f"IMSI is {row['sim_state']}, not in stock"
        elif uid in seen or unavailable(conn, uid, start, end, kind=kind):
            reason = f"{NOUN[kind]} is assigned, reserved or quarantined"
        elif product and any(
            c["effective_from"] > start and c["resource_specification_id"] == kind
            for c in claims(conn, [product["id"]])
        ):
            reason = f"{kind} changes must follow existing assignments"
        else:
            seen.add(uid)
            continue
        problems.append((item.item_id, unavailable_code, reason))
    if item.action == "modify" and not changed and not problems:
        kind = next(k for k in KINDS if needs[k])
        problems.append(
            (item.item_id, CODES[kind][1], f"the product already uses this {NOUN[kind]}")
        )
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

    for uid in locked:
        # re-read: an accepted release may have locked the IMSI in this transaction
        row = fetch_one(conn, "SELECT * FROM subscriber_number WHERE id = %s", (uid,))
        emit.changed(
            conn,
            "TMF639",
            "resource",
            old[uid],
            resource_body(conn, row, today),
            at=now,
            state="usageState",
        )


def _release_imsi(conn, product_id, end, now):
    """An accepted release locks the IMSI so it is not reused unless an operator unlocks it."""
    conn.execute(
        """UPDATE subscriber_number SET administrative_state = 'locked',
        sim_state = 'retired', updated_at = %s
        WHERE resource_specification_id = 'imsi'
        AND (administrative_state <> 'locked' OR sim_state <> 'retired')
        AND id IN (SELECT subscriber_number_id FROM number_assignment
            WHERE product_id = %s AND cancelled_at IS NULL AND resource_specification_id = 'imsi'
            AND (released_on IS NULL OR released_on > %s))""",
        (now, product_id, end),
    )


def execute(conn, item, product_id, item_id, start, now):
    product = fetch_one(conn, "SELECT * FROM product WHERE id = %s", (product_id,))
    if item.action == "add":
        needs = requirements(conn, item.product_offering_id)
        conn.execute(
            "UPDATE product SET requires_msisdn = %s, requires_imsi = %s WHERE id = %s",
            (needs["msisdn"], needs["imsi"], product_id),
        )
    else:
        needs = product_requirements(product)
    if not any(needs.values()):
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
    chosen = {}
    for ref in refs or []:
        kind = fetch_one(
            conn,
            "SELECT resource_specification_id FROM subscriber_number WHERE id = %s",
            (UUID(ref["id"]),),
        )["resource_specification_id"]
        chosen[kind] = UUID(ref["id"])
    current = {}
    if item.action == "modify":
        for c in claims(conn, [product_id]):
            if start in c["valid_for"]:
                current[c["resource_specification_id"]] = c["subscriber_number_id"]
    for kind in KINDS:
        if item.action == "delete":
            pass
        elif item.action == "modify" and (kind not in chosen or current.get(kind) == chosen[kind]):
            continue
        if item.action != "add":
            if kind == "imsi":
                _release_imsi(conn, product_id, end, now)
            conn.execute(
                """UPDATE number_assignment SET released_on = %s, ended_by_item_id = %s,
                updated_at = %s WHERE product_id = %s AND cancelled_at IS NULL
                AND resource_specification_id = %s AND (released_on IS NULL OR released_on > %s)""",
                (end, item_id, now, product_id, kind, end),
            )
        if item.action in ("add", "modify") and kind in chosen:
            conn.execute(
                """INSERT INTO number_assignment (subscriber_number_id, resource_specification_id,
                product_id, assigned_by_item_id, effective_from, released_on, ended_by_item_id,
                quarantine_days, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    chosen[kind],
                    kind,
                    product_id,
                    item_id,
                    start,
                    product["termination_date"],
                    product["terminated_by_item_id"],
                    settings.msisdn_quarantine_days if kind == "msisdn" else 0,
                    now,
                    now,
                ),
            )
            if kind == "imsi":
                sim_stock.hold(conn, chosen[kind], product_id, "active", now)


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
        for claim in fetch_all(
            conn,
            """SELECT subscriber_number_id FROM number_assignment
            WHERE assigned_by_item_id = %s AND cancelled_at IS NULL
            AND resource_specification_id = 'imsi'""",
            (item["id"],),
        ):
            sim_stock.release_hold(conn, claim["subscriber_number_id"], now)
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


# --- SIM and eSIM: picking from stock, swap targets (docs/decisions/sim.md) ---


def sim_type_of(item):
    """The `simType` product characteristic of an add item, or None."""
    for entry in ((item.details or {}).get("product") or {}).get("productCharacteristic") or []:
        if entry.get("name") == "simType":
            return entry.get("value")
    return None


def _kinds(conn, refs):
    ids = [i for i in (_uuid(r) for r in refs or []) if i is not None]
    return {
        r["id"]: r["resource_specification_id"]
        for r in fetch_all(
            conn,
            "SELECT id, resource_specification_id FROM subscriber_number WHERE id = ANY(%s)",
            (ids,),
        )
    }


def pick_sims(conn, request):
    """Choose the SIM of each add item that names a `simType` instead of an IMSI: the
    oldest unlocked one in stock, added to the item's realizingResource. Returns the
    problems (an offering without an IMSI, or none in stock)."""
    problems, taken = [], []
    for item in request.items:
        wanted = sim_type_of(item)
        if wanted is None or item.action != "add":
            continue
        if not requirements(conn, item.product_offering_id)["imsi"]:
            problems.append(
                (item.item_id, "imsiNotRequired", "the product needs no IMSI, so simType is unused")
            )
            continue
        refs = references(item) or []
        if "imsi" in _kinds(conn, refs).values():
            problems.append(
                (
                    item.item_id,
                    "realizingResourceDuplicate",
                    "name an IMSI or a simType, not both",
                )
            )
            continue
        sim = sim_stock.pick_in_stock(conn, wanted, exclude=taken)
        if sim is None:
            problems.append((item.item_id, "simUnavailable", f"no {wanted} SIM in stock"))
            continue
        taken.append(sim["id"])
        reference = {
            "id": str(sim["id"]),
            "@type": "ResourceRef",
            "@referredType": "LogicalResource",
        }
        product = item.details.setdefault("product", {})
        product["realizingResource"] = [*refs, reference]
    return problems


def changes(conn, item, product, start):
    """{kind: new resource id} the item's realizingResource changes on the product."""
    refs = references(item)
    if not refs or product is None:
        return {}
    current = {}
    for c in claims(conn, [product["id"]]):
        if start in c["valid_for"]:
            current[c["resource_specification_id"]] = c["subscriber_number_id"]
    found = {}
    for uid, kind in _kinds(conn, refs).items():
        if current.get(kind) != uid:
            found[kind] = uid
    return found
