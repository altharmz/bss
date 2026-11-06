"""Device IMEI capture and the network compatibility check (issue #194)."""
# ruff: noqa: F811

import psycopg
import pytest

from app.integrations.network import adapter
from app.network import device
from tests import apidata
from tests.apidata import ORDER
from tests.test_api_resource_inventory import number
from tests.test_network_provisioning import line, stub, world  # noqa: F401 - fixtures
from tests.test_number_orders import refs
from tests.test_prepaid_renewal_producer import tick
from tests.test_prepaid_status_orders import errors

DEVICE = "/internal/v1/device"
CHECK = "/internal/v1/device/check"


def make_imei(tac: str, serial: str = "123456") -> str:
    """tac (8 digits) + serial (6 digits) + the Luhn check digit."""
    body = tac + serial
    for digit in range(10):
        if device.valid(body + str(digit)):
            return body + str(digit)
    raise AssertionError


COMPATIBLE = make_imei("35693803")
OTHER_COMPATIBLE = make_imei("35111111", "654321")
INCOMPATIBLE = make_imei("99000001")
UNKNOWN = make_imei("86000000")
OFFLINE = make_imei("98000001")


def imei_item(world, api, value, imei=None, item_id="1", extra=()):
    item = apidata.add_item(world.off, item_id)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value=value)["id"]),
    }
    if imei is not None:
        item["product"]["productCharacteristic"] = [
            {"@type": "StringCharacteristic", "name": "imei", "value": imei},
            *extra,
        ]
    return item


def place(api, world, value, imei):
    return api.post(ORDER, apidata.order_body(world.acc, imei_item(world, api, value, imei)))


def device_item(product, imei, item_id="1"):
    return {
        "@type": "ProductOrderItem",
        "id": item_id,
        "action": "modify",
        "product": {
            "id": product["id"],
            "@type": "ProductRef",
            "productCharacteristic": [
                {"@type": "StringCharacteristic", "name": "imei", "value": imei}
            ],
        },
    }


def characteristic(product, name):
    return next(
        (c["value"] for c in product.get("productCharacteristic", []) if c["name"] == name), None
    )


def shown(api, product_id):
    return api.get(f"{apidata.PRODUCT}/{product_id}").json()


def activated(api, db_conn, world, value, imei):
    response = place(api, world, value, imei)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "inProgress", errors(order)
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "completed"
    (pid,) = db_conn.execute("SELECT id FROM product ORDER BY created_at DESC LIMIT 1").fetchone()
    return order, str(pid)


# --- the number itself ---


@pytest.mark.parametrize(
    "bad",
    [
        COMPATIBLE[:-1],  # 14 digits
        COMPATIBLE + "0",  # 16 digits
        "35693803564380X",  # not digits
        COMPATIBLE[:-1] + str((int(COMPATIBLE[-1]) + 1) % 10),  # wrong check digit
        "",
    ],
)
def test_an_invalid_imei_is_a_422_and_stores_nothing(api, db_conn, world, bad):
    response = place(api, world, "79991234500", bad)
    assert response.status_code == 422, response.text
    assert "imei" in response.text
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM product_device").fetchone() == (0,)


def test_the_luhn_rule_on_known_numbers():
    assert device.valid("490154203237518") and device.valid("356938035643809")
    assert not device.valid("490154203237519") and not device.valid("49015420323751")


def test_imei_alongside_other_characteristics_and_a_device_change_alone(api, db_conn, world):
    extra = [{"@type": "StringCharacteristic", "name": "other", "value": "x"}]
    response = api.post(
        ORDER,
        apidata.order_body(
            world.acc, imei_item(world, api, "79991234500", COMPATIBLE, extra=extra)
        ),
    )
    assert response.status_code == 422  # only simType and imei
    twice = imei_item(world, api, "79991234501", COMPATIBLE)
    twice["product"]["productCharacteristic"].append(
        {"@type": "StringCharacteristic", "name": "imei", "value": COMPATIBLE}
    )
    assert api.post(ORDER, apidata.order_body(world.acc, twice)).status_code == 422


# --- activation ---


def test_activation_records_the_device_and_the_product_shows_it(api, db_conn, world, stub):
    order, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    product = shown(api, pid)
    assert characteristic(product, "imei") == COMPATIBLE
    assert characteristic(product, "deviceCompatibility") == "compatible"
    assert characteristic(product, "deviceReasons") is None
    row = db_conn.execute(
        "SELECT imei, compatibility, reasons, ended_at FROM product_device"
    ).fetchall()
    assert row == [(COMPATIBLE, "compatible", [], None)]
    (history,) = api.get(f"{DEVICE}?product.id={pid}").json()
    assert history["imei"] == COMPATIBLE and history["endedAt"] is None
    # the order item echoes what was sent and the verdict
    item = api.get(order["href"]).json()["productOrderItem"][0]
    chars = {c["name"]: c["value"] for c in item["product"]["productCharacteristic"]}
    assert chars["imei"] == COMPATIBLE and chars["deviceCompatibility"] == "compatible"


