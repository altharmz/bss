"""Pooled data per account (issue #379): the pool's usage and thresholds, its network
commands, the summary and the catalog rule. docs/decisions/usage.md "Pooled data"."""
# ruff: noqa: F811

from datetime import date, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from app.billing import data_pool
from app.billing.core import cycle_period
from app.network.usage import Incoming, ingest, ingest_batch
from app.notify import events
from tests import timeline
from tests.apidata import CATALOG
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.prepaid.usage import line_on, summary
from tests.builders.resources.network_provisioning import (  # noqa: F401 - fixtures
    make_line,
    state,
    stub,
    world,
)

pytestmark = pytest.mark.capability("worker.dataPool")

MIB = 1024 * 1024
A, B, C, D = "79991234501", "79991234502", "79991234503", "79991234504"


@pytest.fixture
def pooled(api, db_conn, world):
    """world.off pools 1000 MB per line; world.cheaper (1000 MB) is a line of its own."""
    make_line(api, world.off, dataPool=1, dataAllowanceMb=1000)
    return world


def period(db_conn, acc, day):
    cycle_day = db_conn.execute(
        "SELECT bill_cycle_day FROM billing_account WHERE id = %s", (acc["id"],)
    ).fetchone()[0]
    db_conn.commit()
    return cycle_period(day, cycle_day)


def pool_marks(db_conn):
    rows = db_conn.execute(
        "SELECT t.threshold, u.network_record_id, t.used, t.included, t.product_id::text"
        " FROM usage_pool_threshold t JOIN usage_record u ON u.id = t.usage_record_id"
        " ORDER BY t.threshold"
    ).fetchall()
    db_conn.commit()
    return rows


def line_marks(db_conn):
    rows = db_conn.execute(
        "SELECT product_id::text, allowance, threshold FROM usage_threshold ORDER BY 1, 2, 3"
    ).fetchall()
    db_conn.commit()
    return rows


def pool_commands(db_conn):
    """(product, kind, purpose, pool size) of every command carrying a pool, oldest first."""
    rows = db_conn.execute(
        "SELECT product_id::text, kind, purpose, (data_pool->>'mb')::int, state"
        " FROM network_command WHERE data_pool IS NOT NULL"
        # the test clock stands still inside a wake-up: the size orders a member's commands
        " ORDER BY created_at, (data_pool->>'mb')::int, kind, product_id"
    ).fetchall()
    db_conn.commit()
    return rows


def data_entry(body):
    return next(a for a in body["allowances"] if (a["zone"], a["kind"]) == ("domestic", "data"))


