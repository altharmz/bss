"""TMF list paging (#286): the pages and filters of the tables that grow with subscribers
use indexes, and an enum filter value that is no label answers an empty page without a
query."""

import pytest

from app.repository import _sql
from tests import apidata

PAGED = (
    "party",
    "customer",
    "billing_account",
    "product",
    "product_order",
    "cancel_product_order",
    "customer_bill",
    "payment",
    "refund",
)


def plan(conn, query, params=()) -> str:
    conn.execute("SET enable_seqscan = off")  # an index that can serve it must be chosen
    try:
        return "\n".join(r[0] for r in conn.execute("EXPLAIN " + query, params))
    finally:
        conn.execute("RESET enable_seqscan")


@pytest.mark.parametrize("table", PAGED)
def test_a_page_reads_the_creation_index_without_sorting(db_conn, table):
    text = plan(db_conn, f"SELECT * FROM {table} ORDER BY created_at, id OFFSET 10000 LIMIT 100")  # noqa: S608
    assert f"{table}_created_at_id_idx" in text, text
    assert "Sort" not in text, text


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("party", "given_name", "Ivan"),
        ("party", "family_name", "Ivanov"),
        ("party", "organization_name", "Acme"),
        ("customer", "name", "Ivan"),
        ("billing_account", "name", "Main"),
        ("customer_bill", "billing_account_id", apidata.NIL),
        ("refund", "payment_id", apidata.NIL),
    ],
)
def test_the_filters_have_an_index(db_conn, table, column, value):
    text = plan(db_conn, f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,))  # noqa: S608
    assert "Seq Scan" not in text, text


def test_a_lines_usage_page_reads_its_index_without_sorting(db_conn):
    text = plan(
        db_conn,
        "SELECT * FROM usage_record WHERE product_id = %s ORDER BY event_at DESC, id LIMIT 100",
        (apidata.NIL,),
    )
    assert "usage_record_product_event_idx" in text and "Sort" not in text, text


def test_an_enum_value_that_is_no_label_is_an_empty_page_without_a_query(api, monkeypatch):
    apidata.account(api)
    real = _sql.fetch_one

    def no_count(conn, query, params=None):
        if "count(*)" in str(query):
            raise AssertionError("an unknown enum value must not reach the database")
        return real(conn, query, params)

    monkeypatch.setattr(_sql, "fetch_one", no_count)
    for url in (
        f"{apidata.PRODUCT}?status=bogus",
        f"{apidata.ACCOUNT}?state=Bogus",
        f"{apidata.CUSTOMER}?status=bogus",
        f"{apidata.ORDER}?state=bogus",
    ):
        response = api.get(url)
        assert response.status_code == 200, (url, response.text)
        assert response.json() == [] and response.headers["X-Total-Count"] == "0", url


def test_a_known_enum_value_and_a_text_filter_still_filter(api):
    acc = apidata.account(api, name="Typed filter")
    assert [a["id"] for a in api.get(f"{apidata.ACCOUNT}?state=Active").json()] == [acc["id"]]
    assert api.get(f"{apidata.ACCOUNT}?state=Closed").headers["X-Total-Count"] == "0"
    found = api.get(f"{apidata.ACCOUNT}?name=Typed%20filter")
    assert found.headers["X-Total-Count"] == "1"
