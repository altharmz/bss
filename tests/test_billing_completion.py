"""Real financial flows: final bill -> credit note -> payout, and immutable documents."""

import base64
import json
import threading
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from app.api import tmfjson
from app.repository import billing
from app.worker import db as worker_db
from app.worker.on_demand import process_demands
from app.worker.process import bill_date
from app.worker.queue import process_requests
from tests import apidata
from tests.apiclient import decimals
from tests.test_api_bill_settlement import check_invariant, pay, summary
from tests.test_billing_db import add, at, delete
from tests.timeline import TZ

REFUND = "/tmf-api/payment/v4/refund"
DEMAND = "/tmf-api/customerBillManagement/v5/customerBillOnDemand"
FORMAT = "/tmf-api/accountManagement/v5/billFormat"
MEDIA = "/tmf-api/accountManagement/v5/billPresentationMedia"
NIL = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def worker(migrated_db_url):
    with worker_db.connect(migrated_db_url) as conn:
        assert worker_db.acquire_ownership(conn)
        yield conn


def payout_body(account, value=20, **over):
    return {
        "account": {"id": account["id"]},
        "totalAmount": {"unit": "USD", "value": value},
        "paymentMethod": {"name": "cash payout", "@type": "Cash"},
        **over,
    }


@pytest.mark.parametrize("restricted", [False, True])
def test_refund_preserves_reserved_credit(api, db_conn, restricted):
    from app.repository import funding
    from tests.factories import NOW

    account = apidata.account(api)
    payment = pay(api, account, 100, date(2026, 12, 1)).json()
    hold = funding.reserve(db_conn, UUID(account["id"]), Decimal("60"), held_at=NOW)
    db_conn.commit()
    extra = {"payment": {"id": payment["id"]}} if restricted else {}
    refused = api.post(REFUND, payout_body(account, 41, **extra))
    assert refused.status_code == 409, refused.text
    response = api.post(REFUND, payout_body(account, 40, **extra))
    assert response.status_code == 201, response.text
    assert funding.load(db_conn, UUID(account["id"])).spendable == 0
    funding.release(db_conn, UUID(account["id"]), hold, released_at=NOW)
    db_conn.commit()
    response = api.post(REFUND, payout_body(account, 60, **extra))
    assert response.status_code == 201, response.text


def run(worker, api, day):
    api.clock.set(at(day))
    bill_date(worker, day, clock=api.clock, tz=TZ, trigger="scheduled")


def demand(api, account):
    response = api.post(DEMAND, {"billingAccount": {"id": account["id"]}})
    assert response.status_code == 201, response.text
    return response.json()


def test_refund_consumes_credit_and_ledger_matches_balance(api, db_conn):
    account = apidata.account(api)
    payment = pay(api, account, 100, date(2026, 12, 1)).json()
    # Make UUID ordering oppose causal ordering so the timestamp tie test is deterministic.
    last_uuid = UUID(int=(1 << 128) - 1)
    db_conn.execute("UPDATE payment SET id = %s WHERE id = %s", (last_uuid, UUID(payment["id"])))
    db_conn.commit()
    payment["id"] = str(last_uuid)
    response = api.post(REFUND, payout_body(account, 25, payment={"id": payment["id"]}))
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["refundDate"] == payment["paymentDate"]  # frozen clock: explicit causal tie
    assert row["paymentMethod"] == {"name": "cash payout", "@type": "Cash"}
    assert api.get(row["href"]).json() == row
    assert (
        api.get(f"{REFUND}?account.id={account['id']}&fields=totalAmount").json()[0]["totalAmount"][
            "value"
        ]
        == 25
    )
    assert summary(api, account)["unallocatedCredit"]["value"] == Decimal(75)
    ledger = decimals(api.get(f"/internal/v1/billingAccount/{account['id']}/transactions"))
    assert ledger[0]["type"] == "payout" and ledger[0]["amount"]["value"] == -25
    assert ledger[0]["balanceAfter"]["value"] == 75
    check_invariant(api, account)
    assert api.post(REFUND, payout_body(account, 76)).status_code == 409
    assert len(api.get(REFUND).json()) == 1
    for method in ("PATCH", "DELETE"):
        assert api.request(method, row["href"]).status_code == 405
    api.clock.set(api.clock.now() + timedelta(hours=1))
    assert api.post(apidata.PAYMENT, apidata.payment_body(account, 5)).status_code == 201
    latest = api.get(f"/internal/v1/billingAccount/{account['id']}/transactions").json()[0]
    assert latest["type"] == "payment" and latest["balanceAfter"]["value"] == 80