def test_three_lines_share_one_allowance_and_mark_80_and_100_once(api, db_conn, pooled, stub):
    a = line_on(api, db_conn, pooled.off, pooled.acc, A)
    b = line_on(api, db_conn, pooled.off, pooled.acc, B)
    c = line_on(api, db_conn, pooled.off, pooled.acc, C)
    at, hour = timeline.midnight(api.clock.today()) + timedelta(hours=12), timedelta(hours=1)
    batch = [
        Incoming("p-1", "data", 1500 * MIB, at, msisdn=A),  # over A's own 1000 MB: no line mark
        Incoming("p-2", "data", 900 * MIB, at + hour, msisdn=B),  # 2400 of 3000: 80 %
        Incoming("p-3", "voice", 600, at + hour, msisdn=C),  # voice stays per line
        Incoming("p-4", "data", 100 * MIB, at + 2 * hour, msisdn=C),
        Incoming("p-5", "data", 50 * MIB, at + 2 * hour, msisdn=A, roaming=True),  # not pooled
        Incoming("p-6", "data", 600 * MIB, at + 3 * hour, msisdn=A),  # 3100: 100 %
        Incoming("p-7", "data", 10 * MIB, at + 4 * hour, msisdn=B),  # over: still once
    ]

    def stored():
        return pool_marks(db_conn), line_marks(db_conn)

    now = api.clock.now()
    with db_conn.transaction() as one_by_one:
        for incoming in batch:
            ingest(db_conn, incoming, now=now, tz=timeline.TZ)
        expected = (
            db_conn.execute(
                "SELECT t.threshold, u.network_record_id, t.used, t.included, t.product_id::text"
                " FROM usage_pool_threshold t JOIN usage_record u ON u.id = t.usage_record_id"
                " ORDER BY t.threshold"
            ).fetchall(),
            db_conn.execute(
                "SELECT product_id::text, allowance, threshold FROM usage_threshold"
                " ORDER BY 1, 2, 3"
            ).fetchall(),
        )
        raise psycopg.Rollback(one_by_one)
    assert stored() == ([], [])
    assert ingest_batch(db_conn, batch, now=now, tz=timeline.TZ) == 7
    db_conn.commit()
    assert stored() == expected  # the feed's batch path gives the same rows
    assert expected[0] == [
        (80, "p-2", 2400 * MIB, 3000 * MIB, b["id"]),
        (100, "p-6", 3100 * MIB, 3000 * MIB, a["id"]),
    ]
    # no per-line data mark (pooled), and C's 600 s of voice are far from its 300 minutes
    assert expected[1] == []

    body = summary(api, c)
    data = data_entry(body)
    assert data["pooled"] is True
    assert data["used"] == 3110 * MIB and data["included"] == 3000 * MIB
    assert data["remaining"] == 0 and data["percent"] == 103 and data["thresholds"] == [80, 100]
    pool_period = period(db_conn, pooled.acc, api.clock.today())
    assert data["pool"] == {
        "id": pooled.acc["id"],
        "startDate": pool_period.start.isoformat(),
        "endDate": pool_period.end.isoformat(),
        "members": 3,
    }
    voice = next(x for x in body["allowances"] if (x["zone"], x["kind"]) == ("domestic", "voice"))
    assert voice["used"] == 600 and voice["pooled"] is False and voice["pool"] is None

    # the account is told once per threshold, naming the line that crossed it
    told = [e for e in events._usage_pool(db_conn, now - timedelta(days=1))]
    assert [(e.type, e.product_id, e.params["allowance"]) for e in told] == [
        ("usage_80", UUID(b["id"]), "shared data allowance"),
        ("usage_100", UUID(a["id"]), "shared data allowance"),
    ]
    assert {e.key for e in told} == {
        f"usage_pool:{pooled.acc['id']}:{pool_period.start}:80",
        f"usage_pool:{pooled.acc['id']}:{pool_period.start}:100",
    }
    db_conn.rollback()


def test_a_line_outside_the_pool_is_tracked_alone(api, db_conn, pooled, stub):
    a = line_on(api, db_conn, pooled.off, pooled.acc, A)
    alone = line_on(api, db_conn, pooled.cheaper, pooled.acc, B)  # 1000 MB, not pooled
    at = timeline.midnight(api.clock.today()) + timedelta(hours=12)
    now = api.clock.now()
    ingest_batch(
        db_conn,
        [
            Incoming("q-1", "data", 900 * MIB, at, msisdn=B),  # 90 % of its own
            Incoming("q-2", "data", 100 * MIB, at, msisdn=A),
        ],
        now=now,
        tz=timeline.TZ,
    )
    db_conn.commit()
    assert line_marks(db_conn) == [(alone["id"], "data", 80)]
    assert pool_marks(db_conn) == []
    own = data_entry(summary(api, alone))
    assert own["pooled"] is False and own["pool"] is None
    assert own["used"] == 900 * MIB and own["included"] == 1000 * MIB
    shared = data_entry(summary(api, a))
    assert shared["pooled"] is True and shared["pool"]["members"] == 1
    assert shared["used"] == 100 * MIB and shared["included"] == 1000 * MIB  # B not counted


