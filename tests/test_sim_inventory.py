"""SIM and eSIM inventory, assignment at activation and SIM swap (issue #186)."""
# ruff: noqa: F811

import importlib.util
from pathlib import Path

import psycopg
import pytest

from tests import apidata
from tests.apidata import ORDER
from tests.test_api_resource_inventory import number
from tests.test_imsi import all_refs
from tests.test_network_provisioning import (  # noqa: F401 - fixtures
    buy,
    commands_of,
    item_uuid,
    state,
    stub,
    world,
)
from tests.test_number_orders import refs
from tests.test_prepaid_renewal_producer import tick

RESOURCE = "/tmf-api/resourceInventory/v5/resource"
BATCH = "/internal/v1/sim/batch"
GENERATE = "/internal/v1/sim/esim-generate"


def sim(i, kind="physical"):
    item = {"iccid": f"89000000000000{i:05d}", "imsi": f"0010100000{i:05d}"}
    if kind == "esim":
        item |= {"smdpAddress": "smdp.example.com", "matchingId": f"MATCH{i:04d}"}
    return item


def stock(api, name="B1", kind="physical", count=3, start=1):
    body = {"batch": name, "simType": kind, "items": [sim(start + n, kind) for n in range(count)]}
    response = api.post(BATCH, body)
    assert response.status_code == 201, response.text
    return response.json()


def resources(api, **filters):
    query = "&".join(f"{k}={v}" for k, v in {"resourceSpecification.id": "imsi", **filters}.items())
    response = api.get(f"{RESOURCE}?{query}&limit=100")
    assert response.status_code == 200, response.text
    return response.json()


def by_imsi(api, imsi):
    (found,) = [r for r in resources(api, value=imsi)]
    return found


def characteristic(resource, name):
    return next(
        (c["value"] for c in resource.get("resourceCharacteristic", []) if c["name"] == name),
        None,
    )


