"""Network provisioning of prepaid order items through the adapter (issue #183)."""
# ruff: noqa: F811

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.clock import FixedClock
from app.config import Settings, settings
from app.repository import funding
from app.repository import network_commands as repo
from app.worker import network
from tests import apidata
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.orders.api_suspension import status_item
from tests.builders.prepaid.prepaid_offering_changes import applied, at, offering_of, switch
from tests.builders.prepaid.prepaid_orders import shop
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick
from tests.builders.prepaid.prepaid_status_orders import errors
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    PROFILE,
    buy,
    characteristics,
    cheaper,
    commands_of,
    item_uuid,
    line,
    make_line,
    state,
    stub,
    world,
)
from tests.builders.tmf.events import stored, subscribe

NET = "/internal/v1/network/command"


@pytest.mark.capability("worker.networkProvisioning")
def test_add_sends_activate_with_the_profile_and_completes_after_the_confirmation(
    api, db_conn, world, stub
):
    order = buy(api, world, "79991234500")
    assert order["state"] == "inProgress"
    assert commands_of(db_conn, order) == [("activate", "pending", 0)]
    assert stub.received == {}  # nothing is sent inside the request
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)

    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    assert commands_of(db_conn, order) == [("activate", "confirmed", 1)]
    ((key, (kind, request)),) = stub.received.items()
    assert key == str(repo.command_id(item_uuid(db_conn, order), "activate"))
    assert kind == "activate"
    assert request["msisdn"] == "79991234500" and request["profile"] == PROFILE
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("active",)


def _states(conn, api_name: str, resource: str) -> list[str]:
    """The states the StateChange events of `api_name` carried, oldest first."""
    key = "state" if resource == "productOrder" else "status"
    return [
        payload["event"][resource][key]
        for event_type, payload in stored(conn, api_name)
        if event_type.endswith("StateChangeEvent")
    ]


@pytest.mark.capability("worker.networkProvisioning")
def test_a_confirmed_activate_moves_pending_active_to_active_with_the_state_events(
    api, db_conn, world, stub
):
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")
    order = buy(api, world, "79991234500")
    product = order["productOrderItem"][0]["product"]
    assert api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"] == "pendingActive"
    assert state(api, order) == "inProgress"  # waits for the network

    tick(db_conn, api.clock.now())
    assert api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"] == "active"
    assert _states(db_conn, "TMF637", "product")[-1] == "active"
    assert _states(db_conn, "TMF622", "productOrder")[-1] == "completed"


def test_an_offering_that_is_not_a_line_sends_nothing(api, db_conn, stub):
    off, acc = shop(api, db_conn, numbers=False)
    order = apidata.order(api, acc, apidata.add_item(off))  # no number, no profile
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    assert db_conn.execute("SELECT count(*) FROM network_command").fetchone() == (0,)
    assert stub.received == {}


def test_temporary_error_keeps_the_item_in_progress_and_retries_with_backoff(
    api, db_conn, world, stub
):
    order = buy(api, world, "79990000001")
    now = api.clock.now()
    tick(db_conn, now)
    assert state(api, order) == "inProgress"
    assert commands_of(db_conn, order) == [("activate", "pending", 1)]
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (0,)
    assert len(stub.attempts) == 1
    tick(db_conn, now + timedelta(seconds=59))  # backoff: not sent again
    assert len(stub.attempts) == 1
    stub.recover("79990000001")
    tick(db_conn, now + timedelta(minutes=1))
    assert state(api, order) == "completed"
    assert commands_of(db_conn, order) == [("activate", "confirmed", 2)]
    assert len(set(stub.attempts)) == 1 and len(stub.received) == 1  # one key, one command


def test_exhausted_commands_wait_for_an_operator_retry_that_resends_the_same_command(
    api, db_conn, world, stub, monkeypatch
):
    monkeypatch.setattr(settings, "network_max_attempts", 2)
    order = buy(api, world, "79990000001")
    now = api.clock.now()
    tick(db_conn, now)
    tick(db_conn, now + timedelta(minutes=1))
    assert commands_of(db_conn, order) == [("activate", "exhausted", 2)]
    tick(db_conn, now + timedelta(days=1))  # no automatic attempts are left
    assert len(stub.attempts) == 2 and state(api, order) == "inProgress"
    (cmd_id,) = [r[0] for r in db_conn.execute("SELECT id FROM network_command").fetchall()]
    first_request = db_conn.execute("SELECT request FROM network_command").fetchone()[0]

    listed = api.get(f"{NET}?productOrder.id={order['id']}").json()
    assert [c["state"] for c in listed] == ["exhausted"]
    assert api.get(f"{NET}/{cmd_id}").json()["lastError"].startswith("stub:")
    stub.recover("79990000001")
    retried = api.post(f"{NET}/{cmd_id}/retry", None)
    assert retried.status_code == 202 and retried.json()["state"] == "pending"
    assert api.post(f"{NET}/{cmd_id}/retry", None).status_code == 409  # pending: nothing to retry
    tick(db_conn, now + timedelta(days=1, minutes=1))
    assert state(api, order) == "completed"
    assert set(stub.attempts) == {str(cmd_id)}
    assert db_conn.execute("SELECT request FROM network_command").fetchone()[0] == first_request
    assert api.post(f"{NET}/{cmd_id}/retry", None).status_code == 409  # confirmed


