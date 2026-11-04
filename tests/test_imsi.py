"""IMSI as a second TMF639 logical resource, assigned and changed by TMF622 orders."""

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from psycopg import errors

from tests import apidata, timeline
from tests.apidata import ORDER, PRODUCT
from tests.factories import NOW, product, rejected, world
from tests.test_api_resource_inventory import URL, number
from tests.test_events import stored, subscribe
from tests.test_number_orders import cancel
from tests.test_prepaid_orders import shop as prepaid_shop
from tests.test_prepaid_renewal_producer import tick
from tests.test_prepaid_status_orders import errors as order_errors

IMSI = "001010123456789"
ICCID = "8900000000000000001"


def imsi(api, value=IMSI, iccid=None, **extra):
    body = {
        "@type": "LogicalResource",
        "name": "SIM",
        "value": value,
        "resourceSpecification": {"id": "imsi", "@type": "ResourceSpecificationRef"},
        **extra,
    }
    if iccid:
        body["resourceCharacteristic"] = [
            {
                "@type": "StringCharacteristic",
                "name": "iccid",
                "valueType": "string",
                "value": iccid,
            }
        ]
    return api.post(URL, body)


def made(api, value=IMSI, iccid=None):
    response = imsi(api, value, iccid)
    assert response.status_code == 201, response.text
    return response.json()


def all_refs(*uids):
    return [{"id": u, "@type": "ResourceRef", "@referredType": "LogicalResource"} for u in uids]


