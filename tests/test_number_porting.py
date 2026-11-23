"""Carrier number pools, port-in and port-out (issue #189)."""
# ruff: noqa: F811

from datetime import timedelta

import pytest

from app.config import settings
from tests import apidata
from tests.apidata import ORDER
from tests.builders.api_order_cancel import CANCEL, cancel_body
from tests.builders.api_resource_inventory import number
from tests.builders.network_provisioning import (  # noqa: F401 - fixtures
    commands_of,
    state,
    stub,
    world,
)
from tests.builders.number_orders import refs
from tests.builders.number_porting import BODY, NUMBER, PORT, buy_number, port_in
from tests.builders.prepaid_orders import shop
from tests.builders.prepaid_renewal_producer import tick

RESOURCE = "/tmf-api/resourceInventory/v5/resource"
POOL = "/internal/v1/number/pool"
PIN = "/internal/v1/port/pin"


def port_state(api, port):
    return api.get(f"{PORT}/{port['id']}").json()


def run_ticks(api, db_conn, count=3):
    for _ in range(count):
        tick(db_conn, api.clock.now())


def line_of(api, db_conn, world, value="12125550199"):
    from tests.builders.network_provisioning import (
        line,
    )

    return line(api, db_conn, world, value)


# --- number pool ---


@pytest.mark.capability("worker.numberPorting")
def test_numbers_requested_by_area_code_appear_in_inventory(api):
    response = api.post(POOL, {"areaCode": "415", "count": 3})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["areaCode"] == "415" and body["requested"] == 3 and len(body["numbers"]) == 3
    listed = api.get(f"{RESOURCE}?resourceSpecification.id=msisdn&limit=100").json()
    values = sorted(r["value"] for r in listed)
    assert values == sorted(n["value"] for n in body["numbers"])
    assert all(v.startswith("1415555") and len(v) == 11 for v in values)  # the fiction range
    assert all(r["administrativeState"] == "unlocked" and r["usageState"] == "idle" for r in listed)
    # a second request gives other numbers
    again = api.post(POOL, {"areaCode": "415", "count": 2}).json()
    assert not {n["value"] for n in again["numbers"]} & set(values)
    assert len(api.get(f"{RESOURCE}?resourceSpecification.id=msisdn&limit=100").json()) == 5


def test_pool_refusals(api, stub):
    assert api.post(POOL, {"areaCode": "155", "count": 1}).status_code == 422
    assert api.post(POOL, {"areaCode": "415", "count": 0}).status_code == 422
    assert api.post(POOL, {"areaCode": "415", "count": 101}).status_code == 422
    assert api.post(POOL, {"areaCode": "415", "count": 1, "x": 1}).status_code == 400
    assert api.post(POOL, {"areaCode": "415", "count": 100}).status_code == 201
    exhausted = api.post(POOL, {"areaCode": "415", "count": 1})
    assert exhausted.status_code == 409 and "no more numbers" in exhausted.text
    assert api.post(POOL, {"areaCode": "212", "count": 1}).status_code == 201  # other area code


# --- port-in ---


@pytest.mark.capability("worker.numberPorting")
def test_port_in_happy_path_activates_the_line_with_the_ported_number(api, db_conn, world, stub):
    port = port_in(api)
    assert port["state"] == "submitted" and port["direction"] == "in"
    assert port["areaCode"] == "212" and "transferPin" not in port
    resource_id = port["resource"]["id"]
    assert api.get(f"{RESOURCE}/{resource_id}").json()["value"] == NUMBER
    order = buy_number(api, world, resource_id)
    assert order["state"] == "inProgress"
    assert commands_of(db_conn, order) == [("activate", "pending", 0)]

    for _ in range(4):
        tick(db_conn, api.clock.now())
        current = port_state(api, port)["state"]
        if current != "completed":
            assert stub.received == {}  # the line is not activated while the port is open
        if state(api, order) == "completed":
            break
    assert current == "completed" and port_state(api, port)["completedAt"] is not None
    assert state(api, order) == "completed"
    ((_, (kind, request)),) = stub.received.items()
    assert kind == "activate" and request["msisdn"] == NUMBER and request["portedIn"] is True
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("active",)
    # the carrier saw the submission once, with the data; our copy of the PIN is gone
    (sent,) = stub.port_in.values()
    assert sent["transferPin"] == "123456" and sent["carrierAccountNumber"] == "ACC-998877"
    assert db_conn.execute("SELECT transfer_pin FROM port_request").fetchone() == (None,)
    assert "123456" not in str(port_state(api, port))


