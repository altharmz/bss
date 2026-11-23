"""Shared builders of tests/test_network_provisioning.py, used by other test modules (#423)."""

from types import SimpleNamespace

import pytest

from app.integrations.network import adapter
from tests import apidata
from tests.apidata import ORDER
from tests.builders.api_resource_inventory import number
from tests.builders.number_orders import refs
from tests.builders.prepaid_orders import shop
from tests.builders.prepaid_renewal_producer import tick

PROFILE = {
    "dataAllowanceMb": 5000,
    "dataCapMb": 5000,
    "throttleKbps": 128,
    "voiceMinutes": 300,
    "smsCount": 100,
    "hotspot": 1,
    "international": 0,
    "voicemail": 1,
}


def characteristics(**over):
    out = []
    for name, value in (PROFILE | over).items():
        kind = "integer"
        values = value if isinstance(value, list) else [value]
        out.append(
            {
                "@type": "CharacteristicSpecification",
                "name": name,
                "description": "",
                "configurable": False,
                "valueType": kind,
                "characteristicValueSpecification": [
                    {
                        "@type": f"{kind.capitalize()}CharacteristicValueSpecification",
                        "valueType": kind,
                        "isDefault": index == 0,
                        "value": v,
                    }
                    for index, v in enumerate(values)
                ],
            }
        )
    return out


def make_line(api, offering, **over):
    response = api.patch(
        offering["productSpecification"]["href"],
        {"productSpecCharacteristic": characteristics(**over)},
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    adapter().reset()
    monkeypatch.setattr(
        loop_module(), "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[])
    )
    yield adapter()
    adapter().reset()


def loop_module():
    from app.worker import loop

    return loop


@pytest.fixture
def world(api, db_conn):
    cheaper = apidata.sellable_offering(
        api, value=5, tax=[{"@type": "TaxItem", "taxCategory": "VAT", "taxRate": 20}]
    )
    response = api.patch(
        cheaper["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 200, response.text
    make_line(api, cheaper, dataAllowanceMb=1000, voiceMinutes=60)  # plan B, a smaller profile
    off, acc = shop(api, db_conn, funds="100", numbers=True)
    make_line(api, off)
    return SimpleNamespace(off=off, acc=acc, cheaper=cheaper)


def buy(api, world, value, item_id="1"):
    item = apidata.add_item(world.off, item_id)
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value=value)["id"]),
    }
    response = api.post(ORDER, apidata.order_body(world.acc, item))
    assert response.status_code == 201, response.text
    return response.json()


def item_uuid(conn, order):
    return conn.execute(
        "SELECT id FROM product_order_item WHERE product_order_id = %s", (order["id"],)
    ).fetchone()[0]


def state(api, order):
    return api.get(order["href"]).json()["state"]


def commands_of(conn, order):
    return conn.execute(
        "SELECT kind, state, attempts FROM network_command WHERE product_order_id = %s"
        " ORDER BY created_at",
        (order["id"],),
    ).fetchall()


def line(api, db_conn, world, value="79991234500"):
    """A funded, active line: the order is bought, confirmed and completed."""
    order = buy(api, world, value)
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    product = db_conn.execute(
        "SELECT p.id, f.id, upper(f.period) FROM product p JOIN funded_interval f"
        " ON f.product_id = p.id ORDER BY p.created_at DESC LIMIT 1"
    ).fetchone()
    return {"id": str(product[0]), "grant": product[1], "due": product[2]}


@pytest.fixture
def cheaper(world):
    return world.cheaper
