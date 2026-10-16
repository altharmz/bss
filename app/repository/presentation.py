"""TMF666 preferences and issued bill document rendering."""

import base64

from psycopg import sql

from app.repository._sql import fetch_all, fetch_one, returning, select_page, update_by_id


def get(conn, table, uid):
    return fetch_one(
        conn, sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table)), (uid,)
    )


def page(conn, table, **kwargs):
    return select_page(conn, table, **kwargs)


def create(conn, table, data):
    return returning(
        conn,
        sql.SQL("INSERT INTO {} (name, description) VALUES (%s,%s) RETURNING *").format(
            sql.Identifier(table)
        ),
        (data.name, data.description),
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


def set_preferences(conn, uid, data):
    from app.api.errors import unprocessable

    if "format" in data.model_fields_set:
        if data.format is not None and get(conn, "bill_format", data.format.id) is None:
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
            if get(conn, "bill_presentation_media", mid) is None:
                raise unprocessable("presentationMedia does not exist")
        conn.execute("DELETE FROM account_presentation_media WHERE billing_account_id = %s", (uid,))
        for mid in ids:
            conn.execute(
                "INSERT INTO account_presentation_media (billing_account_id, media_id)"
                " VALUES (%s,%s)",
                (uid, mid),
            )
        conn.execute("UPDATE billing_account SET name = name WHERE id = %s", (uid,))


def snapshot_bill(conn, bill_id, *, now):
    from app.api import tmfjson
    from app.repository import billing

    row = billing.get_bill(conn, bill_id)
    if row["bill_document"] is not None:
        return
    pref = preferences(conn, row["billing_account_id"])
    fmt = pref["format"]
    document = {
        "id": str(row["id"]),
        "billingAccountId": str(row["billing_account_id"]),
        "billDate": row["bill_date"],
        "issuedAt": now,
        "billingPeriod": {"start": row["bill_period"].lower, "end": row["bill_period"].upper},
        "paymentDueDate": row["payment_due_date"],
        "amountDue": {"unit": row["currency"], "value": row["amount_due"]},
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
                "name": a["offering_name"],
                "amount": {"unit": a["currency"], "value": a["amount"]},
                "period": {"start": a["period"].lower, "end": a["period"].upper},
            }
            for a in billing.bill_charges(conn, bill_id)
        ]
    content = base64.b64encode(tmfjson.dumps(document).encode()).decode()
    conn.execute(
        "UPDATE customer_bill SET bill_document = %s WHERE id = %s AND bill_document IS NULL",
        (content, bill_id),
    )
