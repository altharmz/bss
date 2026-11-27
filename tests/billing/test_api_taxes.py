"""Taxes, gap-analysis slice 7 (docs/decisions/billing-core.md#taxes-2026-10-18).

A price's TMF620 `tax[]` is what billing applies: each ABR carries an appliedTax line per
tax item (rate in percent, 0 where the account's taxExemption covers the tax type), a
bill's taxItem[] sums them per category and rate, and amountDue includes them. Bills are
written by real runs (bill_account); every response is contract-checked."""

import base64
import json
from datetime import date
from decimal import Decimal

import pytest

from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, BILL, CATALOG, ORDER, PAYMENT, PRODUCT, RATE, tax_item
from tests.builders.billing.billing_db import add, at, bill, delete

D = Decimal
PRICE = f"{CATALOG}/productOfferingPrice"
REFUND = "/tmf-api/payment/v4/refund"
INTERNAL = "/internal/v1/billingAccount"


def usd(value) -> dict:
    """Money as a response holds it (read with `decimals`)."""
    return {"unit": "USD", "value": D(value)}


def send(value) -> dict:
    """Money for a request body: a JSON number (the client cannot encode a Decimal)."""
    number = D(value)
    return {"unit": "USD", "value": int(number) if number == int(number) else float(number)}


def vat(rate, amount) -> dict:
    return {"taxCategory": "VAT", "taxRate": D(rate), "taxAmount": usd(amount)}


def without_type(lines: list[dict]) -> list[dict]:
    return [{k: v for k, v in line.items() if k != "@type"} for line in lines]


@pytest.fixture
def taxed(api):
    """300.00 a month plus 20% VAT."""
    return apidata.sellable_offering(api, value=300, tax=[tax_item("VAT", 20)])


def account_billed(api, db_conn, offering, **over) -> dict:
    """An account with one product from Dec 1 (ordered Nov 20) and the bill of Dec 1."""
    account = apidata.account(api, **over)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    return account


def rates(api, account) -> list[dict]:
    return decimals(api.get(f"{RATE}?billingAccount.id={account['id']}"))


def bills(api, account) -> list[dict]:
    return decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))


# --- TMF678: ABRs and bills ---


def test_a_charge_carries_its_tax_lines_and_the_bill_is_due_with_them(api, db_conn, taxed):
    account = account_billed(api, db_conn, taxed)
    [rate] = rates(api, account)
    assert rate["taxExcludedAmount"] == usd("300.00")
    assert rate["taxIncludedAmount"] == usd("360.00")
    assert rate["appliedTax"] == [{**vat(20, "60.00"), "@type": "AppliedBillingTaxRate"}]
    [b] = bills(api, account)
    assert (b["taxExcludedAmount"], b["taxIncludedAmount"], b["amountDue"]) == (
        usd("300.00"),
        usd("360.00"),
        usd("360.00"),
    )
    assert b["taxItem"] == [{**vat(20, "60.00"), "@type": "TaxItem"}]
    assert b["remainingAmount"] == usd("360.00")
    fields = decimals(api.get(f"{BILL}/{b['id']}?fields=taxItem"))
    assert fields["taxItem"] == b["taxItem"]
    picked = decimals(api.get(f"{RATE}/{rate['id']}?fields=appliedTax"))
    assert picked["appliedTax"] == rate["appliedTax"]
    document = json.loads(base64.b64decode(b["billDocument"][0]["content"]), parse_float=D)
    assert document["taxExcludedAmount"] == usd("300.00")
    assert document["taxItem"] == [vat(20, "60.00")]
    assert document["charges"][0]["taxIncludedAmount"] == usd("360.00")


