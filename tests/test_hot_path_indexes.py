"""The worker hot paths, admin list sorts and reference checks of #310 read an index, and the
history guards ask EXISTS instead of counting every reference."""

import re

import pytest

from app.repository import _sql, parties
from tests import apidata
from tests.builders.hot_path_indexes import plan

NIL = apidata.NIL
SINCE = "2026-01-01T00:00:00Z"


def sorts(text: str) -> bool:
    return re.search(r"\bSort  \(", text) is not None  # a Sort node, not Merge Append's key


@pytest.mark.parametrize(
    ("query", "params", "index"),
    [
        # worker hot paths
        (
            "SELECT * FROM applied_customer_billing_rate WHERE product_id = %s"
            " ORDER BY lower(period), charge_type",
            (NIL,),
            "applied_customer_billing_rate_product_id_idx",
        ),
        (
            # auto_candidates also asks rating_type = 'prepaid', which an empty table's
            # estimates would send to billing_account_mode_identity
            "SELECT a.id FROM billing_account a WHERE a.auto_topup_enabled ORDER BY a.id",
            (),
            "billing_account_auto_topup_idx",
        ),
        (
            "SELECT DISTINCT p.billing_account_id FROM product p WHERE p.grantless"
            " AND p.parent_product_id IS NULL AND p.status = 'active'"
            " ORDER BY p.billing_account_id",
            (),
            "product_grantless_account_idx",
        ),
        (
            "SELECT 1 FROM payment_command c WHERE c.renewal_command_id = %s"
            " AND c.state IN ('declined', 'exhausted')",
            (NIL,),
            "payment_command_renewal_idx",
        ),
        (
            "SELECT c.id FROM prepaid_command c WHERE c.kind = 'renewal'"
            " AND c.state = 'completed' AND c.attempted_at >= %s ORDER BY c.attempted_at, c.id",
            (SINCE,),
            "prepaid_command_renewed_idx",
        ),
        (
            "SELECT * FROM usage_threshold t WHERE t.reached_at >= %s"
            " ORDER BY t.reached_at, t.threshold",
            (SINCE,),
            "usage_threshold_reached_idx",
        ),
        (
            "SELECT c.id FROM payment_command c WHERE c.state = 'declined'"
            " AND c.created_at >= %s ORDER BY c.created_at, c.id",
            (SINCE,),
            "payment_command_declined_idx",
        ),
        (
            "SELECT c.id FROM network_command c WHERE c.kind = 'swap_sim'"
            " AND c.state = 'confirmed' AND c.confirmed_at >= %s ORDER BY c.confirmed_at, c.id",
            (SINCE,),
            "network_command_sim_swapped_idx",
        ),
        # billing runs
        (
            "SELECT 1 AS x FROM billing_run WHERE status = 'running' LIMIT 1",
            (),
            "billing_run_running_idx",
        ),
        (
            "SELECT r.id FROM billing_run r WHERE r.status = 'completed' AND r.run_date < %s"
            " ORDER BY r.run_date, r.id LIMIT 1",
            ("2026-01-01",),
            "billing_run_completed_idx",
        ),
        # references
        (
            "SELECT EXISTS (SELECT 1 FROM billing_run_account WHERE billing_account_id = %s)",
            (NIL,),
            "billing_run_account_billing_account_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM usage_charge WHERE billing_account_id = %s)",
            (NIL,),
            "usage_charge_billing_account_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM port_request WHERE billing_account_id = %s)",
            (NIL,),
            "port_request_billing_account_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM port_request WHERE product_order_id = %s)",
            (NIL,),
            "port_request_product_order_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM product_device WHERE recorded_by_item_id = %s)",
            (NIL,),
            "product_device_recorded_by_item_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM product_device WHERE ended_by_item_id = %s)",
            (NIL,),
            "product_device_ended_by_item_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM product_order_error_message"
            " WHERE product_order_item_id = %s)",
            (NIL,),
            "product_order_error_message_product_order_item_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM prepaid_lifecycle_event WHERE product_order_id = %s)",
            (NIL,),
            "prepaid_lifecycle_event_product_order_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM product_order_item WHERE product_offering_id = %s)",
            (NIL,),
            "product_order_item_product_offering_id_idx",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM payment_command WHERE payment_method_id = %s)",
            (NIL,),
            "payment_command_payment_method_id_idx",
        ),
    ],
)
def test_the_lookup_reads_its_index(db_conn, query, params, index):
    text = plan(db_conn, query, params)
    assert index in text, text


