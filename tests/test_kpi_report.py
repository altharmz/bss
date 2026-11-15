"""GET /internal/v1/report/kpi: prepaid KPIs by month (issue #198, docs/decisions/reporting.md).

The rows are inserted directly (the report only reads): prepaid, postpaid and other-currency
accounts with lines that start and end around three months, add-ons, a cancelled and a
never-live product, and ABRs of every revenue source on daily runs in and out of the range.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.range import Range

from app.config import settings
from app.repository import billing, catalog
from tests import factories
from tests.apiclient import decimals

TZ = ZoneInfo(settings.billing_tz)
TODAY = date(2026, 10, 20)
URL = "/internal/v1/report/kpi"


def _line(conn, account, offering, start, end=None, *, status="active", parent=None):
    product = factories.product(conn, account["id"], offering["id"], start)
    conn.execute(
        "UPDATE product SET termination_date = %s, status = %s, parent_product_id = %s,"
        " grantless = %s WHERE id = %s",
        (end, status, parent, parent is not None, product["id"]),
    )
    return product


def _run(conn, day):
    return factories.run(conn, day)


def _abr(conn, account, run, product, amount, period, **extra):
    return billing.insert_abr(
        conn,
        product_id=product["id"] if product else None,
        billing_account_id=account["id"],
        charge_type=extra.pop("charge_type", "recurring"),
        amount=Decimal(amount),
        currency=account["currency"],
        period=Range(*period, "[)"),
        billing_run_id=run["id"],
        applied_at=run["started_at"],
        product_offering_id=extra.pop("offering_id", None),
        **extra,
    )


def _one_day(day):
    return (day, date.fromordinal(day.toordinal() + 1))


@pytest.fixture
def world(db_conn):
    conn = db_conn
    offering = factories.offering(conn)
    fee = catalog.create_product_offering_price(
        conn,
        product_offering_id=offering["id"],
        price_type="oneTime",
        amount=Decimal("2.50"),
        currency="USD",
        valid_for=Range(factories.T0, None, "[)"),
        name="Activation",
    )
    prepaid = factories.account(conn, name="Prepaid")
    conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (prepaid["id"],)
    )
    postpaid = factories.account(conn, name="Postpaid")
    pound = factories.account(conn, currency="GBP", name="Pound")
    conn.execute("UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (pound["id"],))

    p1 = _line(conn, prepaid, offering, date(2026, 7, 10))
    _line(conn, prepaid, offering, date(2026, 7, 1), date(2026, 8, 15), status="terminated")
    _line(conn, prepaid, offering, date(2026, 8, 5), date(2026, 9, 1), status="terminated")
    p4 = _line(conn, prepaid, offering, date(2026, 9, 10), date(2026, 9, 20), status="terminated")
    p5 = _line(conn, prepaid, offering, date(2026, 10, 5))
    _line(conn, prepaid, offering, date(2026, 10, 25), status="created")  # starts after today
    _line(conn, prepaid, offering, date(2026, 8, 1), date(2026, 11, 1), status="pendingTerminate")
    addon = _line(conn, prepaid, offering, date(2026, 9, 1), date(2026, 10, 1), parent=p1["id"])
    _line(conn, prepaid, offering, date(2026, 9, 1), status="cancelled")
    _line(conn, prepaid, offering, date(2026, 9, 3), date(2026, 9, 3), status="terminated")
    other = _line(conn, postpaid, offering, date(2026, 8, 1))
    gbp = _line(conn, pound, offering, date(2026, 8, 1))

    july, august = _run(conn, date(2026, 7, 1)), _run(conn, date(2026, 8, 10))
    september, october = _run(conn, date(2026, 9, 10)), _run(conn, date(2026, 10, 5))
    _abr(conn, prepaid, july, p1, "10.00", (date(2026, 7, 10), date(2026, 8, 1)))  # before
    aug = _abr(conn, prepaid, august, p1, "30.00", (date(2026, 8, 1), date(2026, 9, 1)))
    _abr(conn, prepaid, september, p1, "30.00", (date(2026, 9, 1), date(2026, 10, 1)))
    _abr(
        conn,
        prepaid,
        september,
        p1,
        "-10.00",
        (date(2026, 8, 20), date(2026, 9, 1)),
        charge_type="refund",
        refunds_rate_id=aug["id"],
    )
    for product, amount in ((addon, "5.00"), (p4, "2.50")):
        _abr(
            conn,
            prepaid,
            september,
            product,
            amount,
            _one_day(date(2026, 9, 10)),
            charge_type="oneTime",
            offering_id=offering["id"],
            one_time_price_id=fee["id"],
            charge_name="Activation",
        )
    command = conn.execute(
        "INSERT INTO prepaid_command (id, billing_account_id, kind, due_date, request, created_at)"
        " VALUES (gen_random_uuid(), %s, 'usage', %s, '{}', %s) RETURNING id",
        (prepaid["id"], date(2026, 10, 5), factories.NOW),
    ).fetchone()[0]
    _abr(
        conn,
        prepaid,
        october,
        p5,
        "1.25",
        _one_day(date(2026, 10, 4)),
        charge_type="oneTime",
        offering_id=offering["id"],
        one_time_price_id=fee["id"],
        charge_name="Usage",
        usage_command_id=command,
    )
    _abr(conn, postpaid, august, other, "100.00", (date(2026, 8, 1), date(2026, 9, 1)))
    _abr(conn, pound, august, gbp, "40.00", (date(2026, 8, 1), date(2026, 9, 1)))
    conn.commit()


@pytest.fixture
def report(api, world):
    api.clock.set(datetime(2026, 10, 20, 12, tzinfo=TZ))

    def get(query="from=2026-08&to=2026-10", status=200):
        response = api.get(f"{URL}?{query}" if query else URL)
        assert response.status_code == status, response.text
        return decimals(response)

    return get


def _usd(value):
    return {"unit": "USD", "value": Decimal(value)}


def test_lines_move_month_by_month_and_closing_is_the_next_opening(report):
    body = report()
    assert (body["currency"], body["from"], body["to"]) == ("USD", "2026-08", "2026-10")
    assert body["businessDate"] == "2026-10-20"
    rows = {m["month"]: m for m in body["months"]}
    assert list(rows) == ["2026-08", "2026-09", "2026-10"]
    subscribers = {k: tuple(v["subscribers"].values()) for k, v in rows.items()}
    # opening, grossAdds, disconnects, netAdds, closing, average
    assert subscribers == {
        "2026-08": (2, 2, 1, 1, 3, Decimal("2.5")),
        "2026-09": (3, 1, 2, -1, 2, Decimal("2.5")),
        "2026-10": (2, 1, 0, 1, 3, Decimal("2.5")),
    }
    assert [m["churnRate"] for m in body["months"]] == [
        Decimal("50.00"),
        Decimal("66.67"),
        Decimal("0.00"),
    ]
    assert [(m["firstDay"], m["lastDay"], m["complete"]) for m in body["months"]] == [
        ("2026-08-01", "2026-08-31", True),
        ("2026-09-01", "2026-09-30", True),
        ("2026-10-01", "2026-10-20", False),
    ]


def test_revenue_by_source_and_arpu(report):
    months = report()["months"]
    assert [m["revenue"] for m in months] == [
        {
            "total": _usd("30.00"),
            "plans": _usd("30.00"),
            "addOns": _usd("0.00"),
            "usage": _usd("0.00"),
            "fees": _usd("0.00"),
        },
        {
            "total": _usd("27.50"),
            "plans": _usd("20.00"),  # the September charge less the refund of August's
            "addOns": _usd("5.00"),
            "usage": _usd("0.00"),
            "fees": _usd("2.50"),
        },
        {
            "total": _usd("1.25"),
            "plans": _usd("0.00"),
            "addOns": _usd("0.00"),
            "usage": _usd("1.25"),
            "fees": _usd("0.00"),
        },
    ]
    assert [m["arpu"] for m in months] == [_usd("12.00"), _usd("11.00"), _usd("0.50")]


def test_another_currency_reports_its_own_prepaid_accounts(report):
    months = report("from=2026-08&to=2026-08&currency=GBP")["months"]
    assert [tuple(m["subscribers"].values()) for m in months] == [(0, 1, 0, 1, 1, Decimal("0.5"))]
    assert months[0]["churnRate"] is None
    assert months[0]["revenue"]["total"] == {"unit": "GBP", "value": Decimal("40.00")}
    assert months[0]["arpu"] == {"unit": "GBP", "value": Decimal("80.00")}


def test_an_empty_month_has_no_churn_or_arpu(report):
    (month,) = report("from=2026-01&to=2026-01")["months"]
    assert tuple(month["subscribers"].values()) == (0, 0, 0, 0, 0, Decimal("0"))
    assert month["churnRate"] is None and month["arpu"] is None
    assert month["revenue"]["total"] == _usd("0.00")


def test_defaults_to_the_last_twelve_months(report):
    body = report(None)
    assert (body["from"], body["to"]) == ("2025-11", "2026-10")
    assert len(body["months"]) == 12
    assert body["months"][-1]["subscribers"]["closing"] == 3


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-8",
        "to=2026-13",
        "to=2026-11",  # after the business month
        "from=2026-10&to=2026-09",
        "from=2024-10&to=2026-10",  # 25 months
        "currency=usd",
        "month=2026-10",
    ],
)
def test_bad_parameters_are_a_400(report, query):
    report(query, status=400)


def test_twenty_four_months_are_allowed(report):
    assert len(report("from=2024-11&to=2026-10")["months"]) == 24
