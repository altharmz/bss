"""Usage records (TMF635) and allowance tracking for prepaid lines (issue #190)."""
# ruff: noqa: F811

from datetime import datetime, time, timedelta

import psycopg
import pytest

from app.config import settings
from app.network.usage import Incoming, ingest, ingest_batch
from app.worker import usage as usage_feed
from tests import apidata, contract, timeline
from tests.apiclient import total
from tests.apidata import ORDER
from tests.builders.prepaid.prepaid_offering_changes import switch
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.prepaid.usage import MSISDN, SUMMARY, line_on, summary
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    state,
    stub,
    world,
)
from tests.builders.resources.number_orders import refs

USAGE = "/tmf-api/productUsageManagement/v5/productUsage"


def rejected_records(api, db_conn):
    """The rejected records (no line), read one by one: a list needs product.id or id."""
    db_conn.commit()
    ids = [
        r[0]
        for r in db_conn.execute(
            "SELECT id FROM usage_record WHERE status = 'rejected' ORDER BY event_at DESC, id"
        )
    ]
    return [api.get(f"{USAGE}?id={i}").json()[0] for i in ids]


MIB = 1024 * 1024


def when(api, days=0, hour=12):
    return timeline.midnight(api.clock.today() + timedelta(days=days)) + timedelta(hours=hour)


def record_id(kind, quantity, index, event_at, extra):
    return (
        f"rec-{extra.get('msisdn') or extra.get('imsi')}-{kind}-{quantity}-{index}-{event_at:%d%H}"
    )


def feed(stub, db_conn, api, *records):
    for index, (kind, quantity, event_at, extra) in enumerate(records):
        stub.inject_usage(
            record_id(kind, quantity, index, event_at, extra), kind, quantity, event_at, **extra
        )
    tick(db_conn, api.clock.now())


def records(api, product):
    return api.get(f"{USAGE}?product.id={product['id']}&limit=100").json()


def allowance(body, kind):
    return next(a for a in body["allowances"] if a["kind"] == kind)


