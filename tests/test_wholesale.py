"""Wholesale reconciliation with the network partner's invoices (issue #197).
docs/decisions/wholesale.md."""

from datetime import date, datetime
from decimal import Decimal as D
from uuid import uuid4

import pytest

from app.integrations.network import wholesale as invoice_format
from tests import factories
from tests.apiclient import decimals
from tests.factories import NOW, days
from tests.querycount import counting

URL = "/internal/v1/wholesale/invoice"
HEADER = "msisdn,iccid,period_start,period_end,charge_type,quantity,unit,amount,currency"
MARCH = ("2026-03-01", "2026-04-01")
MB = 1024 * 1024
ICCID = "8901260123456789012"


def number(conn, value, kind="msisdn", iccid=None):
    return conn.execute(
        "INSERT INTO subscriber_number (value, name, resource_specification_id, iccid,"
        " created_at, updated_at) VALUES (%s, 'n', %s, %s, %s, %s) RETURNING id",
        (value, kind, iccid, NOW, NOW),
    ).fetchone()[0]


def claim(conn, number_id, prod, start, end=None, kind="msisdn"):
    conn.execute(
        "INSERT INTO number_assignment (subscriber_number_id, resource_specification_id,"
        " product_id, assigned_by_item_id, effective_from, released_on, ended_by_item_id,"
        " quarantine_days, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s)",
        (
            number_id,
            kind,
            prod["id"],
            prod["created_by_item_id"],
            start,
            end,
            None if end is None else prod["created_by_item_id"],
            NOW,
            NOW,
        ),
    )


def usage(conn, prod, kind, quantity, day):
    conn.execute(
        "INSERT INTO usage_record (id, network_record_id, kind, quantity, event_at, event_date,"
        " product_id, billing_account_id, status, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'notRated', %s)",
        (
            uuid4(),
            f"net-{uuid4()}",
            kind,
            quantity,
            datetime.combine(day, datetime.min.time(), factories.MSK),
            day,
            prod["id"],
            prod["billing_account_id"],
            NOW,
        ),
    )


class World:
    pass


@pytest.fixture
def world(api, db_conn):
    """Prepaid lines for March 2026: clean (P1), live without a charge (P2), charged twice
    (P3), usage off (P4), a number that moves from P5 to P6 on the 15th, a SIM-only match
    (P7), and an add-on (never a line of its own)."""
    conn = db_conn
    w = World()
    w.offering = factories.offering(conn)
    w.account = factories.account(conn)
    conn.execute(
        "UPDATE billing_account SET rating_type = 'prepaid' WHERE id = %s", (w.account["id"],)
    )
    w.run = factories.run(conn)

    def line(start=date(2026, 1, 1), end=None, status="active"):
        prod = factories.product(conn, w.account["id"], w.offering["id"], start)
        conn.execute(
            "UPDATE product SET status = %s, termination_date = %s WHERE id = %s",
            (status, end, prod["id"]),
        )
        return prod

    w.p1, w.p2, w.p3, w.p4 = line(), line(), line(), line()
    w.p5 = line(end=date(2026, 3, 15), status="terminated")
    w.p6 = line(start=date(2026, 3, 15))
    w.p7 = line()
    w.never = line(status="cancelled")
    for prod, value in (
        (w.p1, "12025550101"),
        (w.p2, "12025550102"),
        (w.p3, "12025550103"),
        (w.p4, "12025550104"),
    ):
        claim(conn, number(conn, value), prod, date(2026, 1, 1))
    moved = number(conn, "12025550105")
    claim(conn, moved, w.p5, date(2026, 1, 1), date(2026, 3, 15))
    claim(conn, moved, w.p6, date(2026, 3, 15))
    claim(conn, number(conn, "001010123456787", "imsi", ICCID), w.p7, date(2026, 1, 1), kind="imsi")
    # BSS usage in March: P1 1000 MB (as invoiced), P4 1000 MB (2000 invoiced)
    usage(conn, w.p1, "data", 1000 * MB, date(2026, 3, 10))
    usage(conn, w.p4, "data", 1000 * MB, date(2026, 3, 10))
    usage(conn, w.p4, "data", 5 * MB, date(2026, 4, 2))  # outside the period
    # P1's revenue: 28.00 for Feb 15 - Mar 15 (14 of 28 days in March) and 30.00 for
    # Mar 15 - Apr 14 (17 of 30 days in March): 14.00 + 17.00
    for start, end, amount in (
        (date(2026, 2, 15), date(2026, 3, 15), "28.00"),
        (date(2026, 3, 15), date(2026, 4, 14), "30.00"),
    ):
        factories.charge(
            conn,
            factories.World(w.offering, w.account, w.p1, w.run),
            days(start, end),
            amount,
        )
    conn.commit()
    return w


