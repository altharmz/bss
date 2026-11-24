"""TMF list paging (#286): the pages and filters of the tables that grow with subscribers
use indexes, and an enum filter value that is no label answers an empty page without a
query."""

import re

import pytest

from app.repository import _sql
from tests import apidata
from tests.apiclient import total
from tests.perf.guard import NO_SEQSCAN, plan

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


@pytest.mark.parametrize("table", PAGED)
def test_a_page_reads_the_creation_index_without_sorting(db_conn, table):
    query = f"SELECT * FROM {table} ORDER BY created_at, id OFFSET 10000 LIMIT 100"  # noqa: S608
    text = plan(db_conn, query, off=NO_SEQSCAN)
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
    query = f"SELECT count(*) FROM {table} WHERE {column} = %s"  # noqa: S608
    text = plan(db_conn, query, (value,), off=NO_SEQSCAN)
    assert "Seq Scan" not in text, text


def test_a_lines_usage_page_reads_its_index_without_sorting(db_conn):
    text = plan(
        db_conn,
        "SELECT * FROM usage_record WHERE product_id = %s ORDER BY event_at DESC, id LIMIT 100",
        (apidata.NIL,),
        off=NO_SEQSCAN,
    )
    # partitioned by month (#282): every partition is read through its copy of
    # usage_record_product_event_idx and the pages merge in order, without a Sort node
    scans = [line for line in text.splitlines() if " Scan " in line]
    assert scans and all("_product_id_event_at_id_idx" in line for line in scans), text
    assert not re.search(r"\bSort  \(", text), text  # a Sort node, not Merge Append's key


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
        assert response.json() == [] and total(response, api) == "0", url


def test_a_known_enum_value_and_a_text_filter_still_filter(api):
    acc = apidata.account(api, name="Typed filter")
    assert [a["id"] for a in api.get(f"{apidata.ACCOUNT}?state=Active").json()] == [acc["id"]]
    assert total(api.get(f"{apidata.ACCOUNT}?state=Closed"), api) == "0"
    found = api.get(f"{apidata.ACCOUNT}?name=Typed%20filter")
    assert total(found, api) == "1"
