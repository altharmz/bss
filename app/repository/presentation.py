"""TMF666 preferences and issued bill document rendering."""

import base64
from uuid import UUID

from psycopg import sql

from app.repository._sql import (
    Lock,
    Row,
    fetch_all,
    fetch_one,
    returning,
    select_by_id,
    select_page,
    update_by_id,
)


def get(conn, table, uid, lock: Lock = None):
    return select_by_id(conn, table, uid, lock)


def page(conn, table, **kwargs):
    return select_page(conn, table, **kwargs)


def create(conn, table, data, new_id=None):
    return returning(
        conn,
        sql.SQL(
            "INSERT INTO {} (id, name, description)"
            " VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s) RETURNING *"
        ).format(sql.Identifier(table)),
        (new_id, data.name, data.description),
    )


def update(conn, table, uid, changes):
    return update_by_id(conn, table, uid, changes)


def preferences(conn, account_id):
    return {
        "format": fetch_one(
            conn,
            "SELECT f.id, f.name, f.description FROM bill_format f"
            " JOIN billing_account a ON a.bill_format_id = f.id WHERE a.id = %s",
            (account_id,),
        ),
        "media": fetch_all(
            conn,
            "SELECT m.id, m.name, m.description FROM bill_presentation_media m"
            " JOIN account_presentation_media a ON a.media_id = m.id"
            " WHERE a.billing_account_id = %s ORDER BY a.created_at, m.id",
            (account_id,),
        ),
    }


def preferences_of(conn, account_ids):
    """preferences() for a page of accounts in two statements (#285)."""
    if not account_ids:
        return {}
    ids = list(set(account_ids))
    formats = {
        row["account_id"]: {k: row[k] for k in ("id", "name", "description")}
        for row in fetch_all(
            conn,
            "SELECT a.id AS account_id, f.id, f.name, f.description FROM bill_format f"
            " JOIN billing_account a ON a.bill_format_id = f.id WHERE a.id = ANY(%s)",
            (ids,),
        )
    }
    media: dict = {uid: [] for uid in account_ids}
    for row in fetch_all(
        conn,
        "SELECT a.billing_account_id, m.id, m.name, m.description FROM bill_presentation_media m"
        " JOIN account_presentation_media a ON a.media_id = m.id"
        " WHERE a.billing_account_id = ANY(%s) ORDER BY a.created_at, m.id",
        (ids,),
    ):
        media[row["billing_account_id"]].append({k: row[k] for k in ("id", "name", "description")})
    return {uid: {"format": formats.get(uid), "media": media[uid]} for uid in account_ids}


def set_preferences(conn, uid, data):
    from app.api.errors import unprocessable

    # Referenced formats, media and cycle specifications FOR KEY SHARE until commit: their
    # delete waits for this
    if "cycle_specification" in data.model_fields_set:
        spec = data.cycle_specification
        spec_id = None
        if spec is not None and spec.id != "monthly":
            try:
                spec_id = UUID(spec.id)
            except ValueError:
                raise unprocessable("billStructure.cycleSpecification does not exist") from None
            if get(conn, "billing_cycle_specification", spec_id, "key share") is None:
                raise unprocessable("billStructure.cycleSpecification does not exist")
        conn.execute(
            "UPDATE billing_account SET cycle_specification_id = %s WHERE id = %s",
            (spec_id, uid),
        )
    if "format" in data.model_fields_set:
        if (
            data.format is not None
            and get(conn, "bill_format", data.format.id, "key share") is None
        ):
            raise unprocessable("billStructure.format does not exist")
        conn.execute(
            "UPDATE billing_account SET bill_format_id = %s WHERE id = %s",
            (data.format.id if data.format else None, uid),
        )
    if "presentation_media" in data.model_fields_set:
        ids = [r.id for r in data.presentation_media or []]
        if len(set(ids)) != len(ids):
            raise unprocessable("duplicate presentationMedia references")
        for mid in ids:
            if get(conn, "bill_presentation_media", mid, "key share") is None:
                raise unprocessable("presentationMedia does not exist")
        conn.execute("DELETE FROM account_presentation_media WHERE billing_account_id = %s", (uid,))
        for mid in ids:
            conn.execute(
                "INSERT INTO account_presentation_media (billing_account_id, media_id)"
                " VALUES (%s,%s)",
                (uid, mid),
            )
        conn.execute("UPDATE billing_account SET name = name WHERE id = %s", (uid,))


def remove_preferences(conn, account_id):
    """Drop an account's presentation media choices (before the account is deleted)."""
    conn.execute(
        "DELETE FROM account_presentation_media WHERE billing_account_id = %s", (account_id,)
    )


def _charge_name(abr: Row) -> str:
    """The bill document's line name: "Plan X (multi-line discount −$5.00)" when a tier
    applied."""
    from app.billing import multi_line
    from app.repository import billing

    name = abr["offering_name"] or billing.FEE_NAME
    if abr.get("discount_price_id") is None:
        return name
    return multi_line.label(name, abr["discount_amount"], abr["currency"])


def snapshot_bill(conn, bill_id, *, now):
    from app.api import tmfjson
    from app.repository import billing

    row = billing.get_bill(conn, bill_id)
    if row["bill_document"] is not None:
        return
    pref = preferences(conn, row["billing_account_id"])
    fmt = pref["format"]
    net, tax_lines = billing.bill_taxes(conn, bill_id)
    document = {
        "id": str(row["id"]),
        "billingAccountId": str(row["billing_account_id"]),
        "billDate": row["bill_date"],
        "issuedAt": now,
        "billingPeriod": {"start": row["bill_period"].lower, "end": row["bill_period"].upper},
        "paymentDueDate": row["payment_due_date"],
        "amountDue": {"unit": row["currency"], "value": row["amount_due"]},
        "taxExcludedAmount": {"unit": row["currency"], "value": net},
        "taxItem": [
            {
                "taxCategory": t["category"],
                "taxRate": t["rate"],
                "taxAmount": {"unit": row["currency"], "value": t["amount"]},
            }
            for t in tax_lines
        ],
        "format": {"name": fmt["name"], "description": fmt["description"]}
        if fmt
        else {"name": "JSON detailed"},
        "presentationMedia": [
            {"name": m["name"], "description": m["description"]} for m in pref["media"]
        ],
    }
    if fmt is None or fmt["name"] == "JSON detailed":
        document["charges"] = [
            {
                "id": str(a["id"]),
                "name": _charge_name(a),
                "amount": {"unit": a["currency"], "value": a["amount"]},
                "taxIncludedAmount": {"unit": a["currency"], "value": billing.abr_total(a)},
                "period": {"start": a["period"].lower, "end": a["period"].upper},
            }
            for a in billing.bill_charges(conn, bill_id)
        ]
    content = base64.b64encode(tmfjson.dumps(document).encode()).decode()
    conn.execute(
        "UPDATE customer_bill SET bill_document = %s WHERE id = %s AND bill_document IS NULL",
        (content, bill_id),
    )