def test_an_accounts_usage_reads_its_index_on_every_partition(db_conn):
    text = plan(
        db_conn,
        "SELECT EXISTS (SELECT 1 FROM usage_record WHERE billing_account_id = %s)",
        (NIL,),
    )
    scans = [line for line in text.splitlines() if " Scan " in line]
    assert scans and all("_billing_account_id_event_date_idx" in line for line in scans), text


@pytest.mark.parametrize(
    ("query", "index"),
    [
        (
            "SELECT * FROM applied_customer_billing_rate ORDER BY created_at, id",
            "applied_customer_billing_rate_created_at_id_idx",
        ),
        (
            "SELECT * FROM prepaid_command c ORDER BY c.created_at DESC, c.id DESC",
            "prepaid_command_created_at_id_idx",
        ),
        (
            "SELECT * FROM payment_command ORDER BY created_at DESC, id",
            "payment_command_created_at_id_idx",
        ),
        (
            "SELECT * FROM network_command ORDER BY created_at DESC, id",
            "network_command_created_at_id_idx",
        ),
        (
            "SELECT * FROM notification_message ORDER BY created_at DESC, id",
            "notification_message_created_at_id_idx",
        ),
        (
            # the list's status = 'active' filter matches payment_method_token_once, which
            # an empty table's estimates prefer
            "SELECT * FROM payment_method ORDER BY created_at, id",
            "payment_method_created_at_id_idx",
        ),
        (
            "SELECT d.state FROM event_delivery d WHERE d.subscription_id = %s"
            " AND d.last_attempt_at IS NOT NULL ORDER BY d.last_attempt_at DESC, d.id DESC",
            "event_delivery_subscription_last_idx",
        ),
    ],
)
def test_a_list_page_reads_its_index_without_sorting(db_conn, query, index):
    params = (NIL,) if "%s" in query else ()
    text = plan(db_conn, query + " OFFSET 10000 LIMIT 100", params)
    assert index in text, text
    assert not sorts(text), text


def test_the_history_guards_ask_whether_a_reference_exists(api, monkeypatch):
    """Changing a customer or the rating type is refused once the account has history; the
    guard needs no counts, so it asks EXISTS, never count(*), which read every row of
    billing_run_account (+100k a day at 100k subscribers)."""
    acc = apidata.account(api, ratingType="prepaid")  # a prepaid account has its bucket
    other = apidata.account_body(apidata.customer(api))["relatedParty"]
    real = _sql.fetch_one
    counted = []

    def recording(conn, query, params=None):
        if "count(*)" in str(query):
            counted.append(query)
        return real(conn, query, params)

    monkeypatch.setattr(_sql, "fetch_one", recording)
    response = api.patch(acc["href"], {"ratingType": "postpaid"})
    assert response.status_code == 409, response.text
    assert "once the account has history" in response.json()["message"]
    response = api.patch(acc["href"], {"relatedParty": other})
    assert response.status_code == 409, response.text
    assert "once the account has history" in response.json()["message"]
    assert not counted


def test_is_referenced_answers_as_count_references(api, db_conn):
    acc = apidata.account(api)
    assert not _sql.is_referenced(db_conn, acc["id"], parties.ACCOUNT_HISTORY)
    assert not _sql.count_references(db_conn, acc["id"], parties.ACCOUNT_HISTORY)
    prepaid = apidata.account(api, ratingType="prepaid")
    assert _sql.is_referenced(db_conn, prepaid["id"], parties.ACCOUNT_HISTORY)
    assert _sql.count_references(db_conn, prepaid["id"], parties.ACCOUNT_HISTORY)


@pytest.mark.parametrize(
    "query",
    [
        # worker/prepaid.py: is the renewal blocked, and the product of its interval
        "SELECT f.product_id FROM funded_interval f WHERE f.id = %s::uuid",
        # notify/events.py renewal detectors and the worker's due commands
        "SELECT f.product_id FROM prepaid_command c JOIN funded_interval f"
        " ON f.id = (c.request->>'previous_interval_id')::uuid WHERE c.id = %s",
    ],
)
def test_a_renewals_interval_is_found_by_its_key(db_conn, query):
    """request->>'previous_interval_id' is compared as a uuid: `f.id::text = ...` could only
    filter every funded interval (43 ms per renewal at 100k)."""
    text = plan(db_conn, query, (NIL,))
    assert re.search(r"Index (Only )?Scan using funded_interval_\w+ on funded_interval f", text)
    assert "Index Cond: (id = " in text, text