@pytest.fixture
def line_world(api, world):  # noqa: F811 - the network world, its offering needing an IMSI too
    response = api.patch(
        world.off["productSpecification"]["href"],
        {
            "resourceSpecification": [
                {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                {"id": "imsi", "@type": "ResourceSpecificationRef"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    return world


def order_with(api, world, value, sim_type=None, imsi=None, item_id="1"):  # noqa: F811
    item = apidata.add_item(world.off, item_id)
    resource = refs(number(api, value=value)["id"])
    if imsi is not None:
        resource += refs(imsi)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": resource}
    if sim_type is not None:
        item["product"]["productCharacteristic"] = [
            {"@type": "StringCharacteristic", "name": "simType", "value": sim_type}
        ]
    return api.post(ORDER, apidata.order_body(world.acc, item))


def activate_line(api, db_conn, world, value, sim_type="physical"):  # noqa: F811
    response = order_with(api, world, value, sim_type)
    assert response.status_code == 201, response.text
    order = response.json()
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    return order


def sim_row(db_conn, imsi):
    return db_conn.execute(
        "SELECT sim_state, sim_product_id FROM subscriber_number"
        " WHERE resource_specification_id = 'imsi' AND value = %s",
        (imsi,),
    ).fetchone()


# --- stock: import, list, filter ---


def test_import_a_batch_then_list_and_filter_by_state_and_batch(api):
    summary = stock(api, "B1", "physical", 3)
    assert summary["total"] == 3 and summary["counts"] == {
        "in_stock": 3,
        "reserved": 0,
        "active": 0,
        "retired": 0,
    }
    stock(api, "E1", "esim", 2, start=10)
    assert len(resources(api)) == 5
    physical = resources(api, batchNumber="B1", simState="in_stock")
    assert len(physical) == 3
    first = next(r for r in physical if r["value"] == sim(1)["imsi"])
    assert first["@type"] == "PhysicalResource"
    assert first["serialNumber"] == sim(1)["iccid"] and first["batchNumber"] == "B1"
    assert characteristic(first, "simType") == "physical"
    assert characteristic(first, "simState") == "in_stock"
    assert characteristic(first, "iccid") == sim(1)["iccid"]
    assert len(resources(api, simType="esim")) == 2
    assert resources(api, batchNumber="B1", simState="active") == []
    listed = api.get(BATCH).json()
    assert [b["name"] for b in listed] == ["E1", "B1"] or {b["name"] for b in listed} == {
        "B1",
        "E1",
    }


def test_an_esim_profile_in_stock_hides_its_activation_code(api):
    stock(api, "E1", "esim", 1)
    (profile,) = resources(api, simType="esim")
    assert profile["@type"] == "LogicalResource"
    assert characteristic(profile, "activationCode") is None
    assert characteristic(profile, "matchingId") is None


def test_csv_import(api):
    csv = "iccid,imsi\n" + "\n".join(f"{sim(n)['iccid']},{sim(n)['imsi']}" for n in (1, 2)) + "\n"
    response = api.post_raw(f"{BATCH}?batch=C1&simType=physical", csv, content_type="text/csv")
    assert response.status_code == 201, response.text
    assert response.json()["total"] == 2
    esim = "iccid,imsi,smdpAddress,matchingId\n"
    esim += f"{sim(5)['iccid']},{sim(5)['imsi']},smdp.example.com,ABC123\n"
    response = api.post_raw(f"{BATCH}?batch=C2&simType=esim", esim, content_type="text/csv")
    assert response.status_code == 201, response.text
    assert len(resources(api, batchNumber="C2")) == 1


@pytest.mark.parametrize(
    "mutate,status,text",
    [
        (lambda b: b["items"][0].update(ki="00" * 16), 422, "Ki and OPc"),
        (lambda b: b["items"][0].update(OPc="00" * 16), 422, "Ki and OPc"),
        (lambda b: b["items"][0].update(iccid="123"), 422, "iccid"),
        (lambda b: b["items"][0].update(imsi="12"), 422, "imsi"),
        (lambda b: b["items"].append(dict(b["items"][0])), 422, "repeated"),
        (lambda b: b["items"][0].update(smdpAddress="x", matchingId="y"), 422, "for eSIM"),
        (lambda b: b.update(simType="usb"), 422, "simType"),
        (lambda b: b.update(batch="no spaces"), 422, "batch"),
        (lambda b: b.update(items=[]), 422, "items"),
        (lambda b: b.update(extra=1), 400, "extra"),
    ],
)
def test_import_refusals_store_nothing(api, mutate, status, text):
    body = {"batch": "X1", "simType": "physical", "items": [sim(1), sim(2)]}
    mutate(body)
    response = api.post(BATCH, body)
    assert response.status_code == status, response.text
    assert text in response.text
    assert resources(api) == [] and api.get(BATCH).json() == []


def test_esim_rows_need_both_activation_parts(api):
    body = {"batch": "X1", "simType": "esim", "items": [{**sim(1), "smdpAddress": "smdp.example"}]}
    assert api.post(BATCH, body).status_code == 422


def test_duplicates_with_the_inventory_and_a_reused_batch_name_are_refused(api):
    stock(api, "B1", "physical", 2)
    again = api.post(BATCH, {"batch": "B1", "simType": "physical", "items": [sim(9)]})
    assert again.status_code == 409 and "already exists" in again.text
    clash = api.post(BATCH, {"batch": "B2", "simType": "physical", "items": [sim(1), sim(7)]})
    assert clash.status_code == 409
    assert len(resources(api)) == 2  # nothing of B2 was stocked


def test_the_stub_network_generates_esim_profiles(api):
    response = api.post(GENERATE, {"batch": "G1", "count": 3})
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "generated" and response.json()["total"] == 3
    profiles = resources(api, batchNumber="G1", simType="esim")
    assert len(profiles) == 3 and all(p["resourceSpecification"]["id"] == "imsi" for p in profiles)
    assert api.post(GENERATE, {"batch": "G1", "count": 3}).status_code == 409
    assert api.post(GENERATE, {"batch": "G2", "count": 0}).status_code == 422


def test_no_secret_column_exists(db_conn):
    columns = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name IN ('subscriber_number', 'sim_batch')"
        )
    }
    assert not {c for c in columns if c.lower() in ("ki", "opc", "op", "k")}


# --- activation ---


@pytest.mark.capability("worker.simInventory")
@pytest.mark.parametrize("kind", ["physical", "esim"])
def test_activation_picks_a_sim_and_sends_its_iccid_and_imsi(api, db_conn, line_world, stub, kind):
    stock(api, "B1", kind, 2)
    response = order_with(api, line_world, "79991234500", kind)
    assert response.status_code == 201, response.text
    order = response.json()
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "reserved"  # oldest first, held for the line
    assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)
    assert stub.received == {}
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    ((_, (command, request)),) = stub.received.items()
    assert command == "activate"
    assert request["iccid"] == sim(1)["iccid"] and request["imsi"] == sim(1)["imsi"]
    assert request["simType"] == kind
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "active"
    picked = by_imsi(api, sim(1)["imsi"])
    assert characteristic(picked, "simState") == "active"
    if kind == "esim":
        assert characteristic(picked, "activationCode") == "LPA:1$smdp.example.com$MATCH0001"
    else:
        assert characteristic(picked, "activationCode") is None


def test_an_esim_activation_code_is_shown_once_the_profile_is_assigned(api, db_conn, line_world):
    stock(api, "E1", "esim", 1)
    assert characteristic(by_imsi(api, sim(1)["imsi"]), "activationCode") is None
    activate_line(api, db_conn, line_world, "79991234500", "esim")
    code = characteristic(by_imsi(api, sim(1)["imsi"]), "activationCode")
    assert code == "LPA:1$smdp.example.com$MATCH0001"


def test_activation_with_an_explicit_in_stock_sim_and_without_stock(api, db_conn, line_world):
    stock(api, "B1", "physical", 1)
    none = order_with(api, line_world, "79991234501", "esim")
    assert none.status_code == 201  # rejected orders are stored and carry the reason
    assert none.json()["state"] == "rejected"
    codes = [e["code"] for e in api.get(none.json()["href"]).json()["productOrderErrorMessage"]]
    assert codes == ["simUnavailable"]
    # neither a simType nor an IMSI keeps the old refusal
    bare = order_with(api, line_world, "79991234502")
    assert bare.json()["state"] == "rejected"
    codes = [e["code"] for e in api.get(bare.json()["href"]).json()["productOrderErrorMessage"]]
    assert codes == ["imsiRequired"]
    explicit = order_with(api, line_world, "79991234503", imsi=by_imsi(api, sim(1)["imsi"])["id"])
    assert explicit.json()["state"] == "inProgress"
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "reserved"


def test_a_sim_type_on_an_offering_without_imsi_and_a_bad_type_are_refused(api, world):  # noqa: F811
    stock(api, "B1", "physical", 1)
    item = apidata.add_item(world.off)  # the world's offering lists only the MSISDN
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api)["id"]),
        "productCharacteristic": [
            {"@type": "StringCharacteristic", "name": "simType", "value": "physical"}
        ],
    }
    response = api.post(ORDER, apidata.order_body(world.acc, item))
    assert response.json()["state"] == "rejected"
    item["product"]["productCharacteristic"][0]["value"] = "usb"
    assert api.post(ORDER, apidata.order_body(world.acc, item)).status_code == 422