@pytest.mark.capability("worker.numberPorting")
def test_a_rejected_port_in_shows_its_reason_and_can_be_resubmitted(api, db_conn, world, stub):
    port = port_in(api, transferPin="000000")
    order = buy_number(api, world, port["resource"]["id"])
    run_ticks(api, db_conn, 3)
    rejected = port_state(api, port)
    assert rejected["state"] == "rejected" and rejected["rejectionCode"] == "pinMismatch"
    assert "PIN" in rejected["rejectionReason"]
    assert state(api, order) == "inProgress" and stub.received == {}  # the order waits

    assert api.post(f"{PORT}/{port['id']}/cancel", None).status_code == 409  # an order holds it
    fixed = api.post(f"{PORT}/{port['id']}/resubmit", {"transferPin": "654321"})
    assert fixed.status_code == 202, fixed.text
    assert fixed.json()["state"] == "submitted" and fixed.json()["submissions"] == 2
    assert fixed.json()["rejectionCode"] is None
    run_ticks(api, db_conn, 4)
    assert port_state(api, port)["state"] == "completed"
    assert state(api, order) == "completed"
    assert len(stub.port_in) == 2  # a resubmit is a new submission (new key)
    # only a rejected port-in can be resubmitted
    again = api.post(f"{PORT}/{port['id']}/resubmit", {"transferPin": "1"})
    assert again.status_code == 409


def test_port_in_data_can_be_corrected_on_resubmit(api, db_conn, stub):
    port = port_in(api, billingZip="00000")
    run_ticks(api, db_conn, 3)
    assert port_state(api, port)["rejectionCode"] == "zipMismatch"
    fixed = api.post(
        f"{PORT}/{port['id']}/resubmit", {"transferPin": "123456", "billingZip": "10002"}
    )
    assert fixed.status_code == 202 and fixed.json()["billingZip"] == "10002"
    run_ticks(api, db_conn, 3)
    assert port_state(api, port)["state"] == "completed"
    assert api.post(f"{PORT}/{port['id']}/resubmit", {}).status_code == 422  # PIN is needed


def test_a_port_in_stays_pending_until_the_carrier_completes_it(api, db_conn, world, stub):
    value = "12125550001"
    port = port_in(api, number=value)
    order = buy_number(api, world, port["resource"]["id"])
    run_ticks(api, db_conn, 3)
    assert port_state(api, port)["state"] == "pending"
    assert port_state(api, port)["carrierReference"].startswith("PORT-")
    assert state(api, order) == "inProgress" and stub.received == {}
    stub.complete_port_in(value)
    stub.recover(value)  # the stub's network command for a number ending 0001
    run_ticks(api, db_conn, 3)
    assert port_state(api, port)["state"] == "completed" and state(api, order) == "completed"


def test_temporary_carrier_errors_retry_then_reject_and_resubmit_works(
    api, db_conn, stub, monkeypatch
):
    monkeypatch.setattr(settings, "network_max_attempts", 2)
    value = "12125550002"
    port = port_in(api, number=value)
    now = api.clock.now()
    tick(db_conn, now)
    assert port_state(api, port)["state"] == "submitted" and port_state(api, port)["attempts"] == 1
    tick(db_conn, now + timedelta(seconds=59))  # backoff: not sent again
    assert len(stub.attempts) == 1
    tick(db_conn, now + timedelta(minutes=1))
    rejected = port_state(api, port)
    assert rejected["state"] == "rejected" and rejected["rejectionCode"] == "networkUnavailable"
    stub.recover(value)
    api.post(f"{PORT}/{port['id']}/resubmit", {"transferPin": "123456"})
    run_ticks(api, db_conn, 3)
    assert port_state(api, port)["state"] == "completed"


