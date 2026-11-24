"""Builders other test modules share, from tests/prepaid/test_usage.py (#423)."""

from tests import apidata
from tests.apidata import ORDER
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.network_provisioning import state
from tests.builders.resources.number_orders import refs

SUMMARY = "/internal/v1/usage/summary"

MSISDN = "79991234500"


def line_on(api, db_conn, offering, acc, value=MSISDN):
    item = apidata.add_item(offering, "1")
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": refs(number(api, value=value)["id"]),
    }
    response = api.post(ORDER, apidata.order_body(acc, item))
    assert response.status_code == 201, response.text
    tick(db_conn, api.clock.now())
    assert state(api, response.json()) == "completed"
    product = db_conn.execute(
        "SELECT a.product_id FROM number_assignment a JOIN subscriber_number n"
        " ON n.id = a.subscriber_number_id WHERE n.value = %s",
        (value,),
    ).fetchone()
    return {"id": str(product[0])}


def summary(api, product, **query):
    url = f"{SUMMARY}?product.id={product['id']}" + "".join(f"&{k}={v}" for k, v in query.items())
    response = api.get(url)
    assert response.status_code == 200, response.text
    return response.json()
