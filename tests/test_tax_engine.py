"""Tax engine adapter and the service address of prepaid charges (issue #184)."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.integrations import tax as tax_package
from app.integrations.tax import ChargeLine, TaxUnavailable, adapter
from app.integrations.tax.stub import FAILING_ZIP
from app.worker import loop
from tests import apidata
from tests.apidata import ACCOUNT, ORDER
from tests.test_prepaid_orders import shop
from tests.test_prepaid_renewal_producer import TZ, tick
from tests.test_prepaid_terminations import terminate

CA = {
    "street1": "1 Market St",
    "city": "San Francisco",
    "stateOrProvince": "CA",
    "postCode": "94105",
}
NY = {"street1": "5 Main St", "city": "Albany", "stateOrProvince": "NY", "postCode": "12207-1234"}


def contact(address, contact_type="serviceAddress"):
    return [
        {
            "@type": "Contact",
            "contactType": contact_type,
            "contactMedium": [
                {"@type": "GeographicAddressContactMedium", "country": "US", **address}
            ],
        }
    ]


def set_address(api, acc, address, **kw):
    return api.patch(f"{ACCOUNT}/{acc['id']}", {"contact": contact(address)}, **kw)


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    monkeypatch.setattr(settings, "tax_require_service_address", True)
    monkeypatch.setattr(loop, "scheduled", lambda *a: SimpleNamespace(gap=None, dates=[]))
    adapter().reset()
    yield adapter()
    adapter().reset()


def buy(api, db_conn, address, funds="100"):
    off, acc = shop(api, db_conn, funds=funds, numbers=False)
    if address is not None:
        assert set_address(api, acc, address).status_code == 200
    order = apidata.order(api, acc, apidata.add_item(off))
    return off, acc, order


def charges(conn):
    return conn.execute(
        "SELECT a.id, a.charge_type, a.amount, a.tax_amount, a.applied_tax, b.amount_due"
        " FROM applied_customer_billing_rate a JOIN customer_bill b ON b.id = a.customer_bill_id"
        " ORDER BY a.applied_at, a.charge_type"
    ).fetchall()


@pytest.mark.capability("worker.taxEngine")
def test_a_purchase_gets_the_engine_taxes_and_the_total_is_net_plus_taxes(api, db_conn, engine):
    off, acc, order = buy(api, db_conn, CA)
    assert order["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    ((abr_id, kind, net, tax, lines, due),) = charges(db_conn)
    assert kind == "recurring" and net == D("10.00")
    # 10.00 x 7.25% = 0.725 -> 0.72 (half-even), federal USF 3% = 0.30, CA 911 fee 0.80
    assert [(t["taxCategory"], t["jurisdiction"], D(str(t["taxAmount"]))) for t in lines] == [
        ("sales_tax", "CA state", D("0.72")),
        ("federal_usf", "US federal", D("0.30")),
        ("prepaid_wireless_e911", "CA state", D("0.80")),
    ]
    assert tax == D("1.82") and due == net + tax == D("11.82")  # the flat 20% VAT is not used
    assert db_conn.execute("SELECT tax_amount FROM funded_interval").fetchone() == (D("1.82"),)
    assert db_conn.execute(
        "SELECT state, address->>'stateOrProvince' FROM tax_transaction"
    ).fetchone() == ("committed", "CA")
    assert len(engine.committed) == 1
    # the TMF678 charge shows jurisdiction and type
    body = api.get(f"/tmf-api/customerBillManagement/v5/appliedCustomerBillingRate/{abr_id}")
    assert body.status_code == 200
    assert body.json()["appliedTax"][0]["jurisdiction"] == "CA state"


def test_the_renewal_history_lists_the_engine_tax_lines(api, db_conn):
    """#207: the subscriber card's renewal history shows each tax line with its jurisdiction."""
    _, acc, _ = buy(api, db_conn, CA)
    tick(db_conn, api.clock.now())
    (product,) = api.get(f"/internal/v1/billingAccount/{acc['id']}/products").json()
    (purchase,) = product["renewalHistory"]
    assert purchase["appliedTax"] == [
        {
            "taxCategory": "sales_tax",
            "taxRate": 7.25,
            "taxAmount": {"unit": "USD", "value": 0.72},
            "jurisdiction": "CA state",
        },
        {
            "taxCategory": "federal_usf",
            "taxRate": 3,
            "taxAmount": {"unit": "USD", "value": 0.30},
            "jurisdiction": "US federal",
        },
        {
            "taxCategory": "prepaid_wireless_e911",
            "taxRate": 0,
            "taxAmount": {"unit": "USD", "value": 0.80},
            "jurisdiction": "CA state",
        },
    ]
    assert purchase["tax"]["value"] == 1.82


@pytest.mark.parametrize(
    ("address", "tax"),
    [
        (CA, D("1.82")),
        (NY, D("0.40") + D("0.30") + D("1.20")),
        (CA | {"stateOrProvince": "OR"}, D("1.05")),
    ],
)
def test_different_states_give_different_taxes(api, db_conn, address, tax):
    buy(api, db_conn, address)
    tick(db_conn, api.clock.now())
    assert charges(db_conn)[0][3] == tax


def test_an_account_without_address_keeps_the_flat_price_tax_when_the_gate_is_off(
    api, db_conn, monkeypatch
):
    monkeypatch.setattr(settings, "tax_require_service_address", False)
    buy(api, db_conn, None)
    tick(db_conn, api.clock.now())
    ((_, _, net, tax, lines, _),) = charges(db_conn)
    assert (net, tax) == (D("10.00"), D("2.00"))
    assert [t["taxCategory"] for t in lines] == ["VAT"] and "jurisdiction" not in lines[0]
    assert db_conn.execute("SELECT count(*) FROM tax_transaction").fetchone() == (0,)