def test_the_tax_included_amount_settles_the_bill_and_the_balance(api, db_conn, taxed):
    account = account_billed(api, db_conn, taxed)
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    assert balance["amount"] == usd("-360.00")
    api.clock.set(at(date(2026, 12, 5)))
    paid = api.post(PAYMENT, apidata.payment_body(account, 360, amount=send(300)))
    assert paid.status_code == 201, paid.text
    [b] = bills(api, account)
    assert (b["state"], b["remainingAmount"]) == ("settled", usd("0.00"))
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    assert balance["amount"] == usd("0.00")
    [charge] = decimals(api.get(f"{INTERNAL}/{account['id']}/charges"))
    assert charge["amount"] == usd("360.00")  # with its taxes
    ledger = decimals(api.get(f"{INTERNAL}/{account['id']}/transactions"))
    assert [t["balanceAfter"] for t in ledger] == [usd("0.00"), usd("-360.00")]


def test_each_tax_item_is_a_line_and_the_bill_sums_them_per_category(api, db_conn):
    offering = apidata.sellable_offering(
        api, value=99.99, tax=[tax_item("VAT", 20), tax_item("city", 1.5)]
    )
    account = apidata.account(api)
    for _ in range(2):
        add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    for rate in rates(api, account):
        # 99.99 x 20% = 19.998 -> 20.00; x 1.5% = 1.49985 -> 1.50
        assert without_type(rate["appliedTax"]) == [
            vat(20, "20.00"),
            {"taxCategory": "city", "taxRate": D("1.5"), "taxAmount": usd("1.50")},
        ]
    [b] = bills(api, account)
    assert without_type(b["taxItem"]) == [
        {"taxCategory": "VAT", "taxRate": D(20), "taxAmount": usd("40.00")},
        {"taxCategory": "city", "taxRate": D("1.5"), "taxAmount": usd("3.00")},
    ]
    assert b["amountDue"] == usd(D("199.98") + D("43.00"))


def test_a_refund_takes_its_tax_back(api, db_conn, taxed):
    account = account_billed(api, db_conn, taxed)
    product = decimals(api.get(f"{PRODUCT}?billingAccount.id={account['id']}"))[0]
    delete(api, account, product, date(2026, 12, 16), on=at(date(2026, 12, 10)))
    bill(db_conn, account, date(2026, 12, 16))
    [refund] = [
        r for r in rates(api, account) if r["appliedBillingRateType"] == "appliedBillingCredit"
    ]
    # expected 300 x 15/31 = 145.16, its tax 29.03; refund 145.16 - 300, 29.03 - 60
    assert refund["taxExcludedAmount"] == usd("-154.84")
    assert without_type(refund["appliedTax"]) == [vat(20, "-30.97")]
    assert refund["taxIncludedAmount"] == usd("-185.81")


def test_an_exempt_account_gets_the_line_at_rate_zero(api, db_conn, taxed):
    certificate = {
        "@type": "TaxExemptionCertificate",
        "certificateNumber": "45678889876",
        "reason": "Embassy",
        "taxDefinition": [{"@type": "TaxDefinition", "name": "Value Added Tax", "taxType": "VAT"}],
    }
    account = account_billed(api, db_conn, taxed, taxExemption=[certificate])
    [rate] = rates(api, account)
    assert without_type(rate["appliedTax"]) == [vat(0, "0.00")]
    [b] = bills(api, account)
    assert b["amountDue"] == usd("300.00")
    assert without_type(b["taxItem"]) == [vat(0, "0.00")]


def test_an_exemption_needs_a_matching_type_and_a_current_period(api, db_conn, taxed):
    other = {
        "@type": "TaxExemptionCertificate",
        "taxDefinition": [{"@type": "TaxDefinition", "taxType": "city"}],
    }
    later = {
        "@type": "TaxExemptionCertificate",
        "validFor": {"startDateTime": "2027-01-01T00:00:00+03:00"},
        "taxDefinition": [{"@type": "TaxDefinition", "taxType": "VAT"}],
    }
    untyped = {
        "@type": "TaxExemptionCertificate",
        "taxDefinition": [{"@type": "TaxDefinition", "name": "VAT"}],
    }
    account = account_billed(api, db_conn, taxed, taxExemption=[other, later, untyped])
    [rate] = rates(api, account)
    assert without_type(rate["appliedTax"]) == [vat(20, "60.00")]
    bill(db_conn, account, date(2027, 1, 1))
    january = [r for r in rates(api, account) if r["periodCoverage"]["startDateTime"] > "2026-12"]
    assert [without_type(r["appliedTax"]) for r in january] == [[vat(0, "0.00")]]