def test_concurrent_refunds_cannot_spend_the_same_credit(api):
    account = apidata.account(api)
    pay(api, account, 100, date(2026, 12, 1))
    gate = threading.Barrier(3)
    responses = []

    def post():
        gate.wait()
        responses.append(api.post(REFUND, payout_body(account, 80)))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    gate.wait()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert summary(api, account)["unallocatedCredit"]["value"] == 20
    check_invariant(api, account)


def test_refund_credit_cannot_be_reused_by_later_billing(api, worker):
    offering = apidata.sellable_offering(api, value=300)
    account = apidata.account(api)
    pay(api, account, 400, date(2026, 11, 1))
    assert api.post(REFUND, payout_body(account, 100)).status_code == 201
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run(worker, api, date(2026, 12, 1))
    run(worker, api, date(2027, 1, 1))
    s = summary(api, account)
    assert s["unallocatedCredit"]["value"] == 0
    assert s["outstanding"]["value"] == 300
    check_invariant(api, account)


@pytest.mark.capability("worker.onDemand")
@pytest.mark.parametrize("run_day", [date(2026, 12, 3), date(2026, 12, 31)])
def test_off_cycle_bill_replays_once_and_keeps_monthly_anchor(api, worker, run_day):
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 2), on=at(date(2026, 12, 2)))
    request = demand(api, account)
    assert api.post(DEMAND, {"billingAccount": {"id": account["id"]}}).status_code == 409
    assert process_demands(worker, clock=api.clock, tz=TZ) == []
    run(worker, api, run_day)
    assert process_requests(worker, clock=api.clock, tz=TZ) == [UUID(request["id"])]
    result = api.get(request["href"]).json()
    assert result["state"] == "done"
    bill = api.get(result["customerBill"]["href"]).json()
    assert bill["runType"] == "offCycle"
    assert bill["amountDue"]["value"] == 300
    assert process_demands(worker, clock=api.clock, tz=TZ) == []
    run(worker, api, date(2027, 1, 1))
    bills = api.get(f"{apidata.BILL}?billingAccount.id={account['id']}").json()
    assert len(bills) == 2
    assert bills[1]["runType"] == "onCycle" and bills[1]["amountDue"]["value"] == 310
    check_invariant(api, account)


def test_final_bill_credit_note_can_be_paid_out(api, worker):
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run(worker, api, date(2026, 12, 1))
    pay(api, account, 310, date(2026, 12, 2))
    delete(api, account, product, date(2026, 12, 11), on=at(date(2026, 12, 11)))
    request = demand(api, account)
    run(worker, api, date(2026, 12, 12))
    process_demands(worker, clock=api.clock, tz=TZ)
    assert api.get(request["href"]).json()["state"] == "done"
    assert summary(api, account)["unallocatedCredit"]["value"] == 210
    response = api.post(REFUND, payout_body(account, 210))
    assert response.status_code == 201, response.text
    assert summary(api, account)["unallocatedCredit"]["value"] == 0
    check_invariant(api, account)
    run(worker, api, date(2027, 1, 1))
    check_invariant(api, account)
    assert api.post(REFUND, payout_body(account, 1)).status_code == 409


def test_no_unbilled_charges_rejects_without_empty_bill(api, worker):
    account = apidata.account(api)
    request = demand(api, account)
    run(worker, api, date(2026, 10, 2))
    process_demands(worker, clock=api.clock, tz=TZ)
    assert api.get(request["href"]).json()["state"] == "rejected"
    assert api.get(apidata.BILL).json() == []