def test_a_refused_activation_puts_the_sim_back_in_stock(api, db_conn, line_world, stub):
    stock(api, "B1", "physical", 1)
    response = order_with(api, line_world, "79991230000", "physical")  # stub refuses ...0000
    order = response.json()
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "reserved"
    tick(db_conn, api.clock.now())
    assert state(api, order) == "failed"
    assert sim_row(db_conn, sim(1)["imsi"]) == ("in_stock", None)


def test_a_cancelled_activation_releases_the_sim(api, db_conn, line_world):
    from tests.test_api_order_cancel import CANCEL, cancel_body

    stock(api, "B1", "physical", 1)
    order = order_with(api, line_world, "79991234500", "physical").json()
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "reserved"
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert sim_row(db_conn, sim(1)["imsi"]) == ("in_stock", None)


def test_a_sim_cannot_be_active_on_two_lines(api, db_conn, line_world):
    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    taken = by_imsi(api, sim(1)["imsi"])["id"]
    second = order_with(api, line_world, "79991234501", imsi=taken)
    assert second.json()["state"] == "rejected"
    codes = [e["code"] for e in api.get(second.json()["href"]).json()["productOrderErrorMessage"]]
    assert codes == ["imsiUnavailable"]
    # and the database refuses it even if an order slipped through
    product = db_conn.execute(
        "SELECT sim_product_id FROM subscriber_number WHERE value = %s", (sim(1)["imsi"],)
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        db_conn.execute(
            "UPDATE subscriber_number SET sim_state = 'active', sim_product_id = %s"
            " WHERE value = %s",
            (product, sim(2)["imsi"]),
        )
    db_conn.rollback()


# --- SIM swap ---


def msisdn_of(db_conn, product_id):
    """The product's live number: a modify names the complete resulting set."""
    return str(
        db_conn.execute(
            "SELECT subscriber_number_id FROM number_assignment WHERE product_id = %s"
            " AND resource_specification_id = 'msisdn' AND cancelled_at IS NULL",
            (product_id,),
        ).fetchone()[0]
    )


def swap_order(api, db_conn, world, product_id, new_sim_id, day=None):  # noqa: F811
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product_id,
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(msisdn_of(db_conn, product_id), new_sim_id),
        },
    }
    return api.post(ORDER, apidata.order_body(world.acc, item))