@pytest.mark.capability("worker.usageFeed")
def test_a_feed_batch_stores_records_once_even_when_resent(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    batch = [
        ("data", 10 * MIB, when(api), {"msisdn": MSISDN}),
        ("voice", 120, when(api, hour=13), {"msisdn": MSISDN, "roaming": True}),
        ("sms", 1, when(api, hour=14), {"msisdn": MSISDN}),
    ]
    feed(stub, db_conn, api, *batch)
    assert len(records(api, product)) == 3
    assert stub.usage_queue == [] and len(stub.usage_acked) == 3  # acknowledged after the commit
    # the network sends the same batch again (an acknowledgement was lost)
    stub.usage_acked.clear()
    for index, (kind, quantity, event_at, extra) in enumerate(batch):
        stub.inject_usage(
            record_id(kind, quantity, index, event_at, extra), kind, quantity, event_at, **extra
        )
    tick(db_conn, api.clock.now())
    assert len(records(api, product)) == 3
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (3,)
    assert len(stub.usage_acked) == 3
    voice = next(r for r in records(api, product) if r["usageType"] == "voice")
    names = {c["name"]: c["value"] for c in voice["usageCharacteristic"]}
    assert names["quantity"] == 120 and names["unit"] == "seconds" and names["roaming"] is True
    assert voice["status"] == "notRated" and voice["product"]["id"] == product["id"]
    assert voice["externalIdentifier"][0]["owner"] == "network"


@pytest.mark.capability("worker.usageFeed")
def test_a_batch_is_stored_as_its_records_would_be_one_by_one(api, db_conn, world, stub):
    line_on(api, db_conn, world.off, world.acc)  # 5000 MB, 300 min, 100 sms
    at, hour = when(api), timedelta(hours=1)
    batch = [
        Incoming("b-1", "data", 3000 * MIB, at, msisdn=MSISDN),
        Incoming("b-2", "sms", 79, at, msisdn=MSISDN),
        Incoming("b-3", "data", 1000 * MIB, at + hour, msisdn=MSISDN),  # data: 80 %
        Incoming("b-3", "data", 5 * MIB, at + 2 * hour, msisdn=MSISDN),  # the id again: a replay
        Incoming("b-4", "sms", 30, at, msisdn=MSISDN),  # sms: 80 % and 100 % at once
        Incoming("b-5", "voice", 60, at, msisdn="79990000001"),  # nobody's number
        Incoming("b-6", "data", 1200 * MIB, at + 3 * hour, msisdn=MSISDN),  # 100 %
        # roaming counts only against the roaming allowance (none here, #200): no mark
        Incoming("b-9", "data", 2000 * MIB, at + 4 * hour, msisdn=MSISDN, roaming=True),
        Incoming("b-7", "voice", 120, at, imsi="001010000000009", throttled=True),  # nobody's SIM
        Incoming(
            "b-8", "data", 1 * MIB, at - timedelta(days=400), msisdn=MSISDN
        ),  # before the line
    ]

    def stored():
        rows = db_conn.execute(
            "SELECT network_record_id, kind, quantity, roaming, throttled, msisdn, imsi, event_at,"
            " event_date, product_id, billing_account_id, status, rejection_reason"
            " FROM usage_record WHERE network_record_id LIKE 'b-%' ORDER BY network_record_id"
        ).fetchall()
        marks = db_conn.execute(
            "SELECT t.allowance, t.threshold, u.network_record_id, t.used, t.included,"
            " t.period_start, t.period_end FROM usage_threshold t"
            " JOIN usage_record u ON u.id = t.usage_record_id ORDER BY 1, 2"
        ).fetchall()
        return rows, marks

    now = api.clock.now()
    with db_conn.transaction() as one_by_one:
        news = [ingest(db_conn, incoming, now=now, tz=timeline.TZ)[1] for incoming in batch]
        expected = stored()
        raise psycopg.Rollback(one_by_one)
    assert news.count(True) == 9 and stored() == ([], [])
    assert ingest_batch(db_conn, batch, now=now, tz=timeline.TZ) == 9
    assert stored() == expected
    assert [mark[:5] for mark in expected[1]] == [
        ("data", 80, "b-3", 4000 * MIB, 5000 * MIB),
        ("data", 100, "b-6", 5200 * MIB, 5000 * MIB),
        ("sms", 80, "b-4", 109, 100),
        ("sms", 100, "b-4", 109, 100),
    ]
    assert [row[11] for row in expected[0]].count("rejected") == 3
    # the whole batch again (an acknowledgement was lost): nothing is new
    assert ingest_batch(db_conn, batch, now=now, tz=timeline.TZ) == 0
    assert stored() == expected
    db_conn.rollback()


def backlog(stub, api, count):
    for index in range(count):
        stub.inject_usage(f"bulk-{index}", "sms", 1, when(api), msisdn=MSISDN)


@pytest.mark.capability("worker.usageFeed")
def test_one_wake_up_works_off_a_backlog_batch_by_batch(api, db_conn, world, stub, monkeypatch):
    product = line_on(api, db_conn, world.off, world.acc)
    monkeypatch.setattr(usage_feed, "BATCH", 5)
    acknowledged = []
    ack = stub.ack_usage
    monkeypatch.setattr(stub, "ack_usage", lambda ids: (acknowledged.append(len(ids)), ack(ids)))
    backlog(stub, api, 12)
    tick(db_conn, api.clock.now())
    # every batch in its own transaction, acknowledged after it; the short one ends the tick
    assert acknowledged == [5, 5, 2] and stub.usage_queue == []
    assert len(records(api, product)) == 12


@pytest.mark.capability("worker.usageFeed")
def test_the_feed_budget_leaves_the_rest_of_a_backlog_to_the_next_wake_up(
    api, db_conn, world, stub, monkeypatch
):
    line_on(api, db_conn, world.off, world.acc)
    monkeypatch.setattr(usage_feed, "BATCH", 5)
    monkeypatch.setattr(usage_feed, "FEED_BUDGET", timedelta(0))
    backlog(stub, api, 12)
    tick(db_conn, api.clock.now())
    assert len(stub.usage_queue) == 7
    tick(db_conn, api.clock.now())
    assert len(stub.usage_queue) == 2
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (10,)


@pytest.mark.capability("worker.usageFeed")
def test_a_batch_that_is_not_acknowledged_ends_the_wake_up_and_is_stored_once(
    api, db_conn, world, stub, monkeypatch
):
    line_on(api, db_conn, world.off, world.acc)
    monkeypatch.setattr(usage_feed, "BATCH", 5)
    backlog(stub, api, 12)
    with monkeypatch.context() as down:
        down.setattr(stub, "ack_usage", lambda ids: (_ for _ in ()).throw(OSError("network")))
        tick(db_conn, api.clock.now())  # the same batch is not fetched again and again
    assert len(stub.usage_queue) == 12
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (5,)
    tick(db_conn, api.clock.now())  # resent: the stored five are replays
    assert stub.usage_queue == []
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (12,)


@pytest.mark.capability("worker.usageFeed")
def test_a_resend_with_another_event_date_is_a_replay(api, db_conn, world, stub):
    """The unique key is (network_record_id, event_date) since usage_record is partitioned
    by date (#282). A record id that comes back with another date is found by the id alone
    and answered with the stored record; the resend of the same date as well."""
    product = line_on(api, db_conn, world.off, world.acc)
    stub.inject_usage("rec-moved", "data", MIB, when(api), msisdn=MSISDN)
    tick(db_conn, api.clock.now())
    stub.inject_usage("rec-moved", "data", MIB, when(api), msisdn=MSISDN)  # the same date
    stub.inject_usage("rec-moved", "data", 2 * MIB, when(api, 1), msisdn=MSISDN)  # next day
    tick(db_conn, api.clock.now())
    stored = db_conn.execute(
        "SELECT event_date, quantity FROM usage_record WHERE network_record_id = 'rec-moved'"
    ).fetchall()
    assert stored == [(api.clock.today(), MIB)]
    assert len(records(api, product)) == 1 and len(stub.usage_acked) == 3


@pytest.mark.capability("worker.usageFeed")
def test_records_map_to_the_product_that_held_the_number_across_a_number_change(
    api, db_conn, world, stub
):
    product = line_on(api, db_conn, world.off, world.acc)
    new = number(api, value="79991234511")
    change = api.clock.today() + timedelta(days=5)
    switch_number = apidata.order_body(
        world.acc,
        {
            "id": "1",
            "action": "modify",
            "product": {
                "id": product["id"],
                "@type": "ProductRefOrValue",
                "realizingResource": refs(new["id"]),
            },
        },
        requestedStartDate=timeline.midnight(change).isoformat(),
    )
    assert api.post(ORDER, switch_number).status_code == 201
    feed(
        stub,
        db_conn,
        api,
        ("data", MIB, when(api, 1), {"msisdn": MSISDN}),  # old number, before the change
        ("data", 2 * MIB, when(api, 6), {"msisdn": "79991234511"}),  # new number, after it
        ("data", 4 * MIB, when(api, 6, 13), {"msisdn": MSISDN}),  # old number, after it: no line
        ("data", 8 * MIB, when(api, 1, 13), {"msisdn": "79991234511"}),  # new, before: no line
    )
    mine = {r["usageCharacteristic"][0]["value"] for r in records(api, product)}
    assert mine == {MIB, 2 * MIB}
    rejected = rejected_records(api, db_conn)
    assert sorted(r["usageCharacteristic"][0]["value"] for r in rejected) == [4 * MIB, 8 * MIB]
    assert all("product" not in r for r in rejected)
    reasons = {
        c["value"]
        for r in rejected
        for c in r["usageCharacteristic"]
        if c["name"] == "rejectionReason"
    }
    assert reasons == {"noLine"}
    total = summary(api, product, date=(api.clock.today() + timedelta(days=6)).isoformat())
    assert allowance(total, "data")["used"] == 3 * MIB  # the rejected ones count for nobody


@pytest.mark.capability("worker.usageFeed")
def test_the_imsi_names_the_line_when_there_is_no_msisdn(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    sim = api.post(
        "/tmf-api/resourceInventory/v5/resource",
        {
            "@type": "LogicalResource",
            "name": "imsi",
            "value": "001010000000001",
            "resourceSpecification": {"id": "imsi", "@type": "ResourceSpecificationRef"},
        },
    )
    assert sim.status_code == 201, sim.text
    feed(stub, db_conn, api, ("sms", 3, when(api), {"imsi": "001010000000001"}))
    assert len(records(api, product)) == 0  # an unassigned IMSI is nobody's
    assert len(rejected_records(api, db_conn)) == 1


@pytest.mark.capability("worker.usageFeed")
def test_period_totals_and_remaining_allowance_include_an_add_on(api, db_conn, world, stub):
    # the line starts on the small plan; the bigger profile reaches the network as a
    # change_profile, exactly as an add-on pack's raised allowance will
    product = line_on(api, db_conn, world.cheaper, world.acc)
    feed(stub, db_conn, api, ("data", 600 * MIB, when(api), {"msisdn": MSISDN}))
    before = summary(api, product)
    assert before["period"]["startDate"] == api.clock.today().isoformat()
    data = allowance(before, "data")
    assert data["used"] == 600 * MIB and data["included"] == 1000 * MIB
    assert data["remaining"] == 400 * MIB and data["percent"] == 60
    assert allowance(before, "voice")["included"] == 60 * 60
    assert before["throttled"] is False

    order = switch(api, world.acc, product, world.off, api.clock.today())
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    after = summary(api, product)
    data = allowance(after, "data")
    assert data["used"] == 600 * MIB and data["included"] == 5000 * MIB  # the raised allowance
    assert data["remaining"] == 4400 * MIB and data["percent"] == 12
    assert allowance(after, "voice")["included"] == 300 * 60
    assert allowance(after, "sms")["included"] == 100


@pytest.mark.capability("worker.usageFeed")
def test_the_80_and_100_percent_events_fire_once_per_period(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)  # 5000 MB, 300 min, 100 sms

    def marks():
        return db_conn.execute(
            "SELECT allowance, threshold FROM usage_threshold ORDER BY allowance, threshold"
        ).fetchall()

    feed(stub, db_conn, api, ("data", 3000 * MIB, when(api), {"msisdn": MSISDN}))
    assert marks() == []
    feed(stub, db_conn, api, ("data", 1000 * MIB, when(api, hour=13), {"msisdn": MSISDN}))
    assert marks() == [("data", 80)]  # 4000 of 5000
    feed(stub, db_conn, api, ("data", 100 * MIB, when(api, hour=14), {"msisdn": MSISDN}))
    assert marks() == [("data", 80)]  # not again
    feed(stub, db_conn, api, ("data", 900 * MIB, when(api, hour=15), {"msisdn": MSISDN}))
    assert marks() == [("data", 80), ("data", 100)]
    feed(stub, db_conn, api, ("data", 50 * MIB, when(api, hour=16), {"msisdn": MSISDN}))
    assert marks() == [("data", 80), ("data", 100)]  # over the allowance: still once
    # one record can pass both marks, and each kind has its own
    feed(stub, db_conn, api, ("sms", 100, when(api), {"msisdn": MSISDN}))
    assert marks() == [("data", 80), ("data", 100), ("sms", 80), ("sms", 100)]
    assert allowance(summary(api, product), "sms")["thresholds"] == [80, 100]
    assert allowance(summary(api, product), "voice")["thresholds"] == []
    first_period = summary(api, product)["period"]

    # the next plan period starts again from zero: the marks fire again, once
    due = timeline.TODAY  # replaced below by the grant's end
    due = db_conn.execute("SELECT upper(period) FROM funded_interval").fetchone()[0]
    tick(db_conn, datetime.combine(due, time(0, 5), timeline.TZ))
    renewed = db_conn.execute("SELECT count(*) FROM funded_interval").fetchone()[0]
    assert renewed == 2
    second = api.clock.today() + timedelta(days=45)
    stub.inject_usage("p2-1", "data", 4100 * MIB, when(api, 45), msisdn=MSISDN)
    stub.inject_usage("p2-2", "data", 100 * MIB, when(api, 45, 13), msisdn=MSISDN)
    tick(db_conn, api.clock.now())
    again = summary(api, product, date=second.isoformat())
    assert again["period"]["startDate"] != first_period["startDate"]
    assert allowance(again, "data")["used"] == 4200 * MIB
    assert allowance(again, "data")["thresholds"] == [80]
    assert db_conn.execute(
        "SELECT count(*) FROM usage_threshold WHERE allowance = 'data'"
    ).fetchone() == (3,)


@pytest.mark.capability("worker.usageFeed")
def test_throttled_when_the_network_reports_it_or_data_passes_the_cap(api, db_conn, world, stub):
    reported = line_on(api, db_conn, world.off, world.acc)
    feed(stub, db_conn, api, ("data", MIB, when(api), {"msisdn": MSISDN}))
    assert summary(api, reported)["throttled"] is False
    feed(
        stub, db_conn, api, ("data", MIB, when(api, hour=13), {"msisdn": MSISDN, "throttled": True})
    )
    assert summary(api, reported)["throttled"] is True  # the network said so

    # no flag from the network, but the cap (5000 MB here) is passed
    other = line_on(api, db_conn, world.off, world.acc, value="79991234522")
    feed(stub, db_conn, api, ("data", 4999 * MIB, when(api), {"msisdn": "79991234522"}))
    assert summary(api, other)["throttled"] is False
    feed(stub, db_conn, api, ("data", MIB, when(api, hour=13), {"msisdn": "79991234522"}))
    assert summary(api, other)["throttled"] is True


def test_a_product_without_a_plan_period_has_usage_but_no_allowance(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    body = summary(api, product, date=(api.clock.today() - timedelta(days=30)).isoformat())
    assert body["period"] is None and body["allowances"] == [] and body["throttled"] is False
    assert api.get(f"{SUMMARY}?product.id=00000000-0000-4000-8000-000000000000").status_code == 404
    assert api.get(SUMMARY).status_code == 400
    assert api.get(f"{SUMMARY}?product.id={product['id']}&x=1").status_code == 400
    assert api.get(f"{SUMMARY}?product.id=nope").status_code == 400


# --- TMF635 ---


def create_body(product, record_id="mediation-1", **over):
    body = {
        "@type": "ProductUsage",
        "usageSpecification": {"id": "data", "@type": "ProductUsageSpecificationRef"},
        "product": {"id": product["id"], "@type": "ProductRef", "@referredType": "Product"},
        "usageCharacteristic": [
            {
                "@type": "IntegerCharacteristic",
                "name": "quantity",
                "valueType": "integer",
                "value": 2048,
            },
            {
                "@type": "BooleanCharacteristic",
                "name": "roaming",
                "valueType": "boolean",
                "value": True,
            },
        ],
        "externalIdentifier": [
            {"@type": "ExternalIdentifier", "owner": "mediation", "id": record_id}
        ],
    }
    return body | over


def test_tmf635_reads_validate_against_the_vendored_spec(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    feed(
        stub,
        db_conn,
        api,
        ("data", MIB, when(api), {"msisdn": MSISDN, "roaming": True}),
        ("voice", 61, when(api, hour=13), {"msisdn": MSISDN}),
        ("sms", 1, when(api, hour=14), {"msisdn": "79990000000"}),  # nobody's: rejected
    )
    line = f"{USAGE}?product.id={product['id']}"
    listed = api.get(f"{line}&limit=100")
    assert listed.status_code == 200 and total(listed, api) == "2"
    body = listed.json() + rejected_records(api, db_conn)
    assert len(body) == 3
    for item in body:
        contract.assert_valid("TMF635", "ProductUsage", item)
        one = api.get(item["href"])
        assert one.status_code == 200 and one.json() == item
    assert [r["usageType"] for r in api.get(f"{line}&usageType=voice").json()] == ["voice"]
    assert total(api.get(f"{line}&status=rejected"), api) == "0"
    assert api.get(f"{line}&limit=1&offset=1").headers["X-Result-Count"] == "1"
    only = api.get(f"{USAGE}?id={body[2]['id']}")
    assert total(only, api) == "1" and only.json() == [body[2]]
    # a list across every line is refused (#286)
    for query in ("", "?limit=100", "?status=rejected", "?usageType=voice"):
        refused = api.get(f"{USAGE}{query}")
        assert refused.status_code == 400, query
        assert refused.json()["message"] == "productUsage list needs product.id or id"
    selected = api.get(f"{USAGE}/{body[0]['id']}?fields=status").json()
    assert set(selected) == {"id", "href", "@type", "status"}
    assert api.get(f"{USAGE}?bogus=1").status_code == 400
    assert api.get(f"{USAGE}/00000000-0000-4000-8000-000000000000").status_code == 404
    assert api.get(f"{USAGE}/nope").status_code == 404


def test_tmf635_post_records_one_usage_idempotently(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    body = create_body(product)
    contract.assert_valid("TMF635", "ProductUsage_FVO", body)
    created = api.post(USAGE, body)
    assert created.status_code == 201, created.text
    usage = created.json()
    contract.assert_valid("TMF635", "ProductUsage", usage)
    assert created.headers["Location"] == usage["href"]
    names = {c["name"]: c["value"] for c in usage["usageCharacteristic"]}
    assert names["quantity"] == 2048 and names["roaming"] is True and names["unit"] == "bytes"
    assert usage["externalIdentifier"][0]["id"] == "mediation-1"
    # the same external id again: the stored usage, nothing new
    again = api.post(USAGE, body)
    assert again.status_code == 201 and again.json() == usage
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (1,)
    # the same id for another kind is a conflict
    assert (
        api.post(USAGE, create_body(product, usageSpecification={"id": "sms"})).status_code == 409
    )
    # a posted usage counts toward the period like a fed one
    posted = api.post(
        USAGE,
        create_body(
            product,
            "mediation-2",
            usageCharacteristic=[
                {
                    "@type": "IntegerCharacteristic",
                    "name": "quantity",
                    "valueType": "integer",
                    "value": 4000 * MIB,
                }
            ],
        ),
    )
    assert posted.status_code == 201, posted.text
    assert allowance(summary(api, product), "data")["thresholds"] == [80]


@pytest.mark.parametrize(
    "change,status",
    [
        ({"usageSpecification": {"id": "fax"}}, 422),
        ({"usageType": "voice"}, 422),
        ({"externalIdentifier": []}, 422),
        ({"usageCharacteristic": []}, 422),
        (
            {
                "usageCharacteristic": [
                    {"@type": "IntegerCharacteristic", "name": "quantity", "value": 0}
                ]
            },
            422,
        ),
        (
            {
                "usageCharacteristic": [
                    {"@type": "StringCharacteristic", "name": "colour", "value": "x"}
                ]
            },
            422,
        ),
        ({"serviceUsage": [{"id": "x"}]}, 422),
        ({"product": {"id": "00000000-0000-4000-8000-000000000000"}}, 422),
        ({"unknown": 1}, 400),
        ({"id": "00000000-0000-4000-8000-000000000001"}, 400),
        ({"@type": "Other"}, 400),
    ],
)
def test_tmf635_post_refusals(api, db_conn, world, stub, change, status):
    product = line_on(api, db_conn, world.off, world.acc)
    body = create_body(product) | change
    response = api.post(USAGE, body)
    assert response.status_code == status, response.text
    assert db_conn.execute("SELECT count(*) FROM usage_record").fetchone() == (0,)


def test_a_usage_is_a_fact_patch_and_delete_are_405(api, db_conn, world, stub):
    product = line_on(api, db_conn, world.off, world.acc)
    usage = api.post(USAGE, create_body(product)).json()
    assert api.patch(usage["href"], {"status": "rated"}).status_code == 405
    assert api.delete(usage["href"]).status_code == 405
    assert api.get(usage["href"]).json()["status"] == "notRated"


def test_the_tmf635_server_path_is_the_specs(api):
    assert contract.spec("TMF635")["servers"][0]["url"].endswith(
        "/tmf-api/productUsageManagement/v5/"
    )
    assert settings.billing_tz  # the event date is the business date in this zone
