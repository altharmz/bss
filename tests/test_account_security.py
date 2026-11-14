"""Account security (issue #193): the account PIN, CPNI verification of sensitive actions, line
locks, the audit log and the change alerts. docs/decisions/accounts.md."""
# ruff: noqa: F811

import logging
from datetime import timedelta

import pytest

from app.config import settings
from app.integrations.notify import adapter as sender
from tests import apidata
from tests.apiclient import total
from tests.apidata import ACCOUNT, CUSTOMER, ORDER, PARTY
from tests.test_api_account_attributes import CONTACT
from tests.test_api_resource_inventory import number
from tests.test_imsi import all_refs
from tests.test_network_provisioning import stub, world  # noqa: F401 - fixtures
from tests.test_notifications import contact, customer_of, of_event, quiet
from tests.test_prepaid_renewal_producer import tick
from tests.test_sim_inventory import (  # noqa: F401 - fixtures
    activate_line,
    by_imsi,
    line_world,
    live_product,
    msisdn_of,
    sim,
    sim_row,
    stock,
)

pytestmark = pytest.mark.capability("worker.accountSecurity")

SEC = "/internal/v1/security"
PORT = "/internal/v1/port"
PIN_URL = "/internal/v1/port/pin"
PIN = "482913"
OTHER = "135790"


@pytest.fixture(autouse=True)
def gate(monkeypatch):
    monkeypatch.setattr(settings, "account_pin_required", True)


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


def head(pin):
    return {} if pin is None else {"X-Account-Pin": pin}


def set_pin(api, customer, pin=PIN, reason="identity checked at the counter"):
    return api.put(f"{SEC}/pin/{customer}", {"pin": pin, "reason": reason})


def audit(api, **filters):
    query = "&".join(f"{k}={v}" for k, v in filters.items())
    response = api.get(f"{SEC}/audit?limit=100" + (f"&{query}" if query else ""))
    assert response.status_code == 200, response.text
    return response.json()


def swap(api, db_conn, world, product, new_sim, pin=None):
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product,
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(msisdn_of(db_conn, product), new_sim),
        },
    }
    return api.post(ORDER, apidata.order_body(world.acc, item), headers=head(pin))


def renumber(api, db_conn, world, product, new_number, pin=None):
    current_sim = db_conn.execute(
        "SELECT n.id FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE a.product_id = %s AND a.cancelled_at IS NULL"
        " AND a.resource_specification_id = 'imsi'",
        (product,),
    ).fetchone()[0]
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product,
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(new_number, str(current_sim)),
        },
    }
    return api.post(ORDER, apidata.order_body(world.acc, item), headers=head(pin))


def errors(api, order):
    return [e["code"] for e in api.get(order["href"]).json().get("productOrderErrorMessage", [])]


@pytest.fixture
def line(api, db_conn, line_world, stub):
    """A live prepaid line with a customer who has a PIN, and two spare SIMs."""
    stock(api, "B1", "physical", 3)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    customer = str(customer_of(db_conn, line_world.acc))
    assert set_pin(api, customer).status_code == 200
    return type(
        "Line",
        (),
        {
            "world": line_world,
            "product": live_product(db_conn),
            "customer": customer,
            "spare": by_imsi(api, sim(2)["imsi"])["id"],
            "spare2": by_imsi(api, sim(3)["imsi"])["id"],
        },
    )


# --- the PIN ---


def test_a_pin_is_set_hashed_and_never_returned(api, db_conn, line, caplog):
    caplog.set_level(logging.DEBUG)
    status = api.get(f"{SEC}/pin/{line.customer}").json()
    assert status["set"] and status["setBy"] and status["failedAttempts"] == 0
    assert set(status) == {
        "customer", "set", "setAt", "setBy", "reason", "failedAttempts", "lockedUntil",
    }  # fmt: skip
    stored = db_conn.execute("SELECT pin_hash FROM customer_pin").fetchone()[0]
    db_conn.commit()
    assert PIN not in stored and stored.startswith("scrypt.")
    # the PIN is in no answer of any API, in no log and in no audit row
    swap(api, db_conn, line.world, line.product, line.spare, pin=PIN)
    texts = [
        api.get(f"{SEC}/pin/{line.customer}").text,
        api.get(f"{SEC}/lock/{line.product}").text,
        api.get(f"{SEC}/audit").text,
        api.get(f"{CUSTOMER}/{line.customer}").text,
        caplog.text,
    ]
    assert not [t for t in texts if PIN in t]
    rows = db_conn.execute("SELECT detail::text, reason FROM account_audit").fetchall()
    db_conn.commit()
    assert not [r for r in rows if PIN in r[0] or PIN in (r[1] or "")]
    # the PIN of a rejected request is not echoed either
    bad = set_pin(api, line.customer, pin="12ab")
    assert bad.status_code == 422 and "12ab" not in bad.text