def csv(*rows):
    return (HEADER + "\n" + "\n".join(",".join(map(str, row)) for row in rows) + "\n").encode()


def row(msisdn, charge, amount, quantity="", unit="", iccid="", period=MARCH):
    return (msisdn, iccid, period[0], period[1], charge, quantity, unit, amount, "USD")


INVOICE = csv(
    row("12025550101", "line", "10.00"),
    row("12025550101", "data", "2.00", "1000", "MB"),
    row("12025550103", "line", "10.00"),
    row("12025550103", "line", "10.00", period=("2026-03-10", "2026-04-01")),
    row("12025550104", "line", "10.00"),
    row("12025550104", "data", "4.00", "2000", "MB"),
    row("12025550105", "line", "5.00", period=("2026-03-01", "2026-03-15")),
    row("12025550105", "line", "5.00", period=("2026-03-15", "2026-04-01")),
    row("", "line", "10.00", iccid=ICCID),
    row("12025550199", "line", "10.00"),
)


def upload(api, content, number="TMO-2026-03"):
    return api.request(
        "POST",
        f"{URL}?invoiceNumber={number}",
        content=content,
        headers={"Content-Type": "text/csv"},
    )


def discrepancies(api, invoice, kind=None):
    query = f"?kind={kind}&limit=100" if kind else "?limit=100"
    response = api.get(f"{URL}/{invoice['id']}/discrepancies{query}")
    assert response.status_code == 200, response.text
    return response.json()


# --- reconciliation ---


def test_every_discrepancy_kind_and_a_clean_line(api, db_conn, world):
    response = upload(api, INVOICE)
    assert response.status_code == 201, response.text
    invoice = response.json()
    assert invoice["state"] == "reconciled" and invoice["lineCount"] == 10
    assert invoice["period"] == {"start": "2026-03-01", "end": "2026-04-01"}
    assert invoice["total"] == {"unit": "USD", "value": D("76.00")}
    assert invoice["discrepancies"] == {
        "billedNotActive": 1,
        "activeNotBilled": 1,
        "usageMismatch": 1,
        "duplicateCharge": 1,
    }
    found = discrepancies(api, invoice)
    by_kind = {d["kind"]: d for d in found}
    assert by_kind["billedNotActive"]["msisdn"] == "12025550199"
    assert by_kind["billedNotActive"]["product"] is None
    assert by_kind["billedNotActive"]["lineNumber"] == 10
    assert by_kind["activeNotBilled"]["product"]["id"] == str(world.p2["id"])
    assert by_kind["activeNotBilled"]["msisdn"] == "12025550102"
    assert by_kind["activeNotBilled"]["expected"] == 31  # live days in March
    mismatch = by_kind["usageMismatch"]
    assert mismatch["product"]["id"] == str(world.p4["id"]) and mismatch["usageKind"] == "data"
    assert (D(str(mismatch["expected"])), D(str(mismatch["invoiced"]))) == (D("1000"), D("2000"))
    assert by_kind["duplicateCharge"]["lineNumber"] == 4
    assert by_kind["duplicateCharge"]["detail"]["duplicateOf"] == 3
    # P1, P5, P6, P7 are clean; P1's usage matches; the add-on-less cancelled line is ignored
    flagged = {d["product"]["id"] for d in found if d["product"]}
    assert not flagged & {
        str(world.p1["id"]),
        str(world.p5["id"]),
        str(world.p6["id"]),
        str(world.p7["id"]),
        str(world.never["id"]),
    }
    assert discrepancies(api, invoice, "duplicateCharge") == [by_kind["duplicateCharge"]]


def test_a_number_that_moves_mid_period_maps_each_line_to_its_holder(api, db_conn, world):
    upload(api, INVOICE)
    db_conn.commit()
    rows = db_conn.execute(
        "SELECT line_number, product_id FROM wholesale_invoice_line"
        " WHERE msisdn = '12025550105' OR iccid IS NOT NULL ORDER BY line_number"
    ).fetchall()
    db_conn.commit()
    assert rows == [(7, world.p5["id"]), (8, world.p6["id"]), (9, world.p7["id"])]


