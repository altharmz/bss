"""P4b: the Idempotency-Key journal and its wiring to the prepaid order POST."""

import threading
from datetime import UTC, datetime
from decimal import Decimal as D

import psycopg
import pytest

from app.repository import idempotency
from tests import apidata
from tests.apidata import ORDER
from tests.builders.prepaid.idempotency import body
from tests.builders.prepaid.prepaid_orders import shop

NOW = datetime(2026, 10, 23, 9, tzinfo=UTC)
HASH = "a" * 64


def keyed(api, order, key, **kwargs):
    return api.post(ORDER, order, headers={"Idempotency-Key": key}, **kwargs)


def test_same_key_and_body_replays_one_order_and_one_command(api, db_conn):
    off, acc = shop(api, db_conn)
    order = body(api, off, acc)
    first = keyed(api, order, "retry-1")
    again = keyed(api, order, "retry-1")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert again.headers["Location"] == first.headers["Location"]
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)
    assert db_conn.execute("SELECT count(*) FROM prepaid_command").fetchone() == (1,)
    assert db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone() == (1,)


def test_a_different_body_is_a_conflict_and_changes_nothing(api, db_conn):
    off, acc = shop(api, db_conn)
    first = keyed(api, body(api, off, acc), "retry-2")
    other = keyed(api, body(api, off, acc, "2"), "retry-2")
    assert first.status_code == 201
    assert other.status_code == 409
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (1,)


def test_no_key_means_no_journal_and_two_orders(api, db_conn):
    off, acc = shop(api, db_conn, funds="40")
    for serial in "12":
        assert api.post(ORDER, body(api, off, acc, serial)).status_code == 201
    assert db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone() == (0,)


def test_a_failed_request_leaves_no_key_so_the_retry_runs(api, db_conn):
    off, acc = shop(api, db_conn)
    order = body(api, off, acc)
    bad = {**order, "productOrderItem": []}
    assert keyed(api, bad, "retry-3").status_code == 400
    assert db_conn.execute("SELECT count(*) FROM idempotency_key").fetchone() == (0,)
    assert keyed(api, order, "retry-3").status_code == 201


@pytest.mark.parametrize("key", ["", "a b", "x" * 256, "café"])
def test_a_malformed_key_is_a_400(api, db_conn, key):
    off, acc = shop(api, db_conn)
    response = api.post(
        ORDER, body(api, off, acc), headers={"Idempotency-Key": key.encode("utf-8")}
    )
    assert response.status_code == 400


def test_deleting_the_created_order_tombstones_its_key(api, db_conn):
    off, acc = shop(api, db_conn)
    rejected = apidata.order_body(acc, apidata.rejected_item(api, off))
    first = keyed(api, rejected, "retry-4")
    assert first.json()["state"] == "rejected"
    assert api.delete(f"{ORDER}/{first.json()['id']}").status_code == 204
    assert db_conn.execute("SELECT tombstoned_at IS NOT NULL FROM idempotency_key").fetchone() == (
        True,
    )
    assert keyed(api, rejected, "retry-4").status_code == 409


def test_request_hash_ignores_key_order_and_number_spelling():
    a = idempotency.request_hash([("fields", "id")], {"a": D("10.0"), "b": [{"x": 1, "y": 2}]})
    b = idempotency.request_hash([("fields", "id")], {"b": [{"y": 2, "x": 1}], "a": D("10.00")})
    assert a == b
    assert a != idempotency.request_hash([], {"a": D("10.0"), "b": [{"x": 1, "y": 2}]})
    assert a != idempotency.request_hash([("fields", "id")], {"a": D("11"), "b": []})


def test_request_hash_reads_an_integer_and_a_decimal_as_one_number():
    ten = idempotency.request_hash([], {"a": 10, "b": [{"v": 7}]})
    assert ten == idempotency.request_hash([], {"a": D("10.00"), "b": [{"v": D("7.0")}]})
    assert ten != idempotency.request_hash([], {"a": True, "b": [{"v": 7}]})  # a bool is no 1
    assert idempotency.request_hash([], {"a": 1}) != idempotency.request_hash([], {"a": True})


def test_keys_are_scoped_by_operator_method_and_route(db_conn):
    for operator, route in (("i.petrova", "/a"), ("mcp:bot", "/a"), ("i.petrova", "/b")):
        assert idempotency.begin(db_conn, operator, "POST", route, "k", HASH) is None
        idempotency.store(
            db_conn, operator, "POST", route, "k", HASH,
            status_code=201, response={"id": route}, location=None, now=NOW,
        )  # fmt: skip
    assert idempotency.begin(db_conn, "mcp:bot", "POST", "/a", "k", HASH).response == {"id": "/a"}
    assert idempotency.begin(db_conn, "mcp:bot", "POST", "/b", "k", HASH) is None


def test_journal_rows_are_retained_and_tombstoned_once(db_conn):
    idempotency.store(
        db_conn, "u", "POST", "/a", "k", HASH,
        status_code=201, response={}, location="/a/1", now=NOW,
    )  # fmt: skip
    db_conn.commit()
    for statement in (
        "UPDATE idempotency_key SET response = '{\"x\": 1}'",
        "UPDATE idempotency_key SET status_code = 200",
        "DELETE FROM idempotency_key",
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="retained"):
            db_conn.execute(statement)
        db_conn.rollback()
    assert idempotency.tombstone(db_conn, "/a/1", now=NOW) == 1
    with pytest.raises(psycopg.errors.CheckViolation, match="retained"):
        db_conn.execute("UPDATE idempotency_key SET tombstoned_at = tombstoned_at")


def test_concurrent_duplicates_wait_for_the_first_and_replay_it(db_conn, migrated_db_url):
    idempotency.begin(db_conn, "u", "POST", "/a", "k", HASH)  # holds the key's lock
    seen = {}

    def duplicate():
        with psycopg.connect(migrated_db_url) as other:
            seen["replay"] = idempotency.begin(other, "u", "POST", "/a", "k", HASH)

    thread = threading.Thread(target=duplicate)
    thread.start()
    thread.join(timeout=1)
    assert thread.is_alive(), "the duplicate must wait for the first request"
    idempotency.store(
        db_conn, "u", "POST", "/a", "k", HASH,
        status_code=201, response={"id": 1}, location=None, now=NOW,
    )  # fmt: skip
    db_conn.commit()
    thread.join(timeout=10)
    assert seen["replay"].response == {"id": 1}


def test_rollback_leaves_no_key(db_conn):
    idempotency.begin(db_conn, "u", "POST", "/a", "k", HASH)
    idempotency.store(
        db_conn, "u", "POST", "/a", "k", HASH,
        status_code=201, response={}, location=None, now=NOW,
    )  # fmt: skip
    db_conn.rollback()
    assert idempotency.begin(db_conn, "u", "POST", "/a", "k", HASH) is None