def test_a_pin_needs_6_to_8_digits_and_a_reason(api, line):
    for pin in ("12345", "123456789", "abcdef"):
        assert set_pin(api, line.customer, pin=pin).status_code == 422
    assert set_pin(api, line.customer, reason="  ").status_code == 422
    assert set_pin(api, line.customer, pin="12345678").status_code == 200
    unknown = set_pin(api, "00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404
    assert api.put(f"{SEC}/pin/{line.customer}", {"pin": PIN}).status_code == 422


def test_setting_then_resetting_a_pin_is_audited_with_the_reason(api, line):
    assert set_pin(api, line.customer, pin=OTHER, reason="customer forgot it").status_code == 200
    entries = audit(api, **{"customer.id": line.customer})
    assert [e["action"] for e in entries] == ["pin_reset", "pin_set"]
    assert entries[0]["reason"] == "customer forgot it" and entries[0]["outcome"] == "done"
    assert entries[0]["operator"] and entries[0]["verification"] == "none"


# --- SIM swap ---


def test_a_sim_swap_needs_a_valid_pin(api, db_conn, line):
    spare = line.spare
    cases = [(None, "pinRequired"), (OTHER, "pinInvalid")]
    for pin, code in cases:
        order = swap(api, db_conn, line.world, line.product, spare, pin=pin).json()
        assert order["state"] == "rejected" and errors(api, order) == [code]
        assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)  # nothing moved
    ok = swap(api, db_conn, line.world, line.product, spare, pin=PIN).json()
    assert ok["state"] == "inProgress"
    entries = audit(api, action="sim_swap")
    assert sorted((e["outcome"], e["reason"]) for e in entries) == [
        ("done", None), ("refused", "pinInvalid"), ("refused", "pinRequired"),
    ]  # fmt: skip
    done = next(e for e in entries if e["outcome"] == "done")
    assert done["verification"] == "pin" and done["product"]["id"] == line.product
    assert done["detail"]["productOrder"] == ok["id"]


def test_a_customer_without_a_pin_must_get_one_first(api, db_conn, line_world, stub):
    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    order = swap(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"], pin=PIN)
    assert order.json()["state"] == "rejected" and errors(api, order.json()) == ["pinNotSet"]
    assert [e["reason"] for e in audit(api, action="sim_swap")] == ["pinNotSet"]


def test_the_gate_switch_off_asks_for_no_pin_but_still_audits(api, db_conn, line, monkeypatch):
    monkeypatch.setattr(settings, "account_pin_required", False)
    order = swap(api, db_conn, line.world, line.product, line.spare).json()
    assert order["state"] == "inProgress"
    (entry,) = audit(api, action="sim_swap")
    assert entry["outcome"] == "done" and entry["verification"] == "notRequired"


def test_a_swap_in_a_draft_is_checked_when_it_is_submitted(api, db_conn, line):
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": line.product,
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(msisdn_of(db_conn, line.product), line.spare),
        },
    }
    body = apidata.order_body(line.world.acc, item) | {"requestedInitialState": "draft"}
    draft = api.post(ORDER, body).json()
    assert draft["state"] == "draft"
    refused = api.patch(draft["href"], {"state": "acknowledged"}).json()
    assert refused["state"] == "rejected"
    draft = api.post(ORDER, body).json()
    done = api.patch(draft["href"], {"state": "acknowledged"}, headers=head(PIN)).json()
    assert done["state"] == "inProgress"


# --- number change ---


