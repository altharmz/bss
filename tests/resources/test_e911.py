"""The registered E911 address of Wi-Fi calling lines (issue #201)."""
# ruff: noqa: F811

import pytest

from app.config import settings
from app.network import e911
from tests import apidata
from tests.apidata import ORDER
from tests.builders.billing.tax_engine import CA, set_address
from tests.builders.care.account_security import PIN, audit, head, set_pin
from tests.builders.care.notifications import customer_of
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.prepaid.prepaid_offering_changes import switch
from tests.builders.prepaid.prepaid_orders import shop
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.prepaid.prepaid_status_orders import errors
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    make_line,
    stub,
    world,
)
from tests.builders.resources.number_orders import refs

pytestmark = pytest.mark.capability("worker.e911Address")

HOME = {
    "@type": "GeographicAddress",
    "streetNr": "742",
    "streetName": "Evergreen",
    "streetSuffix": "Terrace",
    "city": " Springfield ",
    "stateOrProvince": "OR",
    "postcode": "97403",
    "country": "US",
}
HOME_STORED = {k: v.strip() for k, v in HOME.items() if k != "@type"}
OFFICE = {
    "@type": "GeographicAddress",
    "streetNr": "1",
    "streetName": "Market",
    "streetSuffix": "St",
    "city": "San Francisco",
    "stateOrProvince": "CA",
    "postcode": "94105-1234",
    "country": "US",
}
OFFICE_STORED = {k: v for k, v in OFFICE.items() if k != "@type"}
REFUSED = OFFICE | {"postcode": "00911"}


def place(address):
    return [{"@type": "RelatedPlaceRefOrValue", "role": "e911Address", "place": address}]


@pytest.fixture
def wifi(api, world):
    """Plan A enables Wi-Fi calling; plan B (`world.cheaper`) does not."""
    make_line(api, world.off, wifiCalling=1)
    return world


def add(api, world, value="79991234500", address=None, offering=None):
    item = apidata.add_item(offering or world.off)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value=value)["id"]),
    }
    if address is not None:
        item["product"]["place"] = place(address)
    return api.post(ORDER, apidata.order_body(world.acc, item))


def change(api, world, product_id, address, pin=None, **extra):
    item = {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "modify",
        "product": {"id": product_id, "@type": "ProductRef", "place": place(address), **extra},
    }
    return api.post(ORDER, apidata.order_body(world.acc, item), headers=head(pin))


def stored(db_conn, product_id=None):
    if product_id is None:
        query = "SELECT e911_address FROM product ORDER BY created_at DESC LIMIT 1"
        return db_conn.execute(query).fetchone()[0]
    return db_conn.execute(
        "SELECT e911_address FROM product WHERE id = %s", (product_id,)
    ).fetchone()[0]


def activated(api, db_conn, world, address=None, value="79991234500", offering=None):
    response = add(api, world, value, address, offering)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "inProgress", errors(order)
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "completed"
    (pid,) = db_conn.execute("SELECT id FROM product ORDER BY created_at DESC LIMIT 1").fetchone()
    return str(pid)


def last_sent(stub):
    return list(stub.received.values())[-1]


# --- activation ---