def test_an_incompatible_device_refuses_the_activation_with_the_reasons(api, db_conn, world, stub):
    response = place(api, world, "79991234500", INCOMPATIBLE)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "rejected" and errors(order) == ["deviceIncompatible"]
    reason = order["productOrderErrorMessage"][0]["reason"]
    assert "band 71" in reason and "locked" in reason and "VoLTE" in reason
    assert db_conn.execute("SELECT count(*) FROM product").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM product_device").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM network_command").fetchone() == (0,)
    assert stub.received == {}  # nothing was sent for the line


@pytest.mark.parametrize("imei", [UNKNOWN, OFFLINE])
def test_an_unknown_device_is_allowed_with_a_warning(api, db_conn, world, stub, imei):
    order, pid = activated(api, db_conn, world, "79991234500", imei)
    product = shown(api, pid)
    assert characteristic(product, "imei") == imei
    assert characteristic(product, "deviceCompatibility") == "unknown"
    assert characteristic(product, "deviceReasons")  # why it is unknown
    item = api.get(order["href"]).json()["productOrderItem"][0]
    chars = {c["name"]: c["value"] for c in item["product"]["productCharacteristic"]}
    assert chars["deviceCompatibility"] == "unknown" and chars["deviceReasons"]
    (history,) = api.get(f"{DEVICE}?product.id={pid}").json()
    assert history["compatibility"] == "unknown" and history["reasons"]


def test_activation_without_an_imei_still_works(api, db_conn, world, stub):
    response = api.post(
        ORDER, apidata.order_body(world.acc, imei_item(world, api, "79991234500", None))
    )
    assert response.status_code == 201 and response.json()["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    (pid,) = db_conn.execute("SELECT id FROM product").fetchone()
    assert characteristic(shown(api, str(pid)), "imei") is None
    assert api.get(f"{DEVICE}?product.id={pid}").json() == []


def test_a_device_on_a_product_without_a_network_profile_is_refused(api, db_conn, stub):
    from tests.test_prepaid_orders import shop

    off, acc = shop(api, db_conn, numbers=False)  # no network profile: not a line
    item = apidata.add_item(off)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "productCharacteristic": [
            {"@type": "StringCharacteristic", "name": "imei", "value": COMPATIBLE}
        ],
    }
    order = api.post(ORDER, apidata.order_body(acc, item)).json()
    assert order["state"] == "rejected" and errors(order) == ["deviceNotApplicable"]


# --- a device change keeps the history ---


def test_a_device_change_closes_the_old_row_and_keeps_it(api, db_conn, world, stub):
    _, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    order = apidata.order(api, world.acc, device_item({"id": pid}, OTHER_COMPATIBLE))
    assert order["state"] == "completed", errors(order)
    product = shown(api, pid)
    assert characteristic(product, "imei") == OTHER_COMPATIBLE
    assert product["status"] == "active"
    history = api.get(f"{DEVICE}?product.id={pid}").json()
    assert [h["imei"] for h in history] == [OTHER_COMPATIBLE, COMPATIBLE]  # newest first
    assert history[0]["endedAt"] is None and history[1]["endedAt"] is not None
    assert db_conn.execute("SELECT count(*) FROM network_command").fetchone() == (1,)  # activate


def test_an_incompatible_replacement_is_refused_and_the_device_stays(api, db_conn, world, stub):
    _, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    order = apidata.order(api, world.acc, device_item({"id": pid}, INCOMPATIBLE))
    assert order["state"] == "rejected" and errors(order) == ["deviceIncompatible"]
    assert characteristic(shown(api, pid), "imei") == COMPATIBLE
    assert len(api.get(f"{DEVICE}?product.id={pid}").json()) == 1


def test_a_device_can_be_added_to_a_line_that_had_none(api, db_conn, world, stub):
    response = api.post(
        ORDER, apidata.order_body(world.acc, imei_item(world, api, "79991234500", None))
    )
    tick(db_conn, api.clock.now())
    (pid,) = db_conn.execute("SELECT id FROM product").fetchone()
    order = apidata.order(api, world.acc, device_item({"id": str(pid)}, UNKNOWN))
    assert order["state"] == "completed", errors(order)
    assert characteristic(shown(api, str(pid)), "deviceCompatibility") == "unknown"
    assert response.status_code == 201