def test_a_number_change_needs_a_valid_pin(api, db_conn, line):
    new = number(api, value="79991234599")["id"]
    for pin, code in ((None, "pinRequired"), (OTHER, "pinInvalid")):
        order = renumber(api, db_conn, line.world, line.product, new, pin).json()
        assert order["state"] == "rejected" and errors(api, order) == [code]
    order = renumber(api, db_conn, line.world, line.product, new, PIN).json()
    assert order["state"] == "completed"
    entries = audit(api, action="number_change")
    assert sorted((e["outcome"], e["reason"]) for e in entries) == [
        ("done", None), ("refused", "pinInvalid"), ("refused", "pinRequired"),
    ]  # fmt: skip


# --- Number Transfer PIN ---


def test_a_number_transfer_pin_needs_a_valid_pin(api, line):
    body = {"productId": line.product}
    response = api.post(PIN_URL, body)
    assert response.status_code == 403 and response.json()["code"] == "pinRequired"
    response = api.post(PIN_URL, body, headers=head(OTHER))
    assert response.status_code == 403 and response.json()["code"] == "pinInvalid"
    assert api.get(f"{PIN_URL}?product.id={line.product}").json() == []  # none was issued
    issued = api.post(PIN_URL, body, headers=head(PIN))
    assert issued.status_code == 201 and issued.json()["pin"]
    entries = audit(api, action="number_transfer_pin")
    assert sorted((e["outcome"], e["reason"]) for e in entries) == [
        ("done", None), ("refused", "pinInvalid"), ("refused", "pinRequired"),
    ]  # fmt: skip
    assert issued.json()["pin"] not in api.get(f"{SEC}/audit").text


# --- contact and address change ---


def party_of(db_conn, customer):
    row = db_conn.execute("SELECT party_id FROM customer WHERE id = %s", (customer,)).fetchone()
    db_conn.commit()
    return str(row[0])


MEDIUM = [{"@type": "EmailContactMedium", "emailAddress": "new@example.org"}]
# the customer API takes no typed medium in a PATCH today (its nested @type is refused): an empty
# list is a change from "none stored" and exercises the same gate
CUSTOMER_MEDIUM = []


def person(db_conn, customer):
    return f"{PARTY}/individual/{party_of(db_conn, customer)}"


def test_a_contact_change_needs_a_valid_pin_on_a_party_a_customer_and_an_account(
    api, db_conn, line
):
    party = f"{PARTY}/individual/{party_of(db_conn, line.customer)}"
    customer = f"{CUSTOMER}/{line.customer}"
    account = f"{ACCOUNT}/{line.world.acc['id']}"
    targets = [(party, "contactMedium", MEDIUM), (customer, "contactMedium", CUSTOMER_MEDIUM)]
    targets.append((account, "contact", CONTACT))
    for url, key, value in targets:
        refused = api.patch(url, {key: value})
        assert refused.status_code == 403 and refused.json()["code"] == "pinRequired", (
            url,
            refused.text,
        )
        wrong = api.patch(url, {key: value}, headers=head(OTHER))
        assert wrong.status_code == 403 and wrong.json()["code"] == "pinInvalid"
        assert key not in api.get(url).json() or api.get(url).json()[key] != value
        ok = api.patch(url, {key: value}, headers=head(PIN))
        assert ok.status_code == 200, ok.text
    entries = audit(api, action="contact_change")
    assert sorted((e["outcome"], e["reason"]) for e in entries) == [
        *[("done", None)] * 3, *[("refused", "pinInvalid")] * 3, *[("refused", "pinRequired")] * 3,
    ]  # fmt: skip


def test_a_change_that_leaves_the_contact_as_it_is_needs_no_pin(api, db_conn, line):
    party = person(db_conn, line.customer)
    assert api.patch(party, {"contactMedium": MEDIUM}, headers=head(PIN)).status_code == 200
    again = api.patch(party, {"contactMedium": MEDIUM})
    assert again.status_code == 200
    assert api.patch(party, {"givenName": "Renamed"}).status_code == 200  # not a contact
    assert len(audit(api, action="contact_change")) == 1


def test_a_customer_without_a_live_line_changes_contacts_freely(api, db_conn, line_world):
    customer = customer_of(db_conn, line_world.acc)
    response = api.patch(person(db_conn, customer), {"contactMedium": MEDIUM})
    assert response.status_code == 200, response.text
    assert audit(api) == []  # nothing to take over: not a sensitive action