def live_product(db_conn):
    return str(db_conn.execute("SELECT id FROM product ORDER BY created_at DESC").fetchone()[0])


def claims(db_conn, product_id):
    return db_conn.execute(
        "SELECT n.value FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE a.product_id = %s AND a.cancelled_at IS NULL"
        " AND a.resource_specification_id = 'imsi' AND upper_inf(a.valid_for)",
        (product_id,),
    ).fetchall()


@pytest.mark.capability("worker.simInventory")
def test_swap_sends_swap_sim_and_on_confirmation_retires_the_old_sim(
    api, db_conn, line_world, stub
):
    stock(api, "B1", "physical", 1)
    stock(api, "E1", "esim", 1, start=20)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    new = by_imsi(api, sim(20)["imsi"])
    response = swap_order(api, db_conn, line_world, product, new["id"])
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["state"] == "inProgress"
    # until the network answers nothing has moved
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "active"
    assert sim_row(db_conn, sim(20)["imsi"])[0] == "reserved"
    assert claims(db_conn, product) == [(sim(1)["imsi"],)]
    assert commands_of(db_conn, order) == [("swap_sim", "pending", 0)]
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    kind, request = list(stub.received.values())[-1]
    assert kind == "swap_sim"
    assert request["imsi"] == sim(1)["imsi"] and request["newImsi"] == sim(20)["imsi"]
    assert request["iccid"] == sim(1)["iccid"] and request["newIccid"] == sim(20)["iccid"]
    assert request["newSimType"] == "esim" and request["msisdn"] == "79991234500"
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "retired"
    assert sim_row(db_conn, sim(20)["imsi"])[0] == "active"
    assert claims(db_conn, product) == [(sim(20)["imsi"],)]
    assert characteristic(by_imsi(api, sim(20)["imsi"]), "activationCode") is not None
    # the retired SIM is not picked again
    assert len(resources(api, simState="retired")) == 1


@pytest.mark.capability("worker.simInventory")
def test_a_refused_swap_changes_nothing(api, db_conn, line_world, stub):
    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    new = by_imsi(api, sim(2)["imsi"])
    stub.refuse_kinds.add("swap_sim")
    order = swap_order(api, db_conn, line_world, product, new["id"]).json()
    tick(db_conn, api.clock.now())
    assert state(api, order) == "failed"
    errors = api.get(order["href"]).json()["productOrderErrorMessage"]
    assert [e["code"] for e in errors] == ["networkRefused"]
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "active"
    assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)
    assert claims(db_conn, product) == [(sim(1)["imsi"],)]
    assert db_conn.execute("SELECT status FROM product WHERE id = %s", (product,)).fetchone() == (
        "active",
    )


def test_cancelling_an_unconfirmed_swap_releases_the_new_sim(api, db_conn, line_world):
    from tests.test_api_order_cancel import CANCEL, cancel_body

    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    order = swap_order(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"]).json()
    response = api.post(CANCEL, cancel_body(order))
    assert response.status_code == 201, response.text
    assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)
    assert sim_row(db_conn, sim(1)["imsi"])[0] == "active"
    tick(db_conn, api.clock.now())
    assert db_conn.execute(
        "SELECT state FROM network_command WHERE product_order_id = %s", (order["id"],)
    ).fetchone() == ("cancelled",)