def test_a_device_change_is_an_item_of_its_own(api, db_conn, world, stub):
    _, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    item = device_item({"id": pid}, OTHER_COMPATIBLE)
    item["product"]["status"] = "suspended"
    assert api.post(ORDER, apidata.order_body(world.acc, item)).status_code == 422
    item = device_item({"id": pid}, OTHER_COMPATIBLE)
    item["productOffering"] = {"id": world.off["id"], "@type": "ProductOfferingRef"}
    assert api.post(ORDER, apidata.order_body(world.acc, item)).status_code == 422
    # a delete item cannot carry one
    gone = {
        "@type": "ProductOrderItem",
        "id": "1",
        "action": "delete",
        "product": device_item({"id": pid}, COMPATIBLE)["product"],
    }
    assert api.post(ORDER, apidata.order_body(world.acc, gone)).status_code == 422
    assert len(api.get(f"{DEVICE}?product.id={pid}").json()) == 1


def test_a_device_change_on_an_ended_product_is_refused(api, db_conn, world, stub):
    from tests.test_prepaid_terminations import terminate

    _, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    terminate(api, world.acc, {"id": pid})
    tick(db_conn, api.clock.now())
    order = apidata.order(api, world.acc, device_item({"id": pid}, OTHER_COMPATIBLE))
    assert order["state"] == "rejected" and errors(order) == ["productAlreadyEnded"]


def test_a_draft_order_keeps_the_imei_until_it_is_submitted(api, db_conn, world, stub):
    item = imei_item(world, api, "79991234500", COMPATIBLE)
    draft = api.post(
        ORDER,
        {
            "@type": "ProductOrder",
            "requestedInitialState": "draft",
            "billingAccount": {"id": world.acc["id"]},
            "productOrderItem": [item],
        },
    ).json()
    assert draft["state"] == "draft"
    submitted = api.patch(draft["href"], {"state": "acknowledged"})
    assert submitted.status_code == 200, submitted.text
    tick(db_conn, api.clock.now())
    (pid,) = db_conn.execute("SELECT id FROM product").fetchone()
    assert characteristic(shown(api, str(pid)), "imei") == COMPATIBLE


# --- the check ---


def test_check_device_answers_without_storing(api, db_conn, stub):
    ok = api.get(f"{CHECK}?imei={COMPATIBLE}")
    assert ok.status_code == 200 and ok.json() == {
        "imei": COMPATIBLE,
        "result": "compatible",
        "reasons": [],
    }
    bad = api.get(f"{CHECK}?imei={INCOMPATIBLE}").json()
    assert bad["result"] == "incompatible" and len(bad["reasons"]) == 3
    unknown = api.get(f"{CHECK}?imei={UNKNOWN}").json()
    assert unknown["result"] == "unknown" and unknown["reasons"]
    offline = api.get(f"{CHECK}?imei={OFFLINE}").json()
    assert offline["result"] == "unknown" and "could not" in offline["reasons"][0]
    assert db_conn.execute("SELECT count(*) FROM product_device").fetchone() == (0,)


def test_check_device_rejects_a_bad_imei_and_unknown_parameters(api):
    assert api.get(f"{CHECK}?imei=123").status_code == 422
    assert (
        api.get(f"{CHECK}?imei={COMPATIBLE[:-1]}{(int(COMPATIBLE[-1]) + 1) % 10}").status_code
        == 422
    )
    assert api.get(CHECK).status_code == 400
    assert api.get(f"{CHECK}?imei={COMPATIBLE}&x=1").status_code == 400


def test_device_history_needs_an_existing_product(api):
    assert api.get(DEVICE).status_code == 400
    assert api.get(f"{DEVICE}?product.id=not-a-uuid").status_code == 400
    assert api.get(f"{DEVICE}?product.id=00000000-0000-0000-0000-000000000000").status_code == 404
    assert api.get(f"{DEVICE}?other=1").status_code == 400


# --- the table ---


def test_one_current_device_per_product_and_a_closed_row_needs_its_item(api, db_conn, world, stub):
    _, pid = activated(api, db_conn, world, "79991234500", COMPATIBLE)
    item_id = db_conn.execute("SELECT recorded_by_item_id FROM product_device").fetchone()[0]
    db_conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db_conn.execute(
            "INSERT INTO product_device (product_id, imei, compatibility, checked_at,"
            " started_at, recorded_by_item_id) VALUES (%s, %s, 'compatible', now(), now(), %s)",
            (pid, OTHER_COMPATIBLE, item_id),
        )
    db_conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute("UPDATE product_device SET ended_at = now()")  # no ended_by_item_id
    db_conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute("UPDATE product_device SET imei = '123'")
    db_conn.rollback()


def test_the_stub_is_deterministic_by_tac():
    network = adapter()
    assert network.check_device(COMPATIBLE).result == "compatible"
    assert network.check_device(INCOMPATIBLE).result == "incompatible"
    assert network.check_device(UNKNOWN).result == "unknown"