def test_a_line_cannot_be_activated_without_a_service_address(api, db_conn):
    off, acc, order = buy(api, db_conn, None)
    assert order["state"] == "rejected"
    assert order["productOrderErrorMessage"][0]["code"] == "serviceAddressRequired"
    assert db_conn.execute("SELECT count(*) FROM product").fetchone() == (0,)
    assert set_address(api, acc, CA).status_code == 200
    assert apidata.order(api, acc, apidata.add_item(off))["state"] == "inProgress"


@pytest.mark.parametrize(
    "bad",
    [
        {**CA, "postCode": "9410"},
        {**CA, "postCode": "94105-12"},
        {**CA, "stateOrProvince": "California"},
        {**CA, "country": "CA"},
        {**CA, "city": " "},
        {k: v for k, v in CA.items() if k != "street1"},
    ],
)
def test_the_service_address_is_validated(api, db_conn, bad):
    _, acc = shop(api, db_conn, numbers=False)
    response = api.patch(f"{ACCOUNT}/{acc['id']}", {"contact": contact({"country": "US"} | bad)})
    assert response.status_code == 422, response.text


def test_the_service_address_is_stored_and_removed_with_the_contact(api, db_conn):
    _, acc = shop(api, db_conn, numbers=False)
    assert set_address(api, acc, NY).status_code == 200
    stored = db_conn.execute("SELECT service_address FROM billing_account").fetchone()[0]
    assert stored == NY | {"country": "US"}
    other = contact(CA, "billing")  # another kind of contact is no service address
    assert api.patch(f"{ACCOUNT}/{acc['id']}", {"contact": other}).status_code == 200
    assert db_conn.execute("SELECT service_address FROM billing_account").fetchone() == (None,)


def test_a_later_address_change_does_not_alter_the_booked_charge(api, db_conn):
    _, acc, _ = buy(api, db_conn, CA)
    tick(db_conn, api.clock.now())
    assert set_address(api, acc, NY).status_code == 200
    assert charges(db_conn)[0][3] == D("1.82")
    assert db_conn.execute(
        "SELECT address->>'stateOrProvince' FROM tax_transaction"
    ).fetchone() == ("CA",)


def test_a_void_reverses_the_taxes_of_a_credited_charge(api, db_conn, engine):
    off, acc, order = buy(api, db_conn, CA)
    tick(db_conn, api.clock.now())
    product = apidata.product_of(api, api.get(order["href"]).json())
    # ended on its first day: the whole interval is credited
    terminate(api, acc, product)
    tick(db_conn, api.clock.now())
    rows = charges(db_conn)
    assert [r[1] for r in rows] == ["recurring", "refund"]
    refund = rows[1]
    assert refund[2] == D("-10.00") and refund[3] == D("-1.82")
    assert [D(str(t["taxAmount"])) for t in refund[4]] == [D("-0.72"), D("-0.30"), D("-0.80")]
    assert db_conn.execute("SELECT state FROM tax_transaction").fetchone() == ("voided",)
    assert list(engine.voided) == list(engine.committed)


def test_engine_failure_at_acceptance_holds_and_books_nothing(api, db_conn):
    off, acc = shop(api, db_conn, numbers=False)
    assert set_address(api, acc, CA | {"postCode": FAILING_ZIP}).status_code == 200
    response = api.post(ORDER, apidata.order_body(acc, apidata.add_item(off)))
    assert response.status_code == 503, response.text
    assert db_conn.execute("SELECT count(*) FROM product_order").fetchone() == (0,)
    assert db_conn.execute("SELECT count(*) FROM funding_hold").fetchone() == (0,)


@pytest.mark.capability("worker.taxEngine")
def test_engine_failure_at_renewal_is_retried_and_nothing_is_charged_without_tax(api, db_conn):
    off, acc, order = buy(api, db_conn, CA)
    tick(db_conn, api.clock.now())
    (due,) = db_conn.execute("SELECT upper(period) FROM funded_interval").fetchone()
    assert set_address(api, acc, CA | {"postCode": FAILING_ZIP}).status_code == 200
    at_due = datetime.combine(due, time(0, 5), TZ)
    tick(db_conn, at_due)
    assert len(charges(db_conn)) == 1  # the renewal was not booked, without or with tax
    assert db_conn.execute(
        "SELECT state, attempts FROM prepaid_command WHERE kind = 'renewal'"
    ).fetchone() == ("failedTechnical", 1)
    assert set_address(api, acc, NY).status_code == 200  # the engine can answer again
    tick(db_conn, at_due + timedelta(minutes=2))
    rows = charges(db_conn)
    assert len(rows) == 2 and rows[1][3] == D("1.90")
    assert db_conn.execute("SELECT count(*) FROM tax_transaction").fetchone() == (2,)


def test_the_stub_is_deterministic_and_fails_on_its_zip():
    stub = adapter()
    line = [ChargeLine(D("10.00"), "plan", "recurring")]
    first = stub.quote(line, CA, None).lines
    assert first == stub.quote(line, CA, None).lines
    zero = stub.quote([ChargeLine(D("0.00"), "plan", "recurring")], CA, None).lines[0]
    assert sum(t.amount for t in zero) == D("0.00")  # no 911 fee on a free charge
    with pytest.raises(TaxUnavailable):
        stub.quote(line, CA | {"postCode": f"{FAILING_ZIP}-1234"}, None)


def test_an_unknown_tax_adapter_fails_startup(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(BSS_TAX_ADAPTER="avalara")
    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "tax_adapter", "avalara")
        with pytest.raises(ValueError, match="unknown BSS_TAX_ADAPTER"):
            tax_package.adapter()