def test_a_second_swap_waits_for_the_first_and_a_taken_sim_is_refused(api, db_conn, line_world):
    stock(api, "B1", "physical", 3)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    first = swap_order(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"])
    assert first.json()["state"] == "inProgress"
    again = swap_order(api, db_conn, line_world, product, by_imsi(api, sim(3)["imsi"])["id"])
    assert again.json()["state"] == "rejected"
    taken = swap_order(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"])
    assert taken.json()["state"] == "rejected"


def test_a_swap_changes_nothing_else_in_its_order(api, db_conn, line_world):
    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    new = by_imsi(api, sim(2)["imsi"])["id"]
    other = number(api, value="79991234599")["id"]
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product,
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(new, other),  # the number changes as well
        },
    }
    order = api.post(ORDER, apidata.order_body(line_world.acc, item)).json()
    assert order["state"] == "rejected"
    codes = [e["code"] for e in api.get(order["href"]).json()["productOrderErrorMessage"]]
    assert codes == ["simSwapAlone"]
    assert sim_row(db_conn, sim(2)["imsi"]) == ("in_stock", None)


# --- legacy IMSI resources and the migration ---


def test_a_legacy_imsi_resource_keeps_its_body_and_follows_the_states(api, db_conn, line_world):
    legacy = api.post(
        RESOURCE,
        {
            "@type": "LogicalResource",
            "name": "SIM",
            "value": "001010123456789",
            "resourceSpecification": {"id": "imsi", "@type": "ResourceSpecificationRef"},
        },
    ).json()
    assert legacy["@type"] == "LogicalResource" and "resourceCharacteristic" not in legacy
    assert resources(api, simState="in_stock", value="001010123456789")
    order = order_with(api, line_world, "79991234500", imsi=legacy["id"]).json()
    assert sim_row(db_conn, "001010123456789")[0] == "reserved"
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    assert sim_row(db_conn, "001010123456789")[0] == "active"


def test_unlocking_a_retired_sim_stocks_it_again(api, db_conn, line_world):
    stock(api, "B1", "physical", 2)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    swap_order(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"])
    tick(db_conn, api.clock.now())
    old = by_imsi(api, sim(1)["imsi"])
    assert characteristic(old, "simState") == "retired" and old["administrativeState"] == "locked"
    echoed = api.patch(old["href"], {"administrativeState": "unlocked"})
    assert echoed.status_code == 200, echoed.text
    assert characteristic(echoed.json(), "simState") == "in_stock"
    # a derived characteristic is never edited
    bad = api.patch(
        old["href"],
        {
            "resourceCharacteristic": [
                {"name": "simState", "valueType": "string", "value": "active"}
            ]
        },
    )
    assert bad.status_code == 422


def test_the_migration_backfills_the_state_of_existing_imsi_rows(api, db_conn, line_world):
    spec = importlib.util.spec_from_file_location(
        "migration_0055", Path("alembic/versions/0055_sim_inventory.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stock(api, "B1", "physical", 3)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    swap_order(api, db_conn, line_world, product, by_imsi(api, sim(2)["imsi"])["id"])
    tick(db_conn, api.clock.now())  # sim 1 retired, sim 2 active, sim 3 in stock
    expected = {n: sim_row(db_conn, sim(n)["imsi"])[0] for n in (1, 2, 3)}
    assert expected == {1: "retired", 2: "active", 3: "in_stock"}
    db_conn.execute(
        "UPDATE subscriber_number SET sim_state = 'in_stock', sim_product_id = NULL"
        " WHERE resource_specification_id = 'imsi'"
    )
    db_conn.execute(module.BACKFILL)
    assert {n: sim_row(db_conn, sim(n)["imsi"])[0] for n in (1, 2, 3)} == expected


def test_the_migration_downgrades_and_upgrades_again(temp_db_url):
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0054")
    command.upgrade(cfg, "0055")
    with psycopg.connect(temp_db_url) as conn:
        assert conn.execute("SELECT count(*) FROM sim_batch").fetchone() == (0,)
