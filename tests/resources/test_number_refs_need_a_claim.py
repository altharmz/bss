"""A subscriber number or IMSI is only ever attached by a product order that claims it (#477):
an order or PATCH that would leave a bare reference on a product taking no numbers is refused,
so the number never looks free while a product names it."""

import json
from datetime import timedelta
from uuid import uuid4

from tests import apidata, timeline
from tests.apidata import ORDER
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs


def plain_shop(api):
    """An offering whose specification lists no msisdn/imsi, and an account."""
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    return offering, account


def claims(db_conn, number_id):
    db_conn.rollback()
    return db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE subscriber_number_id = %s", (number_id,)
    ).fetchone()[0]


def test_an_add_order_with_a_number_for_an_offering_without_numbers_is_rejected(api, db_conn):
    offering, account = plain_shop(api)
    uid = number(api)["id"]
    item = apidata.add_item(offering)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(uid)}
    order = api.post(ORDER, apidata.order_body(account, item)).json()
    assert order["state"] == "rejected"
    [error] = order["productOrderErrorMessage"]
    assert error["code"] == "numberNotRequired"
    assert "add the resourceSpecification to the product specification" in error["reason"]
    assert claims(db_conn, uid) == 0


def test_a_modify_order_attaching_a_number_names_the_specification_fix(api):
    offering, account = plain_shop(api)
    product = apidata.product_of(
        api, api.post(ORDER, apidata.order_body(account, apidata.add_item(offering))).json()
    )
    uid = number(api)["id"]
    order = apidata.order(
        api,
        account,
        {
            "id": "1",
            "action": "modify",
            "product": {"id": product["id"], "@type": "ProductRef", "realizingResource": refs(uid)},
        },
    )
    assert order["state"] == "rejected"
    [error] = order["productOrderErrorMessage"]
    assert error["code"] == "numberNotRequired"
    assert "product inventory" not in error["reason"]  # never steer to the PATCH
    assert "order the product with the number" in error["reason"]


def test_a_patch_cannot_attach_a_number_but_keeps_other_resource_documents(api, db_conn):
    offering, account = plain_shop(api)
    product = apidata.product_of(
        api, api.post(ORDER, apidata.order_body(account, apidata.add_item(offering))).json()
    )
    uid = number(api)["id"]
    refused = api.patch(product["href"], {"realizingResource": refs(uid)})
    assert refused.status_code == 409, refused.text
    assert "numbers are only ever attached by a product order" in refused.text
    assert "realizingResource" not in api.get(product["href"]).json()
    assert claims(db_conn, uid) == 0
    other = str(uuid4())  # not a number: an ordinary resource document
    kept = api.patch(product["href"], {"realizingResource": refs(other)})
    assert kept.status_code == 200, kept.text
    assert kept.json()["realizingResource"][0]["id"] == other


def test_the_cleanup_command_finds_and_removes_only_unbacked_number_references(api, db_conn):
    from psycopg.types.json import Jsonb

    from app import number_refs

    offering, account = plain_shop(api)
    product = apidata.product_of(
        api, api.post(ORDER, apidata.order_body(account, apidata.add_item(offering))).json()
    )
    uid, other = number(api)["id"], str(uuid4())
    # what an order or PATCH could store before #477: a number with no claim, beside a document
    db_conn.execute(
        "UPDATE product SET details = details || %s WHERE id = %s",
        (Jsonb({"realizingResource": refs(uid) + refs(other)}), product["id"]),
    )
    db_conn.commit()
    found = number_refs.unbacked(db_conn)
    assert [(str(r["product_id"]), r["number_id"]) for r in found] == [(product["id"], uid)]
    assert number_refs.remove(db_conn, found, api.clock.now(), api.clock.today()) == 1
    db_conn.commit()
    assert number_refs.unbacked(db_conn) == []
    assert [r["id"] for r in api.get(product["href"]).json()["realizingResource"]] == [other]


def test_the_cleanup_command_leaves_claimed_numbers_alone(api, db_conn):
    from app import number_refs

    offering = apidata.sellable_offering(api)
    api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    item = apidata.add_item(offering)
    item["product"] = {"@type": "ProductRefOrValue", "realizingResource": refs(number(api)["id"])}
    assert api.post(ORDER, apidata.order_body(account, item)).json()["state"] == "completed"
    assert number_refs.unbacked(db_conn) == []


def unbacked_product(api, db_conn):
    """A product whose only realizingResource names a number it holds no claim on."""
    from psycopg.types.json import Jsonb

    offering, account = plain_shop(api)
    product = apidata.product_of(
        api, api.post(ORDER, apidata.order_body(account, apidata.add_item(offering))).json()
    )
    uid = number(api)["id"]
    db_conn.execute(
        "UPDATE product SET details = details || %s WHERE id = %s",
        (Jsonb({"realizingResource": refs(uid)}), product["id"]),
    )
    db_conn.commit()
    return product, uid


def run_cli(monkeypatch, *argv):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["app.number_refs", *argv])
    monkeypatch.delitem(sys.modules, "app.number_refs", raising=False)  # no runpy warning
    runpy.run_module("app.number_refs", run_name="__main__", alter_sys=False)


def test_the_cleanup_command_reports_then_applies(api, db_conn, monkeypatch, capsys):
    product, uid = unbacked_product(api, db_conn)
    run_cli(monkeypatch)
    report = capsys.readouterr().out.splitlines()
    assert json.loads(report[0])["product_id"] == product["id"]
    assert json.loads(report[0])["number_id"] == uid
    assert report[1:] == ["1 unbacked reference(s); run with --apply to remove them"]
    assert api.get(product["href"]).json()["realizingResource"][0]["id"] == uid

    run_cli(monkeypatch, "--apply")
    assert capsys.readouterr().out.splitlines()[-1] == "removed 1 reference(s) from 1 product(s)"
    assert "realizingResource" not in api.get(product["href"]).json()

    run_cli(monkeypatch, "--apply")
    assert capsys.readouterr().out == "no unbacked number references\n"
