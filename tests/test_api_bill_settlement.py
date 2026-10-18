"""Bill lifecycle slice 3: the API (docs/bill-lifecycle-design.md 6.5).

- TMF676 POST /payment settles the account at once under its row lock: explicit
  paymentItem[] first, the rest oldest bill first; paymentItem in responses grows when
  the payment's credit later pays new bills.
- TMF678 customerBill carries remainingAmount, paymentDueDate, appliedPayment, billNo,
  runType, category; PATCH holds and releases (strict release).
- Internal: /billingAccount/{id}/settlement and /customerBill/{id}.

Bills are written by real runs (bill_account); every response is contract-checked."""

import threading
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from app.billing.adapter import bill_account
from tests import apidata
from tests.apiclient import decimals
from tests.apidata import ACCOUNT, BILL, PAYMENT, RATE
from tests.test_billing_db import add, at, bill, delete, run_row

D = Decimal
INTERNAL = "/internal/v1"


@pytest.fixture
def offering(api):
    return apidata.sellable_offering(api, value=300)


def account_with_bills(api, db_conn, offering, *days: date) -> dict:
    """An account with one 300.00 product from Dec 1 and the bills of `days` issued."""
    account = apidata.account(api)
    add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    for day in days:
        bill(db_conn, account, day)
    return account


def bills(api, account: dict) -> list[dict]:
    return decimals(api.get(f"{BILL}?billingAccount.id={account['id']}"))


def pay(api, account: dict, value, day: date, **over):
    api.clock.set(at(day, hour=12))
    return api.post(PAYMENT, apidata.payment_body(account, value, **over))


def item(bill_id: str, value, **over) -> dict:
    return {
        "item": {"id": bill_id, "@referredType": "CustomerBill"},
        "amount": {"unit": "RUB", "value": value},
        **over,
    }


def patch_state(api, bill_id: str, state: str, **headers):
    return api.request(
        "PATCH",
        f"{BILL}/{bill_id}",
        content=f'{{"state": "{state}"}}',
        headers={"Content-Type": "application/merge-patch+json", **headers},
    )


def summary(api, account: dict) -> dict:
    return decimals(api.get(f"{INTERNAL}/billingAccount/{account['id']}/settlement"))


def check_invariant(api, account: dict) -> None:
    """outstanding - unallocatedCredit = -(balance + charges not billed yet), via the API."""
    s = summary(api, account)
    (balance,) = decimals(api.get(f"{ACCOUNT}/{account['id']}"))["accountBalance"]
    unbilled = sum(
        (
            r["taxExcludedAmount"]["value"]
            for r in decimals(api.get(f"{RATE}?billingAccount.id={account['id']}&isBilled=false"))
        ),
        D("0"),
    )
    assert s["outstanding"]["value"] - s["unallocatedCredit"]["value"] == -(
        balance["amount"]["value"] + unbilled
    )


# --- TMF678: the bill view ---