# --- lockout ---


def test_wrong_pins_lock_the_verification_even_for_the_right_one(api, db_conn, line):
    for _ in range(settings.pin_max_attempts):
        order = swap(api, db_conn, line.world, line.product, line.spare, pin=OTHER).json()
        assert errors(api, order) == ["pinInvalid"]
    status = api.get(f"{SEC}/pin/{line.customer}").json()
    assert status["lockedUntil"] is not None
    locked = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert errors(api, locked) == ["pinLocked"]
    response = api.post(PIN_URL, {"productId": line.product}, headers=head(PIN))
    assert response.status_code == 403 and response.json()["code"] == "pinLocked"
    (lockout,) = audit(api, action="pin_lockout")
    assert lockout["customer"]["id"] == line.customer
    api.clock.set(api.clock.now() + timedelta(minutes=settings.pin_lockout_minutes, seconds=1))
    ok = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert ok["state"] == "inProgress"
    assert api.get(f"{SEC}/pin/{line.customer}").json()["failedAttempts"] == 0


def test_a_right_pin_resets_the_count_and_a_reset_clears_a_lockout(api, db_conn, line):
    for _ in range(settings.pin_max_attempts - 1):
        swap(api, db_conn, line.world, line.product, line.spare, pin=OTHER)
    assert api.get(f"{SEC}/pin/{line.customer}").json()["failedAttempts"] == 4
    ok = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert ok["state"] == "inProgress"
    assert api.get(f"{SEC}/pin/{line.customer}").json()["failedAttempts"] == 0
    for _ in range(settings.pin_max_attempts):
        swap(api, db_conn, line.world, line.product, line.spare2, pin=OTHER)
    assert api.get(f"{SEC}/pin/{line.customer}").json()["lockedUntil"] is not None
    set_pin(api, line.customer, pin=OTHER, reason="customer called in")
    assert api.get(f"{SEC}/pin/{line.customer}").json()["lockedUntil"] is None


# --- locks ---


def lock(api, product, *, sim_swap=False, port_out=False, pin=None, reason="customer asked"):
    body = {"simSwap": sim_swap, "portOut": port_out, "reason": reason}
    return api.put(f"{SEC}/lock/{product}", body, headers=head(pin))


def test_a_sim_swap_lock_refuses_a_swap_even_with_a_valid_pin(api, db_conn, line):
    assert api.get(f"{SEC}/lock/{line.product}").json()["simSwap"] is False
    assert lock(api, line.product, sim_swap=True).status_code == 200  # setting needs no PIN
    order = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert order["state"] == "rejected" and errors(api, order) == ["simSwapLocked"]
    assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)
    refused = [e for e in audit(api, action="sim_swap") if e["outcome"] == "refused"]
    assert [e["reason"] for e in refused] == ["simSwapLocked"]
    # clearing it is a sensitive action: the PIN is needed
    assert lock(api, line.product).status_code == 403
    assert lock(api, line.product, pin=OTHER).status_code == 403
    assert api.get(f"{SEC}/lock/{line.product}").json()["simSwap"] is True
    assert lock(api, line.product, pin=PIN).status_code == 200
    ok = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert ok["state"] == "inProgress"
    actions = sorted(e["action"] for e in audit(api) if e["action"].startswith("lock"))
    assert actions == ["lock_cleared"] * 3 + ["lock_set"]  # two refused clearings, one done


def test_a_port_out_lock_refuses_the_transfer_pin_and_a_carrier_port_out(api, db_conn, line, stub):
    issued = api.post(PIN_URL, {"productId": line.product}, headers=head(PIN)).json()["pin"]
    assert lock(api, line.product, port_out=True).status_code == 200
    blocked = api.post(PIN_URL, {"productId": line.product}, headers=head(PIN))
    assert blocked.status_code == 403 and blocked.json()["code"] == "portOutLocked"
    # a PIN issued before the lock does not help: the carrier's request is rejected
    stub.inject_port_out("OUT-1", "79991234500", issued)
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["state"] == "rejected" and out["rejectionCode"] == "portOutLocked"
    assert out["answeredAt"] is not None
    status = db_conn.execute("SELECT status FROM product WHERE id = %s", (line.product,))
    assert status.fetchone() == ("active",)
    db_conn.commit()
    (entry,) = audit(api, action="port_out")
    assert entry["outcome"] == "refused" and entry["reason"] == "portOutLocked"
    assert entry["operator"] == "carrier" and entry["product"]["id"] == line.product


