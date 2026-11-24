"""Builders other test modules share, from tests/resources/test_number_porting.py (#423)."""

from tests import apidata
from tests.apidata import ORDER
from tests.builders.resources.number_orders import refs

PORT = "/internal/v1/port"

NUMBER = "12125550142"

BODY = {
    "number": NUMBER,
    "carrierAccountNumber": "ACC-998877",
    "transferPin": "123456",
    "billingZip": "10001",
    "firstName": "Ada",
    "lastName": "Lovelace",
}


def port_in(api, **over):
    response = api.post(f"{PORT}/in", BODY | over)
    assert response.status_code == 201, response.text
    return response.json()


def buy_number(api, world, resource_id):
    item = apidata.add_item(world.off, "1")
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(resource_id)}
    response = api.post(ORDER, apidata.order_body(world.acc, item))
    assert response.status_code == 201, response.text
    return response.json()