def test_a_bill_shows_its_due_date_and_number(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    assert b["state"] == "sent"
    assert b["paymentDueDate"] == "2026-12-30T21:00:00Z"  # Dec 31, midnight in Moscow
    assert b["remainingAmount"] == {"unit": "RUB", "value": D("300.00")}
    assert b["appliedPayment"] == []
    assert (b["category"], b["runType"]) == ("normal", "onCycle")
    assert b["billNo"] == "BL-" + b["id"].replace("-", "")[:8].upper()


# --- TMF676: a payment settles at once ---


def test_a_payment_settles_at_once_and_its_credit_pays_later_bills(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [dec] = bills(api, account)

    first = pay(api, account, 100, date(2026, 12, 5))
    assert first.status_code == 201
    assert decimals(first)["paymentItem"] == [
        {
            "item": {"id": dec["id"], "href": dec["href"], "@referredType": "CustomerBill"},
            "amount": {"unit": "RUB", "value": D("100.00")},
            "totalAmount": {"unit": "RUB", "value": D("100.00")},
            "@type": "PaymentItem",
        }
    ]
    [dec] = bills(api, account)
    assert (dec["state"], dec["remainingAmount"]["value"]) == ("partiallyPaid", D("200.00"))
    assert dec["appliedPayment"] == [
        {
            "appliedAmount": {"unit": "RUB", "value": D("100.00")},
            "payment": {
                "id": first.json()["id"],
                "href": first.json()["href"],
                "@type": "PaymentRef",
                "@referredType": "Payment",
            },
        }
    ]
    check_invariant(api, account)

    second = decimals(pay(api, account, 250, date(2026, 12, 6)))
    assert [i["amount"]["value"] for i in second["paymentItem"]] == [D("200.00")]
    assert bills(api, account)[0]["state"] == "settled"
    assert summary(api, account)["unallocatedCredit"]["value"] == D("50.00")
    check_invariant(api, account)

    bill(db_conn, account, date(2027, 1, 1))  # the January bill takes the 50.00 left
    jan = bills(api, account)[1]
    assert (jan["state"], jan["remainingAmount"]["value"]) == ("partiallyPaid", D("250.00"))
    grown = decimals(api.get(second["href"]))["paymentItem"]
    assert [(i["item"]["id"], i["amount"]["value"]) for i in grown] == [
        (dec["id"], D("200.00")),
        (jan["id"], D("50.00")),
    ]
    check_invariant(api, account)


def test_a_payment_before_any_bill_is_credit_for_the_first(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering)
    created = decimals(pay(api, account, 500, date(2026, 11, 25)))
    assert "paymentItem" not in created  # nothing to pay yet
    assert summary(api, account)["unallocatedCredit"]["value"] == D("500.00")
    bill(db_conn, account, date(2026, 12, 1))
    assert bills(api, account)[0]["state"] == "settled"
    assert summary(api, account)["unallocatedCredit"]["value"] == D("200.00")
    check_invariant(api, account)


# --- TMF676: explicit lettering ---


def test_explicit_items_pay_the_named_bill_first(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1), date(2027, 1, 1))
    dec, jan = bills(api, account)
    created = pay(api, account, 400, date(2027, 1, 2), paymentItem=[item(jan["id"], 300)])
    assert created.status_code == 201, created.text
    dec, jan = bills(api, account)
    assert (jan["state"], dec["state"]) == ("settled", "partiallyPaid")
    assert dec["remainingAmount"]["value"] == D("200.00")  # the 100.00 not lettered
    detail = decimals(api.get(f"{INTERNAL}/customerBill/{jan['id']}"))
    assert [(s["kind"], s["allocation"], s["amount"]["value"]) for s in detail["sources"]] == [
        ("payment", "explicit", D("300.00"))
    ]
    check_invariant(api, account)


@pytest.mark.parametrize(
    ("items", "status"),
    [
        (lambda b, other: [item(apidata.NIL, 10)], 422),  # not a bill
        (lambda b, other: [item(other, 10)], 422),  # another account's bill
        (lambda b, other: [item(b, 10), item(b, 10)], 422),  # the same bill twice
        (lambda b, other: [item(b, 300.01)], 409),  # more than it owes
        (lambda b, other: [item(b, 0)], 422),
        (lambda b, other: [item(b, 10.001)], 422),
        (lambda b, other: [item(b, 400)], 409),  # more than it owes, and than the payment
        (lambda b, other: [{**item(b, 10), "amount": {"unit": "EUR", "value": 10}}], 422),
        (lambda b, other: [{**item(b, 10), "item": {"id": b, "@referredType": "Product"}}], 422),
        (lambda b, other: [{**item(b, 10), "taxAmount": {"unit": "RUB", "value": 1}}], 422),
        (lambda b, other: [{**item(b, 10), "colour": "red"}], 400),
    ],
)
def test_refused_items_store_nothing(api, db_conn, offering, items, status):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    other = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    [theirs] = bills(api, other)
    response = pay(api, account, 350, date(2026, 12, 5), paymentItem=items(b["id"], theirs["id"]))
    assert response.status_code == status, response.text
    assert api.get(f"{PAYMENT}?account.id={account['id']}").json() == []
    assert bills(api, account)[0]["state"] == "sent"


def test_items_above_the_payment_are_a_422(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1), date(2027, 1, 1))
    dec, jan = bills(api, account)
    response = pay(
        api, account, 100, date(2027, 1, 2), paymentItem=[item(dec["id"], 60), item(jan["id"], 60)]
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("state", ["onHold", "settled"])
def test_items_to_a_bill_that_takes_no_payment_are_a_409(api, db_conn, offering, state):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    if state == "onHold":
        assert patch_state(api, b["id"], "onHold").status_code == 200
    else:
        assert pay(api, account, 300, date(2026, 12, 2)).status_code == 201
    response = pay(api, account, 10, date(2026, 12, 5), paymentItem=[item(b["id"], 10)])
    assert response.status_code == 409, response.text


# --- TMF678: hold and release ---


def test_hold_and_release(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    api.clock.set(at(date(2026, 12, 3)))
    held = patch_state(api, b["id"], "onHold", **{"X-Remote-User": "i.petrova"})
    assert held.status_code == 200, held.text
    assert held.json()["state"] == "onHold"
    assert patch_state(api, b["id"], "onHold").status_code == 200  # a repeat: no change

    # a payment while it is held waits as credit
    pay(api, account, 120, date(2026, 12, 4))
    assert decimals(api.get(b["href"]))["remainingAmount"]["value"] == D("300.00")
    assert summary(api, account)["onHold"] == {
        "count": 1,
        "amount": {"unit": "RUB", "value": D("300.00")},
    }
    check_invariant(api, account)

    # release names the state its allocations imply (nothing paid: sent) ...
    refused = patch_state(api, b["id"], "partiallyPaid")
    assert refused.status_code == 409 and "released as sent" in refused.json()["message"]
    assert patch_state(api, b["id"], "settled").status_code == 409
    released = decimals(patch_state(api, b["id"], "sent"))
    # ... and then the waiting credit pays it
    assert (released["state"], released["remainingAmount"]["value"]) == (
        "partiallyPaid",
        D("180.00"),
    )
    detail = api.get(f"{INTERNAL}/customerBill/{b['id']}").json()
    assert [
        (h["fromState"], h["toState"], h["cause"], h["operator"]) for h in detail["history"]
    ] == [
        (None, "new", "run", None),
        ("new", "validated", "run", None),
        ("validated", "sent", "run", None),
        ("sent", "onHold", "operator", "i.petrova"),
        ("onHold", "sent", "operator", None),
        ("sent", "partiallyPaid", "payment", None),
    ]
    assert detail["allowedStates"] == ["onHold"]
    check_invariant(api, account)


def test_a_held_partly_paid_bill_is_released_as_partially_paid(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    pay(api, account, 100, date(2026, 12, 2))
    assert patch_state(api, b["id"], "onHold").status_code == 200
    detail = api.get(f"{INTERNAL}/customerBill/{b['id']}").json()
    assert detail["allowedStates"] == ["partiallyPaid"]
    assert patch_state(api, b["id"], "sent").status_code == 409
    assert patch_state(api, b["id"], "partiallyPaid").json()["state"] == "partiallyPaid"


@pytest.mark.parametrize(
    ("body", "content_type", "status"),
    [
        ('{"state": "validated"}', "application/merge-patch+json", 409),
        ('{"state": "unknown"}', "application/merge-patch+json", 422),
        # the run's attributes: repeated, never changed (422 for another value)
        ('{"amountDue": {"unit": "RUB", "value": 1}}', "application/merge-patch+json", 422),
        ('{"billCycle": {"id": "x"}}', "application/merge-patch+json", 422),
        ('{"state": "onHold", "colour": 1}', "application/merge-patch+json", 400),
        ('{"state": "onHold", "@type": "Product"}', "application/merge-patch+json", 400),
        ('{"state": "onHold"}', "text/plain", 415),
        ('{"state": null}', "application/merge-patch+json", 422),
        ("{}", "application/merge-patch+json", 200),  # an empty merge patch changes nothing
    ],
)
def test_patch_takes_the_state_only(api, db_conn, offering, body, content_type, status):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    response = api.request(
        "PATCH", f"{BILL}/{b['id']}", content=body, headers={"Content-Type": content_type}
    )
    assert response.status_code == status, response.text
    assert bills(api, account)[0]["state"] == "sent"


def test_a_credit_note_cannot_be_held(api, db_conn, offering):
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))
    delete(api, account, product, date(2026, 12, 20), on=at(date(2026, 12, 19)))
    bill(db_conn, account, date(2026, 12, 20))
    bill(db_conn, account, date(2027, 1, 1))
    note = bills(api, account)[1]
    assert (note["category"], note["state"]) == ("creditNote", "settled")
    assert patch_state(api, note["id"], "onHold").status_code == 409
    assert api.get(f"{BILL}/{apidata.NIL}").status_code == 404
    assert patch_state(api, apidata.NIL, "onHold").status_code == 404


def test_abrs_by_bill(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    rates = api.get(f"{RATE}?bill.id={b['id']}").json()
    assert [r["bill"]["id"] for r in rates] == [b["id"]]


# --- internal ---


def test_the_bill_dialog(api, db_conn, offering):
    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    [b] = bills(api, account)
    payment = pay(api, account, 50, date(2026, 12, 2)).json()
    detail = decimals(api.get(f"{INTERNAL}/customerBill/{b['id']}"))
    assert detail["billNo"] == b["billNo"]
    assert (detail["billDate"], detail["paymentDueDate"]) == ("2026-12-01", "2026-12-31")
    assert detail["period"] == {"start": "2026-12-01", "end": "2027-01-01"}
    assert [(c["kind"], c["amount"]["value"]) for c in detail["charges"]] == [
        ("charge", D("300.00"))
    ]
    assert [(s["id"], s["label"], s["allocation"]) for s in detail["sources"]] == [
        (payment["id"], "bankTransfer", "automatic")
    ]
    assert detail["overdue"] is False
    api.clock.set(at(date(2027, 1, 1)))  # due Dec 31: overdue from Jan 1
    assert api.get(f"{INTERNAL}/customerBill/{b['id']}").json()["overdue"] is True
    assert summary(api, account)["overdue"] == {
        "count": 1,
        "amount": {"unit": "RUB", "value": D("250.00")},
    }
    assert api.get(f"{INTERNAL}/customerBill/{b['id']}?x=1").status_code == 400
    assert api.get(f"{INTERNAL}/customerBill/{apidata.NIL}").status_code == 404
    assert api.get(f"{INTERNAL}/billingAccount/{apidata.NIL}/settlement").status_code == 404


def test_an_account_without_bills(api):
    account = apidata.account(api)
    assert summary(api, account) == {
        "outstanding": {"unit": "RUB", "value": 0},
        "unallocatedCredit": {"unit": "RUB", "value": 0},
        "overdue": {"count": 0, "amount": {"unit": "RUB", "value": 0}},
        "onHold": {"count": 0, "amount": {"unit": "RUB", "value": 0}},
        "paymentTermDays": 30,
    }


# --- the lock: a payment and a run never miss each other ---


def test_a_payment_during_a_run_waits_and_then_pays_the_new_bill(
    api, db_conn, offering, migrated_db_url
):
    """The run holds the account's row lock while it writes and settles the Dec 1 bill;
    a payment posted meanwhile waits for it, then sees the bill and pays it. (The payment's
    foreign key alone already waits on the run's FOR UPDATE - removing the API's own lock
    does not fail this test; test_two_payments_settle_one_after_the_other is the one that
    needs it.)"""
    account = account_with_bills(api, db_conn, offering)
    run = run_row(db_conn, date(2026, 12, 1))
    db_conn.commit()
    api.clock.set(at(date(2026, 12, 1)))
    outcome: dict = {}

    def post_payment() -> None:
        outcome["response"] = api.post(PAYMENT, apidata.payment_body(account, 300))

    first = psycopg.connect(migrated_db_url)
    try:
        with first.transaction():
            bill_account(first, UUID(account["id"]), run)
            thread = threading.Thread(target=post_payment)
            thread.start()
            thread.join(timeout=2)
            assert thread.is_alive(), "the payment did not wait for the run"
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        first.close()
    assert outcome["response"].status_code == 201
    assert bills(api, account)[0]["state"] == "settled"
    check_invariant(api, account)


def test_two_payments_settle_one_after_the_other(api, db_conn, offering, monkeypatch):
    """Two POST /payment on one account at once. The payment's foreign key only takes a
    key-share lock on the account row, which two payments share: without the explicit
    FOR UPDATE both would read the same open bill and allocate the same remaining amount
    (the bounds trigger then fails one with a 500). The first request is paused inside
    the settlement - after it read the bills, before it wrote - while the second runs."""
    from app.billing import settlement

    account = account_with_bills(api, db_conn, offering, date(2026, 12, 1))
    api.clock.set(at(date(2026, 12, 2)))
    real_settle = settlement.settle
    second_settled = threading.Event()
    calls: list[int] = []
    overlapped: list[bool] = []

    def paused_settle(*args, **kwargs):
        calls.append(len(calls))
        if len(calls) == 1:
            # with the lock the second request cannot get here before this one commits
            overlapped.append(second_settled.wait(timeout=2))
        else:
            second_settled.set()
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(settlement, "settle", paused_settle)
    responses: dict[str, object] = {}

    def post_payment(name: str) -> None:
        responses[name] = api.post(PAYMENT, apidata.payment_body(account, 200))

    first = threading.Thread(target=post_payment, args=("first",))
    second = threading.Thread(target=post_payment, args=("second",))
    first.start()
    while not calls:  # the first request is inside the settlement, holding the lock
        threading.Event().wait(0.01)
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert len(calls) == 2
    assert overlapped == [False], "the second payment settled while the first was open"
    assert [responses[n].status_code for n in ("first", "second")] == [201, 201]
    [b] = bills(api, account)
    assert (b["state"], b["remainingAmount"]["value"]) == ("settled", D("0.00"))
    assert summary(api, account)["unallocatedCredit"]["value"] == D("100.00")
    check_invariant(api, account)