def test_cancelling_the_order_cancels_its_port_in(api, db_conn, world, stub):
    port = port_in(api)
    order = buy_number(api, world, port["resource"]["id"])
    run_ticks(api, db_conn, 1)
    assert port_state(api, port)["state"] == "pending"
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert port_state(api, port)["state"] == "cancelled"
    run_ticks(api, db_conn, 2)
    assert stub.received == {}
    # a cancelled port cannot be claimed by a new order
    item = apidata.add_item(world.off, "1")
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(port["resource"]["id"]),
    }
    again = api.post(ORDER, apidata.order_body(world.acc, item))
    assert again.status_code == 201 and again.json()["state"] == "rejected"
    assert "request it again" in again.text
    # a new port-in for the same number can then be recorded
    assert port_in(api)["state"] == "submitted"


def test_an_unclaimed_open_port_in_can_be_cancelled(api, db_conn, stub):
    port = port_in(api)
    response = api.post(f"{PORT}/{port['id']}/cancel", None)
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert api.post(f"{PORT}/{port['id']}/cancel", None).status_code == 409
    run_ticks(api, db_conn, 2)
    assert stub.port_in == {}  # never sent


def test_a_number_being_ported_needs_a_network_line(api, db_conn, stub):
    off, acc = shop(api, db_conn, funds="100", numbers=True)  # not a network line
    port = port_in(api)
    item = apidata.add_item(off, "1")
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(port["resource"]["id"]),
    }
    order = api.post(ORDER, apidata.order_body(acc, item))
    assert order.status_code == 201 and order.json()["state"] == "rejected"
    assert "network line" in order.text


def test_port_in_refusals_and_listing(api, stub):
    assert api.post(f"{PORT}/in", BODY | {"billingZip": "1"}).status_code == 422
    assert api.post(f"{PORT}/in", BODY | {"x": 1}).status_code == 400
    assert (
        api.post(f"{PORT}/in", {k: v for k, v in BODY.items() if k != "transferPin"}).status_code
        == 422
    )
    number(api, value="12125550777")  # a manual number is not ours to port
    assert api.post(f"{PORT}/in", BODY | {"number": "12125550777"}).status_code == 409
    first = port_in(api)
    assert api.post(f"{PORT}/in", BODY).status_code == 409  # already has a request
    assert api.get(f"{PORT}/{first['id']}").json()["state"] == "submitted"
    assert api.get(f"{PORT}/{first['id']}x").status_code in (400, 404)
    listed = api.get(f"{PORT}?direction=in&state=submitted&number={NUMBER}")
    assert [p["id"] for p in listed.json()] == [first["id"]]
    assert api.get(f"{PORT}?state=nope").status_code == 400
    assert api.get(f"{PORT}?direction=out").json() == []


# --- Number Transfer PIN and port-out ---


def pin_for(api, product_id):
    response = api.post(PIN, {"productId": product_id})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.capability("worker.numberPorting")
def test_a_transfer_pin_is_shown_once_never_stored_and_replaced_by_a_newer_one(
    api, db_conn, world, stub
):
    product = line_of(api, db_conn, world)
    first = pin_for(api, product["id"])
    assert len(first["pin"]) == 6 and first["pin"].isdigit() and first["state"] == "active"
    assert first["number"] == "12125550199"
    listed = api.get(f"{PIN}?product.id={product['id']}").json()
    assert [p["id"] for p in listed] == [first["id"]] and "pin" not in listed[0]
    dump = " ".join(
        str(c) for row in db_conn.execute("SELECT * FROM number_transfer_pin") for c in row
    )
    assert first["pin"] not in dump
    second = pin_for(api, product["id"])
    states = {p["id"]: p["state"] for p in api.get(f"{PIN}?product.id={product['id']}").json()}
    assert states == {first["id"]: "revoked", second["id"]: "active"}
    # not a line: unknown product, or a product without a number
    assert api.post(PIN, {"productId": "00000000-0000-4000-8000-000000000000"}).status_code == 404
    assert api.get(PIN).status_code == 400