def test_an_approved_port_out_is_audited(api, db_conn, line, stub):
    issued = api.post(PIN_URL, {"productId": line.product}, headers=head(PIN)).json()["pin"]
    stub.inject_port_out("OUT-2", "79991234500", issued)
    tick(db_conn, api.clock.now())
    (out,) = api.get(f"{PORT}?direction=out").json()
    assert out["state"] == "completed"
    (entry,) = audit(api, action="port_out")
    assert entry["outcome"] == "done" and entry["detail"]["number"] == "79991234500"


def test_locks_need_a_reason_and_a_known_line(api, line):
    assert lock(api, line.product, sim_swap=True, reason="x").status_code == 422
    missing = lock(api, "00000000-0000-4000-8000-000000000000", sim_swap=True)
    assert missing.status_code == 404
    current = api.get(f"{SEC}/lock/{line.product}").json()
    assert current["simSwap"] is False and current["portOut"] is False


# --- the audit log ---


def test_the_audit_log_is_read_only_filterable_and_paged(api, db_conn, line):
    swap(api, db_conn, line.world, line.product, line.spare, pin=OTHER)
    swap(api, db_conn, line.world, line.product, line.spare, pin=PIN)
    everything = audit(api)
    assert len(everything) == 3  # the PIN, a refused swap, a done one
    assert [e["at"] for e in everything] == sorted((e["at"] for e in everything), reverse=True)
    assert len(audit(api, outcome="refused")) == 1
    assert len(audit(api, action="sim_swap")) == 2
    assert len(audit(api, **{"product.id": line.product})) == 2
    assert len(audit(api, **{"billingAccount.id": line.world.acc["id"]})) == 3
    assert audit(api, operator="nobody") == []
    page = api.get(f"{SEC}/audit?limit=1&offset=1")
    assert page.status_code == 200 and len(page.json()) == 1
    assert total(page, api) == "3"
    assert api.get(f"{SEC}/audit?action=bogus").status_code == 400
    assert api.get(f"{SEC}/audit?nope=1").status_code == 400
    for write in (api.post(f"{SEC}/audit", {}), api.put(f"{SEC}/audit", {})):
        assert write.status_code == 405
    assert api.delete(f"{SEC}/audit").status_code == 405
    # nor can the database change it
    import psycopg

    for statement in ("UPDATE account_audit SET operator = 'x'", "DELETE FROM account_audit"):
        with pytest.raises(psycopg.errors.RaiseException):
            db_conn.execute(statement)
        db_conn.rollback()


# --- the admin UI (issue 226) ---


