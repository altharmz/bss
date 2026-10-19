"""Append-only completed payouts and their payment/credit-note sources."""

from app.repository._sql import fetch_one, jsonb, returning, select_page


def get(conn, uid):
    return fetch_one(conn, "SELECT * FROM refund WHERE id = %s", (uid,))


def page(conn, **kwargs):
    return select_page(conn, "refund", **kwargs)


def insert(conn, account_id, data, now, details):
    """`amount` is the total paid out, `tax_amount` the part of it the client called tax,
    `details` its documents (a refund is immutable once written, so all go into the
    INSERT)."""
    return returning(
        conn,
        """
        INSERT INTO refund (billing_account_id, currency, amount, tax_amount, refunded_at,
                            method_name, method_type, description, payment_id, details)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """,
        (
            account_id,
            data.total_amount.unit,
            data.total_amount.value,
            data.tax,
            now,
            data.payment_method.name,
            data.payment_method.at_type,
            data.description,
            data.payment.id if data.payment else None,
            jsonb(details),
        ),
    )


def consume(conn, refund, payment_id, credit_bill_id, amount):
    conn.execute(
        """
        INSERT INTO refund_source
            (refund_id, billing_account_id, payment_id, credit_bill_id, amount)
        VALUES (%s,%s,%s,%s,%s)
    """,
        (refund["id"], refund["billing_account_id"], payment_id, credit_bill_id, amount),
    )