def test_a_refused_activate_fails_the_order_and_leaves_nothing_charged(api, db_conn, world, stub):
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")
    before = funding.load(db_conn, UUID(world.acc["id"])).spendable
    db_conn.commit()
    order = buy(api, world, "79990000000")
    assert funding.load(db_conn, UUID(world.acc["id"])).spendable < before  # held
    db_conn.commit()
    tick(db_conn, api.clock.now())
    final = api.get(order["href"]).json()
    assert final["state"] == "failed"
    assert errors(final) == ["networkRefused"]
    assert "refused activate" in final["productOrderErrorMessage"][0]["reason"]
    assert final["productOrderItem"][0]["state"] == "failed"
    assert commands_of(db_conn, order) == [("activate", "refused", 1)]
    # the product is aborted (provisioning failed, TMF637; not cancelled, which is a
    # withdrawal) at its start date with zero billable days, and nothing was charged
    assert db_conn.execute(
        "SELECT status, start_date = termination_date FROM product"
    ).fetchone() == (
        "aborted",
        True,
    )
    assert _states(db_conn, "TMF637", "product") == ["aborted"]
    assert _states(db_conn, "TMF622", "productOrder") == ["failed"]
    assert db_conn.execute(
        "SELECT (SELECT count(*) FROM funded_interval),"
        " (SELECT count(*) FROM applied_customer_billing_rate),"
        " (SELECT count(*) FROM customer_bill)"
    ).fetchone() == (0, 0, 0)
    assert db_conn.execute(
        "SELECT count(*) FROM funding_hold WHERE released_at IS NULL"
    ).fetchone() == (0,)
    assert funding.load(db_conn, UUID(world.acc["id"])).spendable == before
    db_conn.commit()
    assert db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE cancelled_at IS NULL"
    ).fetchone() == (0,)
    (cmd_id,) = db_conn.execute("SELECT id FROM network_command").fetchone()
    assert api.post(f"{NET}/{cmd_id}/retry", None).status_code == 409  # a refusal is final
    tick(db_conn, api.clock.now() + timedelta(days=1))
    assert len(stub.attempts) == 1


def test_suspend_and_resume_send_suspend_and_restore(api, db_conn, world, stub):
    product = line(api, db_conn, world)
    order = apidata.order(api, world.acc, status_item(product, "suspended"))
    assert order["state"] == "inProgress"
    shown = api.get(f"{apidata.PRODUCT}/{product['id']}").json()
    assert shown["status"] == "active"
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    assert api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"] == "suspended"
    api.clock.set(api.clock.now() + timedelta(days=1))  # one status change per day
    resumed = apidata.order(api, world.acc, status_item(product, "active"))
    assert resumed["state"] != "rejected", errors(resumed)
    tick(db_conn, api.clock.now())
    assert state(api, resumed) == "completed"
    assert [k for k, _ in stub.received.values()] == ["activate", "suspend", "restore"]
    key = str(repo.command_id(item_uuid(db_conn, order), "suspend"))
    assert stub.received[key][1]["profile"] == PROFILE


@pytest.mark.parametrize("kind", ["suspend", "restore"])
def test_a_refused_status_change_leaves_the_product_as_it_was(api, db_conn, world, stub, kind):
    product = line(api, db_conn, world)
    if kind == "restore":
        apidata.order(api, world.acc, status_item(product, "suspended"))
        tick(db_conn, api.clock.now())
        api.clock.set(api.clock.now() + timedelta(days=1))
    wanted = "suspended" if kind == "suspend" else "active"
    stub.refuse_kinds.add(kind)
    before = api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"]
    changes = db_conn.execute("SELECT count(*) FROM product_status_change").fetchone()[0]
    order = apidata.order(api, world.acc, status_item(product, wanted))
    tick(db_conn, api.clock.now())
    final = api.get(order["href"]).json()
    assert final["state"] == "failed" and errors(final) == ["networkRefused"]
    assert api.get(f"{apidata.PRODUCT}/{product['id']}").json()["status"] == before
    assert db_conn.execute("SELECT count(*) FROM product_status_change").fetchone()[0] == changes
    assert db_conn.execute("SELECT manually_suspended FROM product").fetchone()[0] == (
        kind == "restore"
    )