@pytest.mark.capability("worker.billDocument")
def test_presentation_preferences_render_and_preserve_history(api, worker):
    fmt = api.post(FORMAT, {"@type": "BillFormat", "name": "JSON summary"}).json()
    media = api.post(MEDIA, {"name": "Portal", "description": "Self service"}).json()
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    response = api.patch(
        account["href"],
        {
            "billStructure": {
                "format": {"id": fmt["id"]},
                "presentationMedia": [{"id": media["id"]}],
            }
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["billStructure"]["cycleSpecification"]["id"] == "monthly"
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    run(worker, api, date(2026, 12, 1))
    bill = api.get(apidata.BILL).json()[0]
    encoded = bill["billDocument"][0]["content"]
    doc = json.loads(base64.b64decode(encoded))
    assert doc["format"]["name"] == "JSON summary" and "charges" not in doc
    assert doc["presentationMedia"][0]["name"] == "Portal"
    assert api.patch(media["href"], {"name": "Renamed"}).status_code == 200
    assert (
        api.patch(
            account["href"], {"billStructure": {"format": None, "presentationMedia": []}}
        ).status_code
        == 200
    )
    pay(api, account, 310, date(2026, 12, 2))
    assert api.get(bill["href"]).json()["billDocument"][0]["content"] == encoded
    run(worker, api, date(2027, 1, 1))
    second = api.get(apidata.BILL).json()[1]
    detailed = tmfjson.loads(base64.b64decode(second["billDocument"][0]["content"]))
    assert detailed["charges"][0]["amount"]["value"] == 310
    assert detailed["presentationMedia"] == []


@pytest.mark.parametrize(
    "path,name,typ",
    [(FORMAT, "JSON detailed", "BillFormat"), (MEDIA, "Portal", "BillPresentationMedia")],
)
def test_presentation_crud_validation_and_queries(api, path, name, typ):
    response = api.post(f"{path}?fields=name", {"@type": typ, "name": name})
    assert response.status_code == 201, response.text
    row = response.json()
    assert set(row) == {"id", "href", "@type", "name"}
    assert api.get(row["href"]).json()["name"] == name
    assert api.get(f"{path}?name={name}&limit=1").headers["X-Total-Count"] == "1"
    assert api.get(f"{path}?offset=1").json() == []
    assert api.patch(row["href"], {"description": "Updated"}).json()["description"] == "Updated"
    assert "description" not in api.patch(row["href"], {"description": None}).json()
    assert api.patch(row["href"], {"name": None}).status_code == 422
    assert api.post(path, {"name": name}).status_code == 409
    assert api.post(path, {"@type": "Wrong", "name": name}).status_code == 400
    assert api.get(f"{path}?unknown=1").status_code == 400
    assert api.get(f"{path}/{NIL}").status_code == 404
    assert api.patch(f"{path}/{NIL}", {"description": "x"}).status_code == 404


def test_refund_and_preferences_invalid_requests_are_atomic(api):
    account = apidata.account(api)
    pay(api, account, 100, date(2026, 12, 1))
    for value in (0, -1, "20.00", True):
        assert api.post(REFUND, payout_body(account, value)).status_code == 422
    assert api.post(REFUND, payout_body(account, 20, payment={"id": NIL})).status_code == 422
    assert (
        api.post(
            REFUND, payout_body(account, 20, totalAmount={"unit": "GBP", "value": 20})
        ).status_code
        == 422
    )
    assert (
        api.post(REFUND, payout_body(account, 20, refundDate="2026-12-01T00:00:00Z")).status_code
        == 422  # the server's (now): another value rolls the refund back
    )
    assert api.get(REFUND).json() == []
    assert summary(api, account)["unallocatedCredit"]["value"] == 100
    assert (
        api.patch(
            account["href"], {"name": "Lost", "billStructure": {"format": {"id": NIL}}}
        ).status_code
        == 422
    )
    assert api.get(account["href"]).json()["name"] == account["name"]
    assert api.post(FORMAT, {"name": "PDF"}).status_code == 422
    assert api.post(DEMAND, {"billingAccount": {"id": NIL}}).status_code == 422
    assert api.get(f"{DEMAND}/{NIL}").status_code == 404
    assert api.get(f"{REFUND}/{NIL}").status_code == 404


def test_database_credit_bounds_reject_unfunded_refund(api, db_conn):
    account = apidata.account(api)
    with pytest.raises(psycopg.errors.CheckViolation), db_conn.transaction():
        db_conn.execute(
            "INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_type)"
            " VALUES (%s,'USD',20,%s,'Cash')",
            (UUID(account["id"]), api.clock.now()),
        )
        db_conn.execute("SET CONSTRAINTS credit_usage_bounds IMMEDIATE")
    db_conn.rollback()
    assert api.get(REFUND).json() == []


def test_database_combines_payout_and_settlement_usage(api, db_conn):
    account = apidata.account(api)
    payment = pay(api, account, 100, date(2026, 12, 1)).json()
    with pytest.raises(psycopg.errors.CheckViolation), db_conn.transaction():
        refund_id = db_conn.execute(
            "INSERT INTO refund (billing_account_id, currency, amount, refunded_at, method_type)"
            " VALUES (%s,'USD',120,%s,'Cash') RETURNING id",
            (UUID(account["id"]), api.clock.now()),
        ).fetchone()[0]
        db_conn.execute(
            "INSERT INTO refund_source (refund_id, billing_account_id, payment_id, amount)"
            " VALUES (%s,%s,%s,120)",
            (refund_id, UUID(account["id"]), UUID(payment["id"])),
        )
        db_conn.execute("SET CONSTRAINTS credit_usage_bounds IMMEDIATE")
    db_conn.rollback()
    assert api.get(REFUND).json() == []


def test_payout_reserves_unbilled_charges(api, worker):
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    pay(api, account, 400, date(2026, 11, 1))
    add(api, account, offering, date(2026, 12, 2), on=at(date(2026, 12, 2)))
    run(worker, api, date(2026, 12, 3))
    assert summary(api, account)["unallocatedCredit"]["value"] == 400
    assert api.post(REFUND, payout_body(account, 101)).status_code == 409
    assert api.post(REFUND, payout_body(account, 100)).status_code == 201
    check_invariant(api, account)


def test_failed_demand_rolls_back_bill_and_finishes_with_error(api, worker, monkeypatch):
    from app.billing.model import InconsistentState

    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 2), on=at(date(2026, 12, 2)))
    request = demand(api, account)
    run(worker, api, date(2026, 12, 3))

    def fail(*args, **kwargs):
        raise InconsistentState("private failure detail")

    monkeypatch.setattr(billing, "attach_abrs_to_bill", fail)
    process_demands(worker, clock=api.clock, tz=TZ)
    result = api.get(request["href"]).json()
    assert result["state"] == "terminatedWithError"
    assert "private" not in result["description"]
    assert api.get(apidata.BILL).json() == []
    assert api.get(f"{apidata.RATE}?isBilled=false").headers["X-Total-Count"] == "1"