def test_security_requests_as_the_ui_sends_them(api, db_conn, line):
    """The request strings pinned in frontend/src/screens/SubscriberCard.security.test.tsx."""
    json_type = {"Content-Type": "application/json"}

    def put(url, text, pin=None):
        return api.request("PUT", url, content=text, headers={**json_type, **head(pin)})

    reset = put(
        f"{SEC}/pin/{line.customer}", '{"pin":"482913","reason":"identity checked at the counter"}'
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["set"] is True and "pin" not in reset.json()
    assert api.get(f"{SEC}/pin/{line.customer}").json()["reason"] == (
        "identity checked at the counter"
    )
    # setting a lock needs no PIN; clearing it does, sent as X-Account-Pin
    url = f"{SEC}/lock/{line.product}"
    assert put(url, '{"simSwap":false,"portOut":true,"reason":"fraud report"}').status_code == 200
    clear = '{"simSwap":false,"portOut":false,"reason":"customer called"}'
    refused = put(url, clear)
    assert refused.status_code == 403 and refused.json()["code"] == "pinRequired"
    cleared = put(url, clear, pin=PIN)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["portOut"] is False and cleared.json()["reason"] == "customer called"
    # the audit log reads, with the UI's filters and paging
    page = api.get(f"{SEC}/audit?customer.id={line.customer}&offset=0&limit=25")
    assert page.status_code == 200, page.text
    actions = [e["action"] for e in page.json()]
    assert actions[:4] == ["lock_cleared", "lock_cleared", "lock_set", "pin_reset"]
    query = f"customer.id={line.customer}&action=lock_cleared&outcome=refused&offset=0&limit=25"
    (entry,) = api.get(f"{SEC}/audit?{query}").json()
    assert entry["reason"] == "pinRequired" and entry["verification"] == "none"


# --- alerts ---


def alerts(api, action):
    return of_event(api, f"security_{action}")


def test_every_sensitive_action_sends_one_alert_and_a_repeated_tick_none(api, db_conn, line):
    contact(db_conn, line.world.acc)
    assert alerts(api, "pin_set") == []  # nothing is sent before the worker runs
    tick(db_conn, api.clock.now())
    assert len(alerts(api, "pin_set")) == 2  # the earlier PIN set: an SMS and an email
    swap(api, db_conn, line.world, line.product, line.spare, pin=OTHER)  # refused: no alert
    order = swap(api, db_conn, line.world, line.product, line.spare, pin=PIN).json()
    assert order["state"] == "inProgress"
    api.post(PIN_URL, {"productId": line.product}, headers=head(PIN))
    lock(api, line.product, port_out=True)
    api.patch(person(db_conn, line.customer), {"contactMedium": MEDIUM}, headers=head(PIN))
    for _ in range(3):
        tick(db_conn, api.clock.now())
    for action in ("sim_swap", "number_transfer_pin", "lock_set", "contact_change"):
        assert len(alerts(api, action)) == 2, action
    done = [e for e in audit(api) if e["outcome"] == "done"]
    assert len(done) == 5  # the PIN, a swap, a transfer PIN, a lock, a contact change
    total = sum(len(alerts(api, e["action"])) for e in done if e["action"] != "pin_set")
    assert total == 8
    assert alerts(api, "sim_swap")[0]["state"] == "completed"
    assert "79991234500" in alerts(api, "sim_swap")[0]["content"]


def test_a_contact_change_alert_goes_to_the_address_on_record_before_the_change(api, db_conn, line):
    contact(db_conn, line.world.acc, phone="12125550123", email="ada@example.org")
    new = [
        {"@type": "EmailContactMedium", "emailAddress": "thief@example.org"},
        {"@type": "PhoneContactMedium", "phoneNumber": "12125559999"},
    ]
    ok = api.patch(person(db_conn, line.customer), {"contactMedium": new}, headers=head(PIN))
    assert ok.status_code == 200
    tick(db_conn, api.clock.now())
    sent = alerts(api, "contact_change")
    receivers = {r.get("phoneNumber") or r.get("email") for m in sent for r in m["receiver"]}
    assert receivers == {"12125550123", "ada@example.org"}


def test_a_blocked_port_out_and_a_lockout_are_told_to_the_customer(api, db_conn, line, stub):
    contact(db_conn, line.world.acc)
    issued = api.post(PIN_URL, {"productId": line.product}, headers=head(PIN)).json()["pin"]
    lock(api, line.product, port_out=True)
    stub.inject_port_out("OUT-3", "79991234500", issued)
    for _ in range(settings.pin_max_attempts):
        swap(api, db_conn, line.world, line.product, line.spare, pin=OTHER)
    tick(db_conn, api.clock.now())
    assert len(alerts(api, "port_out_blocked")) == 2
    assert len(alerts(api, "pin_lockout")) == 2
    tick(db_conn, api.clock.now())
    assert len(alerts(api, "port_out_blocked")) == 2


def test_a_security_alert_is_not_held_for_the_quiet_hours(api, db_conn, line):
    contact(db_conn, line.world.acc)
    night = quiet(api.clock.now().date(), 23)
    api.clock.set(night)
    set_pin(api, line.customer, pin=OTHER, reason="customer called in")
    tick(db_conn, night)
    (sms,) = [m for m in alerts(api, "pin_reset") if m["messageType"] == "SMS"]
    assert sms["state"] == "completed"
