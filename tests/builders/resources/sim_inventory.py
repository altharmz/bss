"""Builders other test modules share, from tests/resources/test_sim_inventory.py (#423)."""

import pytest

from tests import apidata
from tests.apidata import ORDER
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.imsi import all_refs
from tests.builders.resources.network_provisioning import (
    state,
)
from tests.builders.resources.number_orders import refs

RESOURCE = "/tmf-api/resourceInventory/v5/resource"

BATCH = "/internal/v1/sim/batch"


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