def test_members_are_told_the_size_when_lines_join_and_when_the_period_rolls(
    api, db_conn, pooled, stub
):
    a = line_on(api, db_conn, pooled.off, pooled.acc, A)
    # the activate of the first member carries the pool: 1000 MB
    assert pool_commands(db_conn) == [(a["id"], "activate", "order", 1000, "confirmed")]
    activate = next(req for kind, req in stub.received.values() if kind == "activate")
    assert activate["dataPoolId"] == pooled.acc["id"] and activate["dataPoolMb"] == 1000
    names = {
        c["name"]: c["value"]
        for c in activate["serviceOrderItem"]["service"]["serviceCharacteristic"]
    }
    assert names["dataPoolMb"] == 1000 and names["dataPool"] == 1

    b = line_on(api, db_conn, pooled.off, pooled.acc, B)
    # B's activate carries 2000 MB; A is told the new size by one change_profile, sent by the
    # same wake-up
    assert sorted(pool_commands(db_conn)[1:]) == sorted(
        [
            (b["id"], "activate", "order", 2000, "confirmed"),
            (a["id"], "change_profile", "dataPool", 2000, "confirmed"),
        ]
    )
    told_a = [
        req
        for kind, req in stub.received.values()
        if kind == "change_profile" and req["msisdn"] == A
    ]
    assert told_a[-1]["dataPoolMb"] == 2000 and told_a[-1]["profile"]["dataAllowanceMb"] == 1000
    assert told_a[-1]["serviceOrderItem"]["action"] == "modify"

    # another wake-up tells nobody again
    before = len(pool_commands(db_conn))
    tick(db_conn, api.clock.now())
    assert len(pool_commands(db_conn)) == before

    # A leaves mid-period: its share stays until the period ends, so nothing is sent
    api.clock.set(api.clock.now() + timedelta(days=2))
    order = terminate(api, pooled.acc, a)
    tick(db_conn, api.clock.now())
    assert state(api, order) == "completed"
    after_leave = pool_commands(db_conn)
    assert all(row[2] != "dataPool" or row[3] == 2000 for row in after_leave)
    tick(db_conn, api.clock.now() + timedelta(days=1))
    assert pool_commands(db_conn) == after_leave

    # C joins: 3000 MB (A's share kept); B is told, A (gone) is not
    c = line_on(api, db_conn, pooled.off, pooled.acc, C)
    tick(db_conn, api.clock.now())
    news = pool_commands(db_conn)[len(after_leave) :]
    assert sorted(news, key=lambda row: row[1]) == [
        (c["id"], "activate", "order", 3000, "confirmed"),
        (b["id"], "change_profile", "dataPool", 3000, "confirmed"),
    ]
    assert data_entry(summary(api, b))["included"] == 3000 * MIB
    assert data_entry(summary(api, b))["pool"]["members"] == 2

    # the next pool period: A is gone, B and C are told 2000 MB
    roll = period(db_conn, pooled.acc, api.clock.today()).end
    api.clock.set(timeline.midnight(roll) + timedelta(hours=1))
    tick(db_conn, api.clock.now())
    rolled = [row for row in pool_commands(db_conn) if row[2] == "dataPool" and row[3] == 2000]
    assert {row[0] for row in rolled[-2:]} == {b["id"], c["id"]}
    assert all(row[4] == "confirmed" for row in rolled[-2:])
    assert data_entry(summary(api, b, date=roll.isoformat()))["included"] == 2000 * MIB


def test_an_unsent_pool_size_is_replaced_by_the_next_one(api, db_conn, pooled, stub):
    flaky = "79991230001"  # the stub fails it temporarily until recovered
    stub.recover(flaky)
    a = line_on(api, db_conn, pooled.off, pooled.acc, flaky)
    stub.recovered.discard(flaky)
    line_on(api, db_conn, pooled.off, pooled.acc, B)
    waiting = [row for row in pool_commands(db_conn) if row[0] == a["id"] and row[2] == "dataPool"]
    assert waiting == [(a["id"], "change_profile", "dataPool", 2000, "pending")]
    line_on(api, db_conn, pooled.off, pooled.acc, C)
    told = [row for row in pool_commands(db_conn) if row[0] == a["id"] and row[2] == "dataPool"]
    assert told == [
        (a["id"], "change_profile", "dataPool", 2000, "cancelled"),
        (a["id"], "change_profile", "dataPool", 3000, "pending"),
    ]
    stub.recover(flaky)
    tick(db_conn, api.clock.now() + timedelta(hours=2))
    told = [row for row in pool_commands(db_conn) if row[0] == a["id"] and row[2] == "dataPool"]
    assert told[-1] == (a["id"], "change_profile", "dataPool", 3000, "confirmed")