@pytest.mark.capability("worker.numberPorting")
def test_a_transfer_pin_expires_after_its_validity(api, db_conn, world, stub):
    product = line_of(api, db_conn, world)
    issued = pin_for(api, product["id"])
    assert api.get(f"{PIN}?product.id={product['id']}").json()[0]["state"] == "active"
    created = api.clock.now()
    api.clock.set(
        created + timedelta(days=settings.number_transfer_pin_days) - timedelta(seconds=1)
    )
    assert api.get(f"{PIN}?product.id={product['id']}").json()[0]["state"] == "active"
    api.clock.set(created + timedelta(days=settings.number_transfer_pin_days))
    expired = api.get(f"{PIN}?product.id={product['id']}").json()[0]
    assert expired["state"] == "expired" and expired["id"] == issued["id"]


@pytest.mark.capability("worker.numberPorting")
def test_port_out_with_a_valid_pin_completes_and_ends_the_product(api, db_conn, world, stub):
    value = "12125550199"
    product = line_of(api, db_conn, world, value)
    pin = pin_for(api, product["id"])["pin"]
    stub.inject_port_out("OUT-1", value, pin)
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["state"] == "completed" and out["number"] == value
    assert out["product"]["id"] == product["id"] and out["productOrder"] is not None
    assert out["carrierReference"] == "OUT-1" and out["answeredAt"] is not None
    assert stub.port_answers and next(iter(stub.port_answers.values())) == ("OUT-1", True, None)
    # the product ends through an order, with no deactivate sent to the network
    order = api.get(out["productOrder"]["href"]).json()
    assert order["productOrderItem"][0]["action"] == "delete"
    assert db_conn.execute("SELECT kind FROM network_command ORDER BY kind").fetchall() == [
        ("activate",)
    ]
    run_ticks(api, db_conn, 2)
    assert api.get(out["productOrder"]["href"]).json()["state"] == "completed"
    status, ended = db_conn.execute("SELECT status, termination_date FROM product").fetchone()
    assert ended is not None and status in ("terminated", "pendingTerminate")
    # the number is released and locked: a ported-away number is never assigned again
    resource = api.get(f"{RESOURCE}?value={value}").json()[0]
    assert resource["administrativeState"] == "locked"
    # ... released, not aged: the lifecycle's number aging does not apply to a port-out
    assert resource["allocationStatus"] != "quarantined"
    assert db_conn.execute(
        "SELECT aging_days FROM number_assignment WHERE released_on IS NOT NULL"
    ).fetchone() == (0,)
    # the PIN is spent, and the same reference is not decided twice
    assert db_conn.execute("SELECT count(*) FROM port_request").fetchone() == (1,)
    stub.inject_port_out("OUT-1", value, pin)
    tick(db_conn, api.clock.now())
    assert db_conn.execute("SELECT count(*) FROM port_request").fetchone() == (1,)


@pytest.mark.capability("worker.numberPorting")
def test_port_out_with_an_invalid_pin_is_refused_and_changes_nothing(api, db_conn, world, stub):
    value = "12125550199"
    product = line_of(api, db_conn, world, value)
    pin = pin_for(api, product["id"])["pin"]
    wrong = "000000" if pin != "000000" else "111111"
    stub.inject_port_out("OUT-2", value, wrong)
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["state"] == "rejected" and out["rejectionCode"] == "pinInvalid"
    assert out["productOrder"] is None and out["answeredAt"] is not None
    assert stub.port_answers[next(iter(stub.port_answers))][1] is False
    assert db_conn.execute("SELECT status, termination_date FROM product").fetchone() == (
        "active",
        None,
    )
    assert api.get(f"{RESOURCE}?value={value}").json()[0]["administrativeState"] == "unlocked"
    # the right PIN still works afterwards
    stub.inject_port_out("OUT-3", value, pin)
    tick(db_conn, api.clock.now())
    assert api.get(f"{PORT}?direction=out&state=completed").json()


@pytest.mark.capability("worker.numberPorting")
def test_port_out_with_an_expired_pin_is_refused(api, db_conn, world, stub):
    value = "12125550199"
    product = line_of(api, db_conn, world, value)
    pin = pin_for(api, product["id"])["pin"]
    later = api.clock.now() + timedelta(days=settings.number_transfer_pin_days, seconds=1)
    stub.inject_port_out("OUT-4", value, pin)
    tick(db_conn, later)
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["state"] == "rejected" and out["rejectionCode"] == "pinExpired"
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("active",)