def test_terminate_sends_deactivate_and_credits_after_the_confirmation(api, db_conn, world, stub):
    product = line(api, db_conn, world)
    order = terminate(api, world.acc, product)
    assert order["state"] == "inProgress"
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE charge_type = 'refund'"
    ).fetchone() == (0,)
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    assert [k for k, _ in stub.received.values()] == ["activate", "deactivate"]
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE charge_type = 'refund'"
    ).fetchone() == (1,)


def test_a_refused_deactivate_does_not_end_the_product_or_its_billing(api, db_conn, world, stub):
    product = line(api, db_conn, world)
    stub.refuse_kinds.add("deactivate")
    order = terminate(api, world.acc, product)
    tick(db_conn, api.clock.now())
    final = api.get(order["href"]).json()
    assert final["state"] == "failed" and errors(final) == ["networkRefused"]
    shown = api.get(f"{apidata.PRODUCT}/{product['id']}").json()
    assert shown["status"] == "active" and "terminationDate" not in shown
    assert db_conn.execute(
        "SELECT status, termination_date, terminated_by_item_id FROM product"
    ).fetchone() == ("active", None, None)
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE charge_type = 'refund'"
    ).fetchone() == (0,)
    assert db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE released_on IS NOT NULL"
    ).fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == (1,)
    # the product still renews: billing was not ended
    tick(db_conn, datetime.combine(product["due"], time(0, 5), tz()))
    assert db_conn.execute(
        "SELECT count(*) FROM prepaid_command WHERE kind = 'renewal'"
    ).fetchone() == (1,)


def tz():
    from tests.builders.prepaid.prepaid_posting import (
        TZ,
    )

    return TZ


def held(conn):
    return conn.execute(
        "SELECT coalesce(sum(h.amount), 0) FROM funding_command_hold c"
        " JOIN funding_hold h ON h.id = c.hold_id WHERE h.released_at IS NULL"
    ).fetchone()[0]


def test_an_offering_change_inside_the_interval_sends_change_profile_of_the_new_offering(
    api, db_conn, world, cheaper, stub
):
    product = line(api, db_conn, world)
    today = api.clock.now().date()
    order = switch(api, world.acc, product, cheaper, today)
    assert order["state"] == "inProgress" and held(db_conn) == D("6.00")
    assert [k for k, _ in stub.received.values()] == ["activate"]
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed" and held(db_conn) == 0
    assert offering_of(api, product) == cheaper["id"]
    kind, request = list(stub.received.values())[-1]
    assert kind == "change_profile"
    assert request["profile"] == PROFILE | {"dataAllowanceMb": 1000, "voiceMinutes": 60}


def test_a_refused_change_profile_leaves_the_product_on_its_offering(
    api, db_conn, world, cheaper, stub
):
    product = line(api, db_conn, world)
    stub.refuse_kinds.add("change_profile")
    grants = db_conn.execute("SELECT count(*) FROM funded_interval").fetchone()
    order = switch(api, world.acc, product, cheaper, api.clock.now().date())
    tick(db_conn, api.clock.now())
    final = api.get(order["href"]).json()
    assert final["state"] == "failed" and errors(final) == ["networkRefused"]
    assert offering_of(api, product) == world.off["id"]
    assert held(db_conn) == 0
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM funded_interval").fetchone() == grants
    assert db_conn.execute(
        "SELECT count(*) FROM applied_customer_billing_rate WHERE charge_type = 'refund'"
    ).fetchone() == (0,)


def test_an_offering_change_at_the_renewal_waits_for_the_network(
    api, db_conn, world, cheaper, stub
):
    product = line(api, db_conn, world)
    order = switch(api, world.acc, product, cheaper, product["due"])
    assert order["state"] == "inProgress"  # completes with the network's answer
    tick(db_conn, api.clock.now())
    assert state(api, order) == "inProgress" and len(stub.received) == 1  # due at the renewal
    pay(db_conn, UUID(world.acc["id"]), "18", api.clock.now())
    tick(db_conn, at(product["due"], 0))
    assert state(api, order) == "completed"
    assert list(stub.received.values())[-1][0] == "change_profile"
    assert applied(db_conn, product)
    api.clock.set(at(product["due"]))
    assert offering_of(api, product) == cheaper["id"]