def test_a_wifi_line_takes_the_address_sent_and_the_network_gets_it(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    assert stored(db_conn, product) == HOME_STORED
    kind, request = last_sent(stub)
    assert kind == "activate" and request["profile"]["wifiCalling"] == 1
    assert request["e911Address"] == HOME_STORED
    assert {"name": "e911Address", "value": HOME_STORED} in (
        request["serviceOrderItem"]["service"]["serviceCharacteristic"]
    )
    shown = api.get(f"{apidata.PRODUCT}/{product}").json()
    assert shown["place"] == [
        {
            "role": "e911Address",
            "place": HOME_STORED | {"@type": "GeographicAddress"},
            "@type": "RelatedPlaceRefOrValue",
        }
    ]


def test_without_an_address_the_service_address_is_copied(api, db_conn, wifi, stub):
    assert set_address(api, wifi.acc, CA | {"street2": "Apt 4"}).status_code == 200
    product = activated(api, db_conn, wifi)
    copied = {
        "streetName": CA["street1"],
        "city": CA["city"],
        "stateOrProvince": CA["stateOrProvince"],
        "postcode": CA["postCode"],
        "country": "US",
        "geographicSubAddress": [{"name": "Apt 4", "@type": "GeographicSubAddress"}],
    }
    assert stored(db_conn, product) == copied
    assert last_sent(stub)[1]["e911Address"] == copied
    # copied, not linked: a later service address change leaves the line's address
    other = {"street1": "5 Main St", "city": "Albany", "stateOrProvince": "NY", "postCode": "12207"}
    assert set_address(api, wifi.acc, other).status_code == 200
    assert stored(db_conn, product) == copied


def test_without_any_address_a_wifi_line_is_refused(api, db_conn, wifi, stub):
    response = add(api, wifi)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "rejected" and errors(order) == ["e911AddressRequired"]
    assert stub.received == {}


def test_a_line_without_wifi_calling_keeps_the_address_and_sends_it_when_enabled(
    api, db_conn, world, stub
):
    make_line(api, world.cheaper, dataAllowanceMb=1000, voiceMinutes=60, wifiCalling=1)
    product = activated(api, db_conn, world, HOME)  # plan A: no Wi-Fi calling
    assert stored(db_conn, product) == HOME_STORED
    _, request = last_sent(stub)
    assert "e911Address" not in request and "wifiCalling" not in request["profile"]
    order = switch(api, world.acc, {"id": product}, world.cheaper, api.clock.now().date())
    assert order["state"] == "inProgress", errors(order)
    tick(db_conn, api.clock.now())
    kind, request = last_sent(stub)
    assert kind == "change_profile" and request["e911Address"] == HOME_STORED


def test_a_move_to_a_wifi_plan_without_any_address_is_refused(api, db_conn, world, stub):
    make_line(api, world.cheaper, dataAllowanceMb=1000, voiceMinutes=60, wifiCalling=1)
    product = activated(api, db_conn, world)
    order = switch(api, world.acc, {"id": product}, world.cheaper, api.clock.now().date())
    assert order["state"] == "rejected" and errors(order) == ["e911AddressRequired"]


# --- changes ---


def test_a_change_on_a_wifi_line_waits_for_the_network(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    response = change(api, wifi, product, OFFICE)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "inProgress", errors(order)
    assert stored(db_conn, product) == HOME_STORED  # not before the network confirms
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "completed"
    kind, request = last_sent(stub)
    assert kind == "update_e911" and request["e911Address"] == OFFICE_STORED
    assert request["serviceOrderItem"]["action"] == "modify"
    assert stored(db_conn, product) == OFFICE_STORED
    (entry,) = audit(api, action="e911_changed")
    assert entry["outcome"] == "done" and entry["product"]["id"] == product


def test_a_refused_change_leaves_the_old_address(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    order = change(api, wifi, product, REFUSED).json()
    assert order["state"] == "inProgress", errors(order)
    tick(db_conn, api.clock.now())
    failed = api.get(order["href"]).json()
    assert failed["state"] == "failed"
    assert [(e["code"], e["message"]) for e in failed["productOrderErrorMessage"]] == [
        ("networkRefused", "e911AddressInvalid")
    ]
    assert stored(db_conn, product) == HOME_STORED


def test_an_activation_with_the_refused_address_cancels_the_line(api, db_conn, wifi, stub):
    order = add(api, wifi, address=REFUSED).json()
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "failed"
    assert db_conn.execute("SELECT status FROM product").fetchone() == ("cancelled",)


def test_a_change_on_a_line_without_wifi_calling_is_stored_at_once(api, db_conn, world, stub):
    product = activated(api, db_conn, world, HOME)
    sent = len(stub.received)
    order = change(api, world, product, OFFICE).json()
    assert order["state"] == "completed", errors(order)
    assert stored(db_conn, product) == OFFICE_STORED
    assert len(stub.received) == sent
    assert db_conn.execute(
        "SELECT count(*) FROM network_command WHERE kind = 'update_e911'"
    ).fetchone() == (0,)


def test_a_change_waiting_for_the_network_can_be_cancelled(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    order = change(api, wifi, product, OFFICE).json()
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "cancelled"
    assert stored(db_conn, product) == HOME_STORED
    assert db_conn.execute(
        "SELECT state FROM network_command WHERE kind = 'update_e911'"
    ).fetchone() == ("cancelled",)


def test_a_change_needs_the_pin_of_a_protected_customer(api, db_conn, wifi, stub, monkeypatch):
    monkeypatch.setattr(settings, "account_pin_required", True)
    product = activated(api, db_conn, wifi, HOME)
    assert set_pin(api, str(customer_of(db_conn, wifi.acc))).status_code == 200
    refused = change(api, wifi, product, OFFICE).json()
    assert refused["state"] == "rejected" and errors(refused) == ["pinRequired"]
    accepted = change(api, wifi, product, OFFICE, pin=PIN).json()
    assert accepted["state"] == "inProgress", errors(accepted)
    outcomes = [(e["outcome"], e["verification"]) for e in audit(api, action="e911_changed")]
    assert outcomes == [("done", "pin"), ("refused", "none")]


# --- validation ---


@pytest.mark.parametrize(
    ("bad", "path"),
    [
        ({"stateOrProvince": "ZZ"}, "place.0.place.stateOrProvince"),
        ({"postcode": "9410"}, "place.0.place.postcode"),
        ({"country": "CA"}, "place.0.place.country"),
        ({"streetName": " "}, "place.0.place.streetName"),
    ],
)
def test_an_invalid_address_is_a_422_with_its_path(api, db_conn, wifi, bad, path):
    response = add(api, wifi, address=HOME | bad)
    assert response.status_code == 422, response.text
    assert path in response.json()["message"]
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (0,)


def test_another_place_role_is_a_400(api, wifi):
    item = apidata.add_item(wifi.off)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "place": [{"@type": "RelatedPlaceRefOrValue", "role": "installation", "place": HOME}],
    }
    response = api.post(ORDER, apidata.order_body(wifi.acc, item))
    assert response.status_code == 400, response.text
    assert "e911Address" in response.json()["message"]


def test_a_change_is_an_item_of_its_own(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    response = change(api, wifi, product, OFFICE, status="suspended")
    assert response.status_code == 422, response.text
    assert "of its own" in response.json()["message"]


def test_an_address_on_an_offering_that_is_no_line_is_refused(api, db_conn, stub):
    off, acc = shop(api, db_conn, funds="100", numbers=False)
    item = apidata.add_item(off)
    item["product"] = {"@type": "ProductRefOrValue", "place": place(HOME)}
    order = api.post(ORDER, apidata.order_body(acc, item)).json()
    assert order["state"] == "rejected" and errors(order) == ["e911NotApplicable"]


def test_tmf637_place_of_a_line_changes_only_through_an_order(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    response = api.patch(f"{apidata.PRODUCT}/{product}", {"place": place(OFFICE)})
    assert response.status_code == 409, response.text
    assert stored(db_conn, product) == HOME_STORED


def test_tmf637_post_of_a_line_checks_the_place_as_an_order_does(api, wifi):
    body = {
        "@type": "Product",
        "billingAccount": {"id": wifi.acc["id"]},
        "productOffering": {"id": wifi.off["id"]},
    }
    bad = api.post(apidata.PRODUCT, body | {"place": place(HOME | {"postcode": "1"})})
    assert bad.status_code == 422, bad.text
    assert "place.0.place.postcode" in bad.json()["message"]
    other = [{"@type": "RelatedPlaceRefOrValue", "role": "installation", "place": HOME}]
    assert api.post(apidata.PRODUCT, body | {"place": other}).status_code == 400


def test_the_account_products_show_the_address_and_wifi_calling(api, db_conn, wifi, stub):
    product = activated(api, db_conn, wifi, HOME)
    rows = api.get(f"/internal/v1/billingAccount/{wifi.acc['id']}/products").json()
    (row,) = [r for r in rows if r["id"] == product]
    assert row["e911Address"] == HOME_STORED and row["wifiCalling"] is True


def test_an_unconfirmed_change_is_a_pending_change_of_the_line(api, db_conn, wifi, stub):
    """#384: the card's pending-change line shows the new address until the network confirms
    it, and Undo (cancelling the order) removes it."""
    product = activated(api, db_conn, wifi, HOME)
    url = f"/internal/v1/billingAccount/{wifi.acc['id']}/products"

    def pending():
        (row,) = [r for r in api.get(url).json() if r["id"] == product]
        return row["pendingChanges"]

    order = change(api, wifi, product, OFFICE).json()
    assert pending() == [
        {
            "kind": "e911",
            "date": api.clock.today().isoformat(),
            "e911Address": OFFICE_STORED,
            "productOrder": {"id": order["id"], "href": order["href"]},
        }
    ]
    tick(db_conn, api.clock.now())
    assert pending() == []

    other = change(api, wifi, product, HOME).json()
    assert [c["kind"] for c in pending()] == ["e911"]
    assert api.post(CANCEL, cancel_body(other)).status_code == 201
    assert pending() == []


def test_e911_address_orders_as_the_ui_sends_them(api, db_conn, wifi, stub):
    """The UI's order bodies (frontend/src/api/e911.ts, pinned in SubscriberCard.e911.test.tsx):
    Add product's typed address as product.place of the add item, with a sub-address, and Edit
    E911 address's modify item carrying only the place, without a PIN header when none is
    typed. Here (not tests/internal/test_ui_requests.py) for the network fixtures."""

    def ui_place(address):
        return [{"@type": "RelatedPlaceRefOrValue", "role": "e911Address", "place": address}]

    typed = HOME | {
        "city": "Springfield",
        "geographicSubAddress": [{"@type": "GeographicSubAddress", "name": "Apt 4"}],
    }
    item = {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "add",
        "productOffering": {"id": wifi.off["id"], "@type": "ProductOfferingRef"},
        "product": {
            "@type": "ProductRefOrValue",
            "realizingResource": refs(number(api, value="79991234500")["id"]),
            "place": ui_place(typed),
        },
    }
    added = api.post(ORDER, apidata.order_body(wifi.acc, item))
    assert added.status_code == 201 and added.json()["state"] == "inProgress", added.text
    tick(db_conn, api.clock.now())
    (pid,) = db_conn.execute("SELECT id FROM product ORDER BY created_at DESC LIMIT 1").fetchone()
    assert stored(db_conn, pid) == {k: v for k, v in typed.items() if k != "@type"}

    body = {
        "@type": "ProductOrder",
        "billingAccount": {"id": wifi.acc["id"], "@type": "BillingAccountRef"},
        "productOrderItem": [
            {
                "@type": "ProductOrderItem",
                "id": "1",
                "action": "modify",
                "product": {
                    "@type": "ProductRefOrValue",
                    "id": str(pid),
                    "place": ui_place(OFFICE),
                },
            }
        ],
    }
    changed = api.post(ORDER, body)
    assert changed.status_code == 201 and changed.json()["state"] == "inProgress", changed.text
    tick(db_conn, api.clock.now())
    assert api.get(changed.json()["href"]).json()["state"] == "completed"
    assert stored(db_conn, pid) == OFFICE_STORED


def test_the_address_rules():
    assert e911.address_problems(HOME, "p") == []
    assert e911.normalized(HOME) == HOME_STORED
    assert e911.role_problem(place(HOME)) is None
    assert e911.role_problem(place(HOME) * 2) is not None
    assert e911.wifi_calling({"wifiCalling": 1}) and not e911.wifi_calling({"wifiCalling": 0})