def test_blocked_demand_waits_and_can_be_recovered(api, worker):
    account = apidata.account(api)
    request = demand(api, account)
    run(worker, api, date(2026, 10, 2))
    run(worker, api, date(2026, 10, 3))
    worker.execute(
        "UPDATE billing_run_account SET status = 'failed', error = 'test failure'"
        " WHERE run_id = (SELECT id FROM billing_run WHERE run_date = '2026-10-03')"
    )
    assert process_demands(worker, clock=api.clock, tz=TZ) == []
    assert api.get(request["href"]).json()["state"] == "inProgress"
    worker.execute("UPDATE billing_run_account SET status = 'ok', error = NULL")
    process_demands(worker, clock=api.clock, tz=TZ)
    assert api.get(request["href"]).json()["state"] == "rejected"


def test_account_creation_preferences_and_partial_merge_patch(api):
    fmt = api.post(FORMAT, {"name": "JSON summary"}).json()
    media = api.post(MEDIA, {"name": "Portal"}).json()
    body = apidata.account_body(apidata.customer(api))
    body["billStructure"] = {
        "format": {"id": fmt["id"]},
        "presentationMedia": [{"id": media["id"]}],
    }
    response = api.post(apidata.ACCOUNT, body)
    assert response.status_code == 201, response.text
    account = response.json()
    changed = api.patch(account["href"], {"billStructure": {"format": None}}).json()
    assert "format" not in changed["billStructure"]
    assert changed["billStructure"]["presentationMedia"][0]["id"] == media["id"]
    assert api.patch(account["href"], {"billStructure": None}).status_code == 422
    assert (
        api.patch(
            account["href"], {"billStructure": {"presentationMedia": [{"id": media["id"]}] * 2}}
        ).status_code
        == 422
    )