def test_port_out_refusals_unknown_number_no_pin_and_a_used_pin(api, db_conn, world, stub):
    value = "12125550199"
    product = line_of(api, db_conn, world, value)
    stub.inject_port_out("OUT-5", "19998887766", "123456")  # not our number
    stub.inject_port_out("OUT-6", value, "123456")  # no PIN was ever generated
    tick(db_conn, api.clock.now())
    codes = {
        p["carrierReference"]: p["rejectionCode"] for p in api.get(f"{PORT}?direction=out").json()
    }
    assert codes == {"OUT-5": "numberUnknown", "OUT-6": "pinInvalid"}
    unknown = next(
        p for p in api.get(f"{PORT}?direction=out").json() if p["carrierReference"] == "OUT-5"
    )
    assert unknown["resource"] is None
    pin = pin_for(api, product["id"])["pin"]
    stub.inject_port_out("OUT-7", value, pin)
    tick(db_conn, api.clock.now())
    stub.inject_port_out("OUT-8", value, pin)  # the PIN is single use (and the line is gone)
    tick(db_conn, api.clock.now())
    again = {p["carrierReference"]: p for p in api.get(f"{PORT}?direction=out").json()}
    assert again["OUT-7"]["state"] == "completed"
    assert again["OUT-8"]["rejectionCode"] in ("lineNotActive", "pinInvalid")


def test_an_unanswered_port_out_is_answered_again_until_the_carrier_takes_it(
    api, db_conn, world, stub, monkeypatch
):
    value = "12125550199"
    line_of(api, db_conn, world, value)
    stub.inject_port_out("OUT-9", value, "123456")
    real = stub.answer_port_out
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("carrier down")
        return real(*args, **kwargs)

    monkeypatch.setattr(stub, "answer_port_out", flaky)
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["answeredAt"] is None
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["answeredAt"] is not None and len(calls) == 2
    assert calls[0][0] == calls[1][0]  # the same idempotency key


# --- the requests the admin UI sends (ports.ts, AddProduct.tsx; PortRequests.test.tsx) ---


@pytest.mark.capability("worker.numberPorting")
def test_ui_port_requests_replayed(api, db_conn, world, stub):
    # Add product's port-in step: the request first, then an order with the number it made
    port = port_in(api, transferPin="000000")
    order = buy_number(api, world, port["resource"]["id"])
    assert order["state"] == "inProgress"
    run_ticks(api, db_conn, 3)
    # the list the screen loads, filters in the order it sends them, and the detail
    listed = api.get(
        f"{PORT}?direction=in&state=rejected&number={NUMBER}&offset=0&limit=100"
    ).json()
    assert [p["id"] for p in listed] == [port["id"]]
    assert api.get(f"{PORT}/{port['id']}").json()["rejectionCode"] == "pinMismatch"
    # cancel sends `{}`; an order holds the number, so the server's text is shown in the dialog
    held = api.post(f"{PORT}/{port['id']}/cancel", {})
    assert held.status_code == 409, held.text
    # resubmit sends only the PIN and the fields the operator changed
    fixed = api.post(
        f"{PORT}/{port['id']}/resubmit", {"transferPin": "654321", "billingZip": "10002"}
    )
    assert fixed.status_code == 202 and fixed.json()["billingZip"] == "10002"
    # the pool dialog and the PIN dialog
    pool = api.post(POOL, {"areaCode": "415", "count": 3})
    assert pool.status_code == 201 and len(pool.json()["numbers"]) == 3
    run_ticks(api, db_conn, 4)
    product = db_conn.execute("SELECT id FROM product").fetchone()[0]
    pin = api.post(PIN, {"productId": str(product)})
    assert pin.status_code == 201 and len(pin.json()["pin"]) == 6
    pins = api.get(f"{PIN}?product.id={product}&offset=0&limit=100")
    assert pins.status_code == 200 and [p["id"] for p in pins.json()] == [pin.json()["id"]]