def test_a_refused_change_profile_at_the_renewal_drops_the_change(
    api, db_conn, world, cheaper, stub
):
    product = line(api, db_conn, world)
    stub.refuse_kinds.add("change_profile")
    order = switch(api, world.acc, product, cheaper, product["due"])
    pay(db_conn, UUID(world.acc["id"]), "18", api.clock.now())
    tick(db_conn, at(product["due"], 0))
    assert state(api, order) == "failed"
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (0,)
    api.clock.set(at(product["due"]))
    assert offering_of(api, product) == world.off["id"]
    tick(db_conn, at(product["due"], 1))  # the renewal goes on with the old offering
    assert db_conn.execute(
        "SELECT quote->>'offering_id' FROM funded_interval WHERE command_kind = 'renewal'"
    ).fetchone() == (world.off["id"],)


def test_the_profile_is_edited_on_the_offering_and_sent_unchanged(api, db_conn, world, stub):
    # an offering narrows a value the specification offers; any other value is a 400
    make_line(api, world.off, dataAllowanceMb=[5000, 10000, 20000])

    def narrowed(value):
        return {
            "@type": "ProductSpecCharacteristicValueUse",
            "name": "dataAllowanceMb",
            "valueType": "integer",
            "productSpecCharacteristicValue": [
                {
                    "@type": "IntegerCharacteristicValueSpecification",
                    "valueType": "integer",
                    "value": value,
                }
            ],
        }

    use, bad = narrowed(10000), narrowed(7)
    rejected = api.patch(world.off["href"], {"prodSpecCharValueUse": [bad]})
    assert rejected.status_code == 400, rejected.text
    ok = api.patch(world.off["href"], {"prodSpecCharValueUse": [use]})
    assert ok.status_code == 200, ok.text
    order = buy(api, world, "79991234500")
    # a later edit does not change what this order sends
    assert (
        api.patch(world.off["href"], {"prodSpecCharValueUse": [narrowed(20000)]}).status_code == 200
    )
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    (_, request) = next(iter(stub.received.values()))
    assert request["profile"] == PROFILE | {"dataAllowanceMb": 10000}
    assert (
        db_conn.execute("SELECT profile FROM network_command").fetchone()[0] == request["profile"]
    )
    names = [c["name"] for c in request["serviceOrderItem"]["service"]["serviceCharacteristic"]]
    assert names == sorted(PROFILE)


def test_profile_characteristics_take_their_own_types(api, world):
    href = world.off["productSpecification"]["href"]
    for name, value in (("hotspot", 2), ("smsCount", -1), ("throttleKbps", 1.5)):
        response = api.patch(href, {"productSpecCharacteristic": characteristics(**{name: value})})
        assert response.status_code in (400, 422), (name, response.text)
    other = api.patch(href, {"productSpecCharacteristic": characteristics(dataAllowanceMb=3)[:1]})
    assert other.status_code == 200, other.text


def test_profile_refusals_name_the_characteristic(api, world):
    href = world.off["productSpecification"]["href"]
    valueless = characteristics()
    del valueless[0]["characteristicValueSpecification"][0]["value"]
    missing = api.patch(href, {"productSpecCharacteristic": valueless})
    assert missing.status_code == 400, missing.text
    assert missing.json()["message"] == (
        "productSpecCharacteristic: dataAllowanceMb: the network profile takes integers of"
        " zero or more"
    )
    empty = characteristics()
    empty[0]["characteristicValueSpecification"] = []
    none = api.patch(href, {"productSpecCharacteristic": empty})
    assert none.status_code == 400, none.text
    assert none.json()["message"] == (
        "productSpecCharacteristic: dataAllowanceMb: at least one"
        " characteristicValueSpecification is needed"
    )
    make_line(api, world.off)
    use = {
        "@type": "ProductSpecCharacteristicValueUse",
        "name": "roamingDataMb",
        "valueType": "integer",
        "productSpecCharacteristicValue": [
            {"@type": "IntegerCharacteristicValueSpecification", "valueType": "integer",
             "value": 500}
        ],
    }  # fmt: skip
    undeclared = api.patch(world.off["href"], {"prodSpecCharValueUse": [use]})
    assert undeclared.status_code == 400, undeclared.text
    assert undeclared.json()["message"] == (
        "prodSpecCharValueUse: roamingDataMb: the productSpecification declares no such"
        " characteristic"
    )
    assert "prodSpecCharValueUse" not in api.get(world.off["href"]).json()