def test_a_daily_price_on_a_pooled_offering_is_refused(api, db_conn, pooled):
    daily = api.post(
        f"{CATALOG}/productOfferingPrice",
        {
            "@type": "ProductOfferingPrice",
            "name": "Daily",
            "lifecycleStatus": "Active",
            "priceType": "recurring",
            "recurringChargePeriodType": "daily",
            "price": {"unit": "USD", "value": 1},
            "validFor": {"startDateTime": timeline.iso(api.clock.now() + timedelta(days=1))},
        },
    )
    assert daily.status_code == 201, daily.text
    body = {
        "@type": "ProductOffering",
        "name": "Pooled daily",
        "lifecycleStatus": "Active",
        "productSpecification": {
            "id": pooled.off["productSpecification"]["id"],
            "@type": "ProductSpecificationRef",
        },
        "productOfferingPrice": [{"id": daily.json()["id"], "@type": "ProductOfferingPriceRef"}],
    }
    response = api.post(f"{CATALOG}/productOffering", body)
    assert response.status_code == 422, response.text
    assert "dataPool offering takes a monthly recurring price" in response.text
    # an offering of the same specification that does not pool may price daily
    no_pool = body | {
        "prodSpecCharValueUse": [
            {
                "@type": "ProductSpecCharacteristicValueUse",
                "name": "dataPool",
                "valueType": "integer",
                "productSpecCharacteristicValue": [
                    {
                        "@type": "IntegerCharacteristicValueSpecification",
                        "valueType": "integer",
                        "value": 0,
                    }
                ],
            }
        ]
    }
    make_line(api, pooled.off, dataPool=[1, 0], dataAllowanceMb=1000)
    response = api.post(f"{CATALOG}/productOffering", no_pool)
    assert response.status_code == 201, response.text


def test_pool_size_counts_each_share_from_its_first_day_and_keeps_leavers():
    account, start, end = uuid4(), date(2026, 11, 5), date(2026, 12, 5)
    plan, plan2, other, add_on, gone = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    pooled_offer, alone_offer, pack = uuid4(), uuid4(), uuid4()
    profiles = {
        pooled_offer: {"dataPool": 1, "dataAllowanceMb": 1000},
        alone_offer: {"dataPool": 0, "dataAllowanceMb": 700},
        pack: {"dataAllowanceMb": 500},
    }

    def row(product, offering, first, ended=None, parent=None, grantless=False):
        return {
            "product_id": product,
            "parent_product_id": parent,
            "grantless": grantless,
            "first_day": first,
            "termination_date": ended,
            "offering_id": offering,
        }

    pool = data_pool.build(
        (account, start, end),
        [
            row(plan, pooled_offer, start),
            row(gone, pooled_offer, start, ended=date(2026, 11, 10)),  # left: share kept
            row(plan2, pooled_offer, date(2026, 11, 20)),  # joins on the 20th
            row(other, alone_offer, start),  # not pooled
            row(add_on, pack, date(2026, 11, 15), parent=plan, grantless=True),  # adds data
        ],
        profiles.get,
    )
    assert pool is not None
    assert set(pool.members) == {plan, gone, plan2}
    assert pool.size_mb(start) == 2000
    assert pool.size_mb(date(2026, 11, 12)) == 2000  # gone keeps its share
    assert pool.size_mb(date(2026, 11, 15)) == 2500  # the add-on's 500 MB
    assert pool.size_mb(date(2026, 11, 20)) == 3500
    assert sorted(pool.live(date(2026, 11, 20))) == sorted([plan, plan2])
    assert (
        data_pool.build((account, start, end), [row(other, alone_offer, start)], profiles.get)
        is None
    )