def test_an_untaxed_price_bills_as_before(api, db_conn):
    account = account_billed(api, db_conn, apidata.sellable_offering(api, value=300))
    [rate] = rates(api, account)
    assert (rate["appliedTax"], rate["taxIncludedAmount"]) == ([], usd("300.00"))
    [b] = bills(api, account)
    assert (b["taxItem"], b["taxExcludedAmount"], b["amountDue"]) == (
        [],
        usd("300.00"),
        usd("300.00"),
    )


# --- TMF622/637: price views ---


def test_order_and_product_prices_show_the_rate_and_the_tax_included_amount(api, taxed):
    account = apidata.account(api)
    product = add(api, account, taxed, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    [price] = decimals(api.get(product["href"]))["productPrice"]
    assert price["price"]["dutyFreeAmount"] == usd("300.00")
    assert price["price"]["taxIncludedAmount"] == usd("360.00")
    assert price["price"]["taxRate"] == 20
    [order] = decimals(api.get(f"{ORDER}?billingAccount.id={account['id']}"))
    [item_price] = order["productOrderItem"][0]["itemPrice"]
    assert item_price["price"]["taxIncludedAmount"] == usd("360.00")
    [total] = order["orderTotalPrice"]
    assert (total["price"]["taxRate"], total["price"]["taxIncludedAmount"]) == (20, usd("360.00"))


def test_a_total_over_different_rates_has_no_one_rate(api):
    account = apidata.account(api)
    plain = apidata.sellable_offering(api, value=100, name="Plain")
    taxed = apidata.sellable_offering(api, value=100, name="Taxed", tax=[tax_item("VAT", 20)])
    api.clock.set(at(date(2026, 11, 20)))
    placed = apidata.order(
        api,
        account,
        apidata.add_item(plain, "1"),
        apidata.add_item(taxed, "2"),
        requestedStartDate="2026-11-30T21:00:00Z",
    )
    [total] = decimals(api.get(f"{ORDER}/{placed['id']}"))["orderTotalPrice"]
    assert "taxRate" not in total["price"]
    assert total["price"]["taxIncludedAmount"] == usd("220.00")


# --- TMF620: the rules a price's tax follows ---


def price_body(**over) -> dict:
    return {
        "@type": "ProductOfferingPrice",
        "name": "Monthly",
        "lifecycleStatus": "Active",
        "priceType": "recurring",
        "recurringChargePeriodType": "monthly",
        "price": send(300),
        **over,
    }


@pytest.mark.parametrize(
    ("tax", "where"),
    [
        ([{"@type": "TaxItem", "taxRate": 20}], "tax.0.taxCategory"),
        ([{"@type": "TaxItem", "taxCategory": "VAT"}], "tax.0.taxRate"),
        ([tax_item("VAT", 120)], "tax.0.taxRate"),
        ([tax_item("VAT", -1)], "tax.0.taxRate"),
        ([tax_item("VAT", 20), tax_item("VAT", 10)], "tax.1.taxCategory"),
        ([tax_item("VAT", 20, taxAmount=send(59))], "tax.0.taxAmount"),
        ([tax_item("VAT", 20, taxAmount={"unit": "EUR", "value": 60})], "tax.0.taxAmount"),
    ],
)
def test_a_tax_billing_cannot_apply_is_a_422(api, tax, where):
    response = api.post(PRICE, price_body(tax=tax))
    assert response.status_code == 422, response.text
    assert where in response.json()["message"]
    assert api.get(PRICE).json() == []


def test_a_tax_amount_is_the_tax_of_a_full_month_and_is_kept(api):
    tax = [tax_item("VAT", 12.5, taxAmount=send("37.50"))]
    created = api.post(PRICE, price_body(tax=tax))
    assert created.status_code == 201, created.text
    assert decimals(api.get(created.json()["href"]))["tax"] == [
        {"@type": "TaxItem", "taxCategory": "VAT", "taxRate": D("12.5"), "taxAmount": usd("37.50")}
    ]
    # a new price must come with a matching tax amount
    changed = api.patch(created.json()["href"], {"price": send(400)})
    assert changed.status_code == 422 and "tax.0.taxAmount" in changed.json()["message"]


def test_an_inline_price_with_a_bad_tax_is_refused_with_its_path(api):
    spec = api.post(
        f"{CATALOG}/productSpecification",
        {"@type": "ProductSpecification", "name": "Fiber", "lifecycleStatus": "Active"},
    ).json()
    response = api.post(
        f"{CATALOG}/productOffering",
        {
            "@type": "ProductOffering",
            "name": "Fiber",
            "lifecycleStatus": "Active",
            "productSpecification": {"id": spec["id"], "@type": "ProductSpecificationRef"},
            "productOfferingPrice": [price_body(tax=[tax_item("VAT", 200)])],
        },
    )
    assert response.status_code == 422, response.text
    assert "productOfferingPrice.0.tax.0.taxRate" in response.json()["message"]


def test_the_tax_changes_until_the_offering_is_ordered(api, taxed):
    [ref] = taxed["productOfferingPrice"]
    href = f"{PRICE}/{ref['id']}"
    changed = api.patch(href, {"tax": [tax_item("VAT", 10)]})
    assert changed.status_code == 200, changed.text
    add(api, apidata.account(api), taxed, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    for tax in ([tax_item("VAT", 20)], None, [tax_item("VAT", 10), tax_item("city", 1)]):
        refused = api.patch(href, {"tax": tax})
        assert refused.status_code == 409, (tax, refused.text)
        assert "end its validFor" in refused.json()["message"]
    same = api.patch(href, {"tax": [tax_item("VAT", 10)], "name": "Monthly VAT"})
    assert same.status_code == 200, same.text  # the current tax repeated is no change


# --- TMF676: taxAmount on items and refunds ---


def test_a_payment_item_pays_its_amount_and_tax_amount(api, db_conn, taxed):
    account = account_billed(api, db_conn, taxed)
    [b] = bills(api, account)
    api.clock.set(at(date(2026, 12, 5)))
    item = {
        "item": {"id": b["id"], "@referredType": "CustomerBill"},
        "amount": send(100),
        "taxAmount": send(20),
        "totalAmount": send(120),
    }
    paid = api.post(PAYMENT, apidata.payment_body(account, 120, paymentItem=[item]))
    assert paid.status_code == 201, paid.text
    assert decimals(api.get(b["href"]))["remainingAmount"] == usd("240.00")
    # 230 + 20 is more than the 240 the bill still owes, though 230 alone is not
    over = {**item, "amount": send(230), "totalAmount": send(250)}
    refused = api.post(PAYMENT, apidata.payment_body(account, 250, paymentItem=[over]))
    assert refused.status_code == 409, refused.text


def test_a_refund_keeps_its_tax_amount(api, db_conn):
    account = apidata.account(api)
    api.clock.set(at(date(2026, 12, 1)))
    apidata.payment(api, account, 120)
    body = {
        "account": {"id": account["id"]},
        "totalAmount": send(60),
        "taxAmount": send(10),
        "paymentMethod": {"name": "cash payout", "@type": "Cash"},
    }
    created = api.post(REFUND, body)
    assert created.status_code == 201, created.text
    refund = decimals(api.get(f"{REFUND}/{created.json()['id']}"))
    assert (refund["amount"], refund["taxAmount"], refund["totalAmount"]) == (
        usd(50),
        usd(10),
        usd(60),
    )
    bad = api.post(REFUND, {**body, "amount": send(40)})
    assert bad.status_code == 422, bad.text