@pytest.fixture
def shop(api):
    offering = apidata.sellable_offering(api)
    response = api.patch(
        offering["productSpecification"]["href"],
        {
            "resourceSpecification": [
                {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                {"id": "imsi", "@type": "ResourceSpecificationRef"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    return offering, account


def order(api, account, item, day=None):
    body = apidata.order_body(account, item)
    if day is not None:
        body["requestedStartDate"] = timeline.iso(timeline.midnight(day))
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def add(api, shop, *uids, day=None):
    offering, account = shop
    item = apidata.add_item(offering)
    if uids:
        item["product"] = {"@type": "ProductRefOrValue", "realizingResource": all_refs(*uids)}
    return order(api, account, item, day)


def modify(api, account, product, *uids, day=None):
    item = {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product["id"],
            "@type": "ProductRefOrValue",
            "realizingResource": all_refs(*uids),
        },
    }
    return order(api, account, item, day)


# --- TMF639 ---


def test_imsi_resource_crud_filter_and_iccid(api):
    msisdn = number(api)
    sim = made(api, iccid=ICCID)
    assert sim["resourceSpecification"] == {"id": "imsi", "@type": "ResourceSpecificationRef"}
    assert sim["resourceCharacteristic"] == [
        {"@type": "StringCharacteristic", "name": "iccid", "valueType": "string", "value": ICCID}
    ]
    assert sim["administrativeState"] == "unlocked"
    assert sim["allocationStatus"] == "available"
    assert api.get(sim["href"]).json() == sim
    kinds = {
        kind: [r["id"] for r in api.get(f"{URL}?resourceSpecification.id={kind}").json()]
        for kind in ("msisdn", "imsi")
    }
    assert kinds == {"msisdn": [msisdn["id"]], "imsi": [sim["id"]]}
    assert api.get(f"{URL}?value={IMSI}").json()[0]["id"] == sim["id"]
    assert api.patch(sim["href"], {"administrativeState": "locked"}).status_code == 200
    assert api.delete(sim["href"]).status_code == 204


def test_an_imsi_and_an_msisdn_may_share_digits(api):
    digits = "123456789012345"
    assert number(api, digits)["value"] == digits
    assert made(api, digits)["value"] == digits
    assert imsi(api, digits).status_code == 409


@pytest.mark.parametrize("value", ["1234567890123", "1234567890123456", "12345678901234x", ""])
def test_imsi_value_must_be_14_or_15_digits(api, value):
    assert imsi(api, value).status_code in (400, 422)


@pytest.mark.parametrize(
    "iccid", ["7900000000000000001", "89000000000000001", "890000000000000000012345", "89abc"]
)
def test_iccid_must_be_e118_shaped(api, iccid):
    assert imsi(api, iccid=iccid).status_code == 422


def test_iccid_is_unique_and_only_for_imsi(api):
    made(api, iccid=ICCID)
    assert imsi(api, "001010123456780", ICCID).status_code == 409
    response = api.post(
        URL,
        {
            "@type": "LogicalResource",
            "name": "N",
            "value": "12025550123",
            "resourceCharacteristic": [
                {
                    "@type": "StringCharacteristic",
                    "name": "iccid",
                    "valueType": "string",
                    "value": ICCID,
                }
            ],
        },
    )
    assert response.status_code == 422


def test_iccid_changes_only_before_first_assignment(api, shop):
    sim = made(api)
    patch = {
        "resourceCharacteristic": [
            {
                "@type": "StringCharacteristic",
                "name": "iccid",
                "valueType": "string",
                "value": ICCID,
            }
        ]
    }
    changed = api.patch(sim["href"], patch)
    assert (
        changed.status_code == 200 and changed.json()["resourceCharacteristic"][0]["value"] == ICCID
    )
    other = "8900000000000000002"
    patch["resourceCharacteristic"][0]["value"] = other
    assert api.patch(sim["href"], patch).status_code == 200
    n = number(api)
    assert add(api, shop, n["id"], sim["id"])["state"] == "completed"
    patch["resourceCharacteristic"][0]["value"] = ICCID
    assert api.patch(sim["href"], patch).status_code == 409
    assert api.get(sim["href"]).json()["resourceCharacteristic"][0]["value"] == other


# --- TMF620 ---


def test_imsi_requirement_is_frozen_after_ordering(api, shop):
    n, sim = number(api), made(api)
    assert add(api, shop, n["id"], sim["id"])["state"] == "completed"
    response = api.patch(
        shop[0]["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    assert response.status_code == 409


# --- TMF622 / TMF637 ---


def test_add_with_msisdn_and_imsi_then_terminate_locks_the_imsi(api, shop, db_conn):
    n, sim = number(api), made(api)
    placed = add(api, shop, n["id"], sim["id"])
    assert placed["state"] == "completed"
    product = apidata.product_of(api, placed)
    assert {r["id"] for r in product["realizingResource"]} == {n["id"], sim["id"]}
    assert api.get(sim["href"]).json()["usageState"] == "busy"
    found = api.get(f"{PRODUCT}?realizingResource.id={sim['id']}").json()
    assert [p["id"] for p in found] == [product["id"]]
    subscribe(api, "TMF639", "ResourceAttributeValueChangeEvent")
    ended = apidata.order(api, shop[1], apidata.delete_item(product["id"]))
    assert ended["state"] == "completed"
    released = api.get(sim["href"]).json()
    assert released["administrativeState"] == "locked"
    assert released["allocationStatus"] == "available"
    assert api.get(n["href"]).json()["administrativeState"] == "unlocked"
    assert "ResourceAttributeValueChangeEvent" in [e for e, _ in stored(db_conn, "TMF639")]
    assert add(api, shop, n["id"], sim["id"])["state"] == "rejected"
    assert api.patch(sim["href"], {"administrativeState": "unlocked"}).status_code == 200
    assert add(api, shop, n["id"], sim["id"])["state"] == "completed"


def test_imsi_only_change_keeps_the_number_and_locks_the_old_imsi(api, shop, db_conn):
    n, old, new = number(api), made(api), made(api, "001010123456788")
    product = apidata.product_of(api, add(api, shop, n["id"], old["id"]))
    day = timeline.TOMORROW + timedelta(days=2)
    placed = modify(api, shop[1], product, n["id"], new["id"], day=day)
    assert placed["state"] == "completed"
    # accepted: the old IMSI is locked now, yet still assigned until the release date
    assert api.get(old["href"]).json()["administrativeState"] == "locked"
    assert api.get(old["href"]).json()["usageState"] == "busy"
    assert {r["id"] for r in api.get(product["href"]).json()["realizingResource"]} == {
        n["id"],
        old["id"],
    }
    api.clock.set(timeline.midnight(day))
    assert {r["id"] for r in api.get(product["href"]).json()["realizingResource"]} == {
        n["id"],
        new["id"],
    }
    assert api.get(old["href"]).json()["usageState"] == "idle"
    assert api.get(f"{PRODUCT}?realizingResource.id={old['id']}").json() == []
    assert db_conn.execute(
        "SELECT count(*) FROM number_assignment WHERE resource_specification_id = 'msisdn'"
    ).fetchone() == (1,)


def test_cancelling_an_imsi_change_leaves_the_imsi_locked_and_assigned(api, shop):
    n, old, new = number(api), made(api), made(api, "001010123456788")
    product = apidata.product_of(api, add(api, shop, n["id"], old["id"]))
    day = timeline.TOMORROW + timedelta(days=2)
    change = modify(api, shop[1], product, n["id"], new["id"], day=day)
    assert cancel(api, change).status_code == 201
    kept = api.get(old["href"]).json()
    assert kept["administrativeState"] == "locked" and kept["usageState"] == "busy"
    api.clock.set(timeline.midnight(day))
    assert {r["id"] for r in api.get(product["href"]).json()["realizingResource"]} == {
        n["id"],
        old["id"],
    }
    assert api.get(new["href"]).json()["allocationStatus"] == "available"


def test_msisdn_and_imsi_change_in_one_item(api, shop):
    n1, n2 = number(api), number(api, "12025550124")
    s1, s2 = made(api), made(api, "001010123456788")
    product = apidata.product_of(api, add(api, shop, n1["id"], s1["id"]))
    placed = modify(api, shop[1], product, n2["id"], s2["id"])
    assert placed["state"] == "completed"
    assert {r["id"] for r in api.get(product["href"]).json()["realizingResource"]} == {
        n2["id"],
        s2["id"],
    }
    assert api.get(s1["href"]).json()["administrativeState"] == "locked"
    assert api.get(n1["href"]).json()["administrativeState"] == "unlocked"


@pytest.mark.parametrize(
    "case,code",
    [
        ("no-imsi", "imsiRequired"),
        ("no-msisdn", "numberRequired"),
        ("unknown", "imsiUnavailable"),
        ("locked", "imsiUnavailable"),
        ("busy", "imsiUnavailable"),
        ("duplicate", "realizingResourceDuplicate"),
    ],
)
def test_add_rejections(api, shop, db_conn, case, code):
    n, sim = number(api), made(api)
    uids = [n["id"], sim["id"]]
    if case == "no-imsi":
        uids = [n["id"]]
    elif case == "no-msisdn":
        uids = [sim["id"]]
    elif case == "unknown":
        uids = [n["id"], str(uuid4())]
    elif case == "locked":
        assert api.patch(sim["href"], {"administrativeState": "locked"}).status_code == 200
    elif case == "busy":
        assert add(api, shop, number(api, "12025550199")["id"], sim["id"])["state"] == "completed"
    elif case == "duplicate":
        uids = [n["id"], sim["id"], made(api, "001010123456788")["id"]]
    count = db_conn.execute("SELECT count(*) FROM product").fetchone()[0]
    placed = add(api, shop, *uids)
    assert placed["state"] == "rejected"
    assert code in order_errors(placed)
    assert db_conn.execute("SELECT count(*) FROM product").fetchone()[0] == count


def test_imsi_not_required_when_the_specification_lists_only_the_msisdn(api, db_conn):
    offering = apidata.sellable_offering(api)
    api.patch(
        offering["productSpecification"]["href"],
        {"resourceSpecification": [{"id": "msisdn", "@type": "ResourceSpecificationRef"}]},
    )
    account = apidata.account(api)
    api.clock.set(timeline.START + timedelta(hours=9))
    n, sim = number(api), made(api)
    placed = add(api, (offering, account), n["id"], sim["id"])
    assert placed["state"] == "rejected"
    assert "imsiNotRequired" in order_errors(placed)


def test_modify_set_must_be_complete_and_changed(api, shop, db_conn):
    n, sim, other = number(api), made(api), made(api, "001010123456788")
    product = apidata.product_of(api, add(api, shop, n["id"], sim["id"]))
    assert modify(api, shop[1], product, n["id"], sim["id"])["state"] == "rejected"
    assert modify(api, shop[1], product, other["id"])["state"] == "rejected"
    assert modify(api, shop[1], product, n["id"], other["id"])["state"] == "completed"


# --- database guarantees ---


def sim_row(conn, value, kind="imsi", iccid=None):
    return conn.execute(
        """INSERT INTO subscriber_number
        (value, name, resource_specification_id, iccid, created_at, updated_at)
        VALUES (%s, 'SIM', %s, %s, %s, %s) RETURNING id""",
        (value, kind, iccid, NOW, NOW),
    ).fetchone()[0]


def kind_claim(conn, uid, prod, kind="imsi"):
    conn.execute(
        """INSERT INTO number_assignment (subscriber_number_id, resource_specification_id,
        product_id, assigned_by_item_id, effective_from, quarantine_days, created_at, updated_at)
        VALUES (%s, %s, %s, %s, '2026-01-01', 0, %s, %s)""",
        (uid, kind, prod["id"], prod["created_by_item_id"], NOW, NOW),
    )


def test_exclusions_refuse_two_live_imsis_on_a_product_and_one_imsi_on_two_products(db_conn):
    w = world(db_conn)
    other = product(db_conn, w.account["id"], w.offering["id"], date(2026, 1, 1))
    a, b = sim_row(db_conn, "001010123456781"), sim_row(db_conn, "001010123456782")
    kind_claim(db_conn, a, w.product)
    with rejected(db_conn, errors.ExclusionViolation):
        kind_claim(db_conn, b, w.product)
    with rejected(db_conn, errors.ExclusionViolation):
        kind_claim(db_conn, a, other)
    # one claim per kind is fine: an MSISDN beside the IMSI
    kind_claim(db_conn, sim_row(db_conn, "12025550123", "msisdn"), w.product, "msisdn")
    # a claim must name the resource's own kind
    with rejected(db_conn, errors.ForeignKeyViolation):
        kind_claim(db_conn, b, other, "msisdn")


@pytest.mark.parametrize(
    "value,kind,iccid",
    [("12345", "imsi", None), ("12025550123", "msisdn", ICCID), ("12025550123", "other", None)],
)
def test_kind_columns_are_checked(db_conn, value, kind, iccid):
    with rejected(db_conn, errors.CheckViolation):
        sim_row(db_conn, value, kind, iccid)


# --- prepaid ---


def test_prepaid_imsi_only_change_runs_as_a_number_only_order(api, db_conn, monkeypatch):
    from app.worker import loop

    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = prepaid_shop(api, db_conn)
    api.patch(
        off["productSpecification"]["href"],
        {
            "resourceSpecification": [
                {"id": "msisdn", "@type": "ResourceSpecificationRef"},
                {"id": "imsi", "@type": "ResourceSpecificationRef"},
            ]
        },
    )
    n, sim, new = number(api, "79991234000"), made(api), made(api, "001010123456788")
    item = apidata.add_item(off, "1")
    item["product"] = {
        "@type": "ProductRefOrValue",
        "realizingResource": all_refs(n["id"], sim["id"]),
    }
    response = api.post(ORDER, apidata.order_body(acc, item))
    assert response.status_code == 201, response.text
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(response.json()["id"])]
    product_id = db_conn.execute("SELECT id::text FROM product").fetchone()[0]
    product = {"id": product_id, "href": f"{PRODUCT}/{product_id}"}
    placed = modify(api, acc, product, n["id"], new["id"])
    assert placed["state"] == "completed" and order_errors(placed) == []
    assert {r["id"] for r in api.get(product["href"]).json()["realizingResource"]} == {
        n["id"],
        new["id"],
    }
    assert api.get(sim["href"]).json()["administrativeState"] == "locked"
