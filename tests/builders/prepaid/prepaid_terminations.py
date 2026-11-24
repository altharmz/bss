"""Builders other test modules share, from tests/prepaid/test_prepaid_terminations.py (#423)."""

from tests import apidata
from tests.apidata import ORDER


def terminate(api, acc, *products, **over):
    items = [apidata.delete_item(p["id"], str(i + 1)) for i, p in enumerate(products)]
    response = api.post(ORDER, apidata.order_body(acc, *items, **over))
    assert response.status_code == 201, response.text
    return response.json()


def credits(conn):
    return conn.execute(
        "SELECT a.product_id::text, a.amount, a.tax_amount, lower(a.period), upper(a.period),"
        " b.amount_due, b.state, b.category, b.run_type"
        " FROM applied_customer_billing_rate a JOIN customer_bill b ON b.id = a.customer_bill_id"
        " WHERE a.charge_type = 'refund' ORDER BY a.product_id"
    ).fetchall()