def test_cost_revenue_and_margin_per_line_are_exact(api, world):
    invoice = upload(api, INVOICE).json()
    lines = api.get(f"{URL}/{invoice['id']}/lines?limit=100").json()
    p1 = next(
        line for line in lines if line["product"] and line["product"]["id"] == str(world.p1["id"])
    )
    assert p1["cost"]["value"] == D("12.00") and p1["invoiceLines"] == 2
    assert p1["revenue"]["value"] == D("31.00") and p1["margin"]["value"] == D("19.00")
    unmatched = next(line for line in lines if line["product"] is None)
    assert unmatched["msisdn"] == "12025550199" and unmatched["margin"]["value"] == D("-10.00")
    by_margin = api.get(f"{URL}/{invoice['id']}/lines?sort=margin&limit=100").json()
    margins = [line["margin"]["value"] for line in by_margin]
    assert margins == sorted(margins) and by_margin[-1] == p1
    summary = api.get(f"{URL}/{invoice['id']}").json()
    assert summary["cost"]["value"] == D("76.00")
    assert summary["revenue"]["value"] == D("31.00")
    assert summary["margin"]["value"] == D("-45.00")
    assert summary["unmatchedNumbers"] == 1 and summary["matchedProducts"] == 6
    listed = api.get(URL).json()
    assert [i["id"] for i in listed] == [invoice["id"]]


def test_usage_within_the_tolerance_is_no_mismatch(api, world):
    # 1015 MB against 1000: 1.5%, and 15 MB > 10 MB - under 2%, so no mismatch
    content = csv(
        row("12025550101", "line", "10.00"),
        row("12025550101", "data", "2.00", "1015", "MB"),
    )
    invoice = upload(api, content, "TOL-1").json()
    assert invoice["discrepancies"]["usageMismatch"] == 0


# --- the file ---


def test_a_duplicate_invoice_number_is_a_409(api, world):
    assert upload(api, INVOICE).status_code == 201
    again = upload(api, INVOICE)
    assert again.status_code == 409


@pytest.mark.parametrize(
    "content, where",
    [
        (csv(row("12025550101", "line", "ten")), "row 1, column amount"),
        (csv(row("12025550101", "line", "1.001")), "row 1, column amount"),
        (csv(row("12025550101", "line", "1.00"), row("x", "line", "1.00")), "row 2, column msisdn"),
        (csv(row("12025550101", "data", "1.00", "5", "minute")), "row 1, column unit"),
        (csv(row("12025550101", "fee", "1.00")), "row 1, column charge_type"),
        (
            csv(row("12025550101", "line", "1.00", period=("2026-03-05", "2026-03-01"))),
            "row 1, column period_end",
        ),
        (b"msisdn,amount\n1,2\n", "header"),
        (HEADER.encode() + b"\n", "no rows"),
    ],
)
def test_a_malformed_file_is_a_422_naming_row_and_column(api, world, content, where):
    response = upload(api, content, "BAD-1")
    assert response.status_code == 422
    assert where in response.json()["message"]


def test_a_float_looking_amount_is_exact(api, world):
    content = csv(
        row("12025550101", "line", "0.1"),
        row("12025550101", "data", "0.2", "1000", "MB"),
    )
    invoice = decimals(upload(api, content, "EXACT-1"))
    assert invoice["total"]["value"] == D("0.30")
    parsed = invoice_format.parse_invoice(content)
    assert parsed.total == D("0.3") and all(isinstance(line.amount, D) for line in parsed.lines)


def test_content_type_and_invoice_number_are_required(api, world):
    wrong = api.request(
        "POST", f"{URL}?invoiceNumber=X1", content=INVOICE, headers={"Content-Type": "text/plain"}
    )
    assert wrong.status_code == 415
    missing = api.request("POST", URL, content=INVOICE, headers={"Content-Type": "text/csv"})
    assert missing.status_code == 400


def test_too_many_rows_is_a_413(api, world, monkeypatch):
    monkeypatch.setattr(invoice_format, "MAX_LINES", 3)
    content = csv(*[row("12025550101", "other", "1.00")] * 4)
    assert upload(api, content, "BIG-1").status_code == 413


def test_imported_rows_are_immutable(api, db_conn, world):
    upload(api, INVOICE)
    db_conn.commit()
    with pytest.raises(Exception, match="immutable"):
        db_conn.execute("UPDATE wholesale_invoice_line SET amount = 0")
    db_conn.rollback()
    with pytest.raises(Exception, match="immutable"):
        db_conn.execute("DELETE FROM wholesale_invoice")
    db_conn.rollback()


def test_the_import_costs_the_same_statements_for_10_or_100_lines(api, world, monkeypatch):
    def statements(n, number):
        content = csv(*[row("12025550101", "other", "1.00")] * n)
        with counting(monkeypatch) as seen:
            response = api.client.post(
                f"{URL}?invoiceNumber={number}",
                content=content,
                headers={"Content-Type": "text/csv"},
            )
        assert response.status_code == 201, response.text
        return len(seen)

    assert statements(10, "Q-10") == statements(100, "Q-100")