def test_a_send_is_idempotent_across_a_worker_restart(api, db_conn, world, stub, monkeypatch):
    from app.worker import db as worker_db

    buy(api, world, "79991234500")
    now = api.clock.now()
    assert worker_db.acquire_ownership(db_conn)
    db_conn.commit()

    def crash(*args, **kwargs):
        raise RuntimeError("worker died after the network answered")

    monkeypatch.setattr(repo, "confirm", crash)
    with pytest.raises(RuntimeError):
        network.process_network(db_conn, clock=FixedClock(now), tz=tz())
    monkeypatch.undo()
    assert db_conn.execute("SELECT state, attempts FROM network_command").fetchone() == (
        "pending",
        0,
    )
    # the restarted worker sends the same key again; the network knows it already
    tick(db_conn, now)
    tick(db_conn, now + timedelta(minutes=5))  # and nothing is sent twice afterwards
    assert len(stub.attempts) == 2 and len(set(stub.attempts)) == 1 and len(stub.received) == 1
    assert db_conn.execute("SELECT count(*) FROM network_command").fetchone() == (1,)


def test_recording_a_command_twice_returns_the_same_command(api, db_conn, world):
    order = buy(api, world, "79991234500")
    row = db_conn.execute(
        "SELECT * FROM network_command WHERE product_order_id = %s", (order["id"],)
    ).fetchone()
    again = repo.insert(
        db_conn,
        order_id=UUID(order["id"]),
        order_item_id=item_uuid(db_conn, order),
        product_id=None,
        kind="activate",
        due_date=api.clock.now().date(),
        profile={},
        now=api.clock.now(),
    )
    assert again["id"] == row[0] and again["profile"] == PROFILE
    assert db_conn.execute("SELECT count(*) FROM network_command").fetchone() == (1,)


def test_cancelling_an_order_cancels_its_unsent_command_but_not_a_confirmed_one(
    api, db_conn, world, stub
):
    order = buy(api, world, "79991234500")
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert commands_of(db_conn, order) == [("activate", "cancelled", 0)]
    tick(db_conn, api.clock.now())
    assert stub.attempts == []

    done = buy(api, world, "79991234501")
    tick(db_conn, api.clock.now())
    assert state(api, done) == "completed"
    assert api.post(CANCEL, cancel_body(done)).status_code == 409


def test_an_unknown_adapter_fails_startup(monkeypatch):
    monkeypatch.setenv("BSS_NETWORK_ADAPTER", "tmobile")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("BSS_NETWORK_ADAPTER", "stub")
    assert Settings().network_adapter == "stub"
    monkeypatch.delenv("BSS_NETWORK_ADAPTER")
    assert Settings().network_adapter == "stub"


def cancel_refused(api, order, text):
    before = api.get(order["href"]).json()
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 409, response.text
    assert text in response.json()["message"], response.json()["message"]
    assert api.get(order["href"]).json() == before


def test_an_offering_change_waiting_for_the_network_at_the_renewal_cannot_be_cancelled(
    api, db_conn, world, cheaper, stub
):
    product = line(api, db_conn, world)
    order = switch(api, world.acc, product, cheaper, product["due"])
    assert order["state"] == "inProgress"
    cancel_refused(api, order, "an offering change waiting for the network cannot be cancelled")
    assert db_conn.execute("SELECT count(*) FROM product_offering_change").fetchone() == (1,)


def test_a_refused_offering_change_cannot_be_cancelled(api, db_conn, world, cheaper, stub):
    product = line(api, db_conn, world)
    stub.refuse_kinds.add("change_profile")
    order = switch(api, world.acc, product, cheaper, api.clock.now().date())
    tick(db_conn, api.clock.now())
    assert state(api, order) == "failed"
    cancel_refused(api, order, f"productOrder {order['id']} is failed: it cannot be cancelled")


def test_a_line_keeps_a_number_an_empty_resource_list_would_drop(api, db_conn, world, stub):
    product = line(api, db_conn, world)
    item = {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "modify",
        "product": {"id": product["id"], "@type": "ProductRef", "realizingResource": []},
    }
    order = apidata.order(api, world.acc, item)
    assert order["state"] == "rejected" and errors(order) == ["numberRequired"]
    shown = api.get(f"{apidata.PRODUCT}/{product['id']}").json()
    assert len(shown["realizingResource"]) == 1
