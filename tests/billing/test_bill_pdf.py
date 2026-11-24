"""Bill PDFs and email delivery (#202, docs/decisions/bills.md "PDF documents and email
delivery"): the run renders a PDF for every bill it issues, serves it at the billDocument
url, and emails it to accounts with the `email` presentation medium, once per bill."""
# ruff: noqa: F811

import hashlib
import io
import json
import os
import time
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.billing import bill_pdf
from app.config import settings
from app.integrations.notify import adapter as sender
from app.main import app
from app.worker.notify import process_notifications
from tests import apidata
from tests.builders.billing.billing_completion import (  # noqa: F401
    FORMAT,
    MEDIA,
    demand,
    run,
    worker,
)
from tests.builders.billing.billing_db import add, at
from tests.timeline import NOW, TZ

DEC1, JAN1 = date(2026, 12, 1), date(2027, 1, 1)
EMAIL = "olga@example.org"


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


def text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def pdf_of(api, bill) -> bytes:
    [document] = [d for d in bill["billDocument"] if d["mimeType"] == "application/pdf"]
    assert document["url"].endswith(f"/customerBill/{bill['id']}/document.pdf")
    response = api.client.get(document["url"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert document["size"] == {"amount": len(response.content), "units": "bytes"}
    return response.content


def billed_account(api, *, media=None, fmt=None, name="Fiber 100", value=310, cust=None):
    """An account with one product from Dec 1; `media`/`fmt`: presentation preferences."""
    api.clock.set(NOW)  # prices take effect from now on
    offering = apidata.sellable_offering(api, value=value, name=name)
    account = apidata.account(api, cust)
    structure = {}
    if media:
        structure["presentationMedia"] = [{"id": m["id"]} for m in media]
    if fmt:
        structure["format"] = {"id": fmt["id"]}
    if structure:
        assert api.patch(account["href"], {"billStructure": structure}).status_code == 200
    add(api, account, offering, DEC1, on=at(date(2026, 11, 20)))
    return account


def bills_of(api, account):
    return api.get(f"{apidata.BILL}?billingAccount.id={account['id']}").json()


def email_contact(db_conn, account, email=EMAIL):
    db_conn.execute(
        "UPDATE customer SET details = details || %s::jsonb"
        " WHERE id = (SELECT customer_id FROM billing_account WHERE id = %s)",
        (
            json.dumps({"contactMedium": [{"@type": "EmailContactMedium", "emailAddress": email}]}),
            account["id"],
        ),
    )
    db_conn.commit()


@pytest.mark.capability("worker.billPdf")
def test_an_issued_bill_has_a_pdf_with_its_number_amount_and_charges(api, worker, db_conn):
    second = apidata.sellable_offering(api, value=45, name="Static IP")
    account = billed_account(api, name="Fiber 100 Ünicode")
    add(api, account, second, DEC1, on=at(date(2026, 11, 21)))
    pay = apidata.payment_body(account, value=100)
    api.clock.set(at(date(2026, 11, 25)))
    assert api.post("/tmf-api/payment/v4/payment", pay).status_code == 201
    run(worker, api, DEC1)
    [bill] = bills_of(api, account)
    assert [d["mimeType"] for d in bill["billDocument"]] == ["application/json", "application/pdf"]
    content = pdf_of(api, bill)
    assert content.startswith(b"%PDF")
    printed = text(content)
    assert bill["billNo"] in printed
    assert "Fiber 100 Ünicode" in printed and "Static IP" in printed  # every charge
    assert "355.00 USD" in printed  # the bill total
    assert "Amount due: 255.00 USD" in printed  # what is left after the payment
    assert "-100.00 USD" in printed  # the payment applied at issue
    assert "Dec 31, 2026" in printed  # the due date (30 days)
    row = db_conn.execute(
        "SELECT sha256, rendered_at FROM customer_bill_document WHERE customer_bill_id = %s",
        (bill["id"],),
    ).fetchone()
    db_conn.commit()
    assert row[0] == hashlib.sha256(content).hexdigest() and row[1] == at(DEC1)


@pytest.mark.capability("worker.billPdf")
def test_the_summary_format_prints_totals_without_charges(api, worker):
    fmt = api.post(FORMAT, {"@type": "BillFormat", "name": "JSON summary"}).json()
    summary = billed_account(api, fmt=fmt, name="Summary Plan")
    detailed = billed_account(api, name="Detailed Plan")
    run(worker, api, DEC1)
    short = text(pdf_of(api, bills_of(api, summary)[0]))
    full = text(pdf_of(api, bills_of(api, detailed)[0]))
    assert "Summary Plan" not in short and "Charges\n" not in short
    assert "310.00 USD" in short and "Totals" in short
    assert "Detailed Plan" in full and "Charge" in full


@pytest.mark.capability("worker.billPdf")
def test_the_pdf_is_not_rewritten_by_later_changes(api, worker, db_conn):
    account = billed_account(api)
    run(worker, api, DEC1)
    [bill] = bills_of(api, account)
    before = pdf_of(api, bill)
    assert api.patch(account["href"], {"name": "Renamed account"}).status_code == 200
    run(worker, api, JAN1)
    assert pdf_of(api, api.get(bill["href"]).json()) == before
    assert "Renamed account" in text(pdf_of(api, bills_of(api, account)[0]))  # the new bill
    with pytest.raises(Exception, match="immutable"):
        db_conn.execute("UPDATE customer_bill_document SET rendered_at = now()")
    db_conn.rollback()


@pytest.mark.capability("worker.billPdf")
def test_a_render_failure_fails_the_account_and_writes_nothing(api, worker, db_conn, monkeypatch):
    broken = billed_account(api)
    fine = billed_account(api)
    real = bill_pdf.render

    def render(bill):
        if str(bill.account_id) == broken["id"]:
            raise bill_pdf.RenderError("font missing")
        return real(bill)

    monkeypatch.setattr(bill_pdf, "render", render)
    run(worker, api, DEC1)
    assert bills_of(api, broken) == []
    assert len(bills_of(api, fine)) == 1
    status, error = db_conn.execute(
        "SELECT status, error FROM billing_run_account WHERE billing_account_id = %s",
        (broken["id"],),
    ).fetchone()
    db_conn.commit()
    assert status == "failed" and error.startswith("DocumentFailed")
    assert total_abrs(api, broken) == 0  # nothing of the account was written


def total_abrs(api, account):
    return len(api.get(f"{apidata.RATE}?billingAccount.id={account['id']}").json())


@pytest.mark.capability("worker.billPdf")
def test_an_off_cycle_bill_has_a_pdf(api, worker):
    account = billed_account(api)
    request = demand(api, account)
    from app.worker.queue import process_requests

    run(worker, api, date(2026, 12, 3))
    assert process_requests(worker, clock=api.clock, tz=TZ) == [UUID(request["id"])]
    bill = api.get(api.get(request["href"]).json()["customerBill"]["href"]).json()
    assert bill["runType"] == "offCycle"
    assert bill["billNo"] in text(pdf_of(api, bill))


def test_a_legacy_bill_and_an_unknown_bill_have_no_pdf(api, worker, db_conn):
    account = billed_account(api)
    run(worker, api, DEC1)
    [bill] = bills_of(api, account)
    # a bill issued before migration 0086 has no document row (the trigger guards deletes)
    db_conn.execute("ALTER TABLE customer_bill_document DISABLE TRIGGER USER")
    db_conn.execute("DELETE FROM customer_bill_document")
    db_conn.execute("ALTER TABLE customer_bill_document ENABLE TRIGGER USER")
    db_conn.commit()
    legacy = api.get(bill["href"]).json()
    assert [d["mimeType"] for d in legacy["billDocument"]] == ["application/json"]
    assert api.client.get(f"{bill['href']}/document.pdf").status_code == 404
    assert api.client.get(f"{apidata.BILL}/{uuid4()}/document.pdf").status_code == 404


def test_the_bill_dialog_reads_the_pdf_link_or_null(api, worker, db_conn):
    """#386: the internal bill detail carries the billDocument PDF's url and size, or null."""
    account = billed_account(api)
    run(worker, api, DEC1)
    [bill] = bills_of(api, account)
    [document] = [d for d in bill["billDocument"] if d["mimeType"] == "application/pdf"]
    detail = api.get(f"/internal/v1/customerBill/{bill['id']}").json()
    assert detail["pdf"] == {"url": document["url"], "size": document["size"]["amount"]}
    db_conn.execute("ALTER TABLE customer_bill_document DISABLE TRIGGER USER")
    db_conn.execute("DELETE FROM customer_bill_document")
    db_conn.execute("ALTER TABLE customer_bill_document ENABLE TRIGGER USER")
    db_conn.commit()
    assert api.get(f"/internal/v1/customerBill/{bill['id']}").json()["pdf"] is None


def test_the_pdf_needs_authentication(api, worker):
    account = billed_account(api)
    run(worker, api, DEC1)
    [bill] = bills_of(api, account)
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get(f"{bill['href']}/document.pdf").status_code == 401


# --- email delivery ---


def email_media(api, name="Email"):
    return api.post(MEDIA, {"name": name, "description": "Bill by email"}).json()


def bill_emails(log):
    return [s for s in log.sent if s.channel == "email" and s.subject.startswith("Your bill")]


@pytest.mark.capability("worker.billEmail")
def test_an_email_account_gets_its_bill_once_with_the_pdf(api, worker, db_conn, log_sender):
    emailed = billed_account(api, media=[email_media(api)])
    email_contact(db_conn, emailed)
    portal = api.post(MEDIA, {"name": "Portal"}).json()
    other = billed_account(api, media=[portal])
    email_contact(db_conn, other, "other@example.org")
    run(worker, api, DEC1)
    process_notifications(worker, clock=api.clock, tz=TZ)
    process_notifications(worker, clock=api.clock, tz=TZ)  # the worker runs twice
    [bill] = bills_of(api, emailed)
    [sent] = bill_emails(log_sender)
    assert sent.to == EMAIL and sent.subject == f"Your bill {bill['billNo']}"
    assert "310.00 USD" in sent.body and "Dec 31, 2026" in sent.body
    [(name, mime, content)] = sent.attachments
    assert (name, mime) == (f"{bill['billNo']}.pdf", "application/pdf")
    assert content == pdf_of(api, bill)
    messages = api.get(
        f"/tmf-api/communicationManagement/v4/communicationMessage?billingAccount.id={emailed['id']}"
    ).json()
    [message] = messages
    assert message["messageType"] == "Email" and message["state"] == "completed"
    assert message["attachment"][0]["url"].endswith(f"{bill['id']}/document.pdf")
    other_messages = api.get(
        f"/tmf-api/communicationManagement/v4/communicationMessage?billingAccount.id={other['id']}"
    ).json()
    assert other_messages == []  # no email medium: no bill email


@pytest.mark.capability("worker.billEmail")
def test_the_switch_off_sends_no_bill(api, worker, db_conn, log_sender, monkeypatch):
    account = billed_account(api, media=[email_media(api, "EMAIL")])  # the name, any case
    email_contact(db_conn, account)
    monkeypatch.setattr(settings, "bill_email_enabled", False)
    run(worker, api, DEC1)
    process_notifications(worker, clock=api.clock, tz=TZ)
    assert bill_emails(log_sender) == []
    monkeypatch.setattr(settings, "bill_email_enabled", True)
    process_notifications(worker, clock=api.clock, tz=TZ)
    assert len(bill_emails(log_sender)) == 1  # "EMAIL" counts as the email medium


@pytest.mark.capability("worker.billEmail")
def test_a_bill_email_without_an_address_is_cancelled_not_texted(api, worker, log_sender):
    account = billed_account(api, media=[email_media(api)])
    run(worker, api, DEC1)
    process_notifications(worker, clock=api.clock, tz=TZ)
    assert log_sender.sent == []
    [message] = api.get(
        f"/tmf-api/communicationManagement/v4/communicationMessage?billingAccount.id={account['id']}"
    ).json()
    assert message["messageType"] == "Email" and message["state"] == "cancelled"


@pytest.mark.capability("worker.billEmail")
def test_a_resent_bill_email_carries_the_pdf_again(api, worker, db_conn, log_sender):
    account = billed_account(api, media=[email_media(api)])
    email_contact(db_conn, account)
    run(worker, api, DEC1)
    process_notifications(worker, clock=api.clock, tz=TZ)
    [message] = api.get(
        f"/tmf-api/communicationManagement/v4/communicationMessage?billingAccount.id={account['id']}"
    ).json()
    response = api.post(f"/internal/v1/notification/message/{message['id']}/resend", {})
    assert response.status_code == 201, response.text
    process_notifications(worker, clock=api.clock, tz=TZ)
    first, again = bill_emails(log_sender)
    assert again.attachments == first.attachments and again.key != first.key


@pytest.mark.capability("worker.billEmail")
def test_the_emails_of_one_bill_are_listed_by_its_attachment(api, worker, db_conn, log_sender):
    """#386: the bill dialog's delivery line lists the emails that carried the bill's PDF
    (`attachment.id` = `{bill id}-pdf`), its resend too; another bill's id lists none."""
    account = billed_account(api, media=[email_media(api)])
    email_contact(db_conn, account)
    run(worker, api, DEC1)
    process_notifications(worker, clock=api.clock, tz=TZ)
    [bill] = bills_of(api, account)
    url = "/tmf-api/communicationManagement/v4/communicationMessage"
    [first] = api.get(f"{url}?billingAccount.id={account['id']}").json()
    again = api.post(f"/internal/v1/notification/message/{first['id']}/resend", {}).json()
    query = f"{url}?billingAccount.id={account['id']}&attachment.id={bill['id']}-pdf"
    both = {again["id"], first["id"]}  # one instant under the test clock: no order to pin
    assert {m["id"] for m in api.get(query).json()} == both
    assert {m["id"] for m in api.get(f"{url}?attachment.id={bill['id']}-pdf").json()} == both
    assert api.get(f"{url}?attachment.id={uuid4()}-pdf").json() == []
    for bad in (bill["id"], f"{bill['id']}-json", "x-pdf"):
        assert api.get(f"{url}?attachment.id={bad}").status_code == 400


# --- rendering cost ---


def test_rendering_takes_under_50_ms_a_bill():
    """The issue's target, measured on 1000 renders of a detailed bill (#202): 13 ms a bill
    alone. Under the parallel suite (`-n auto`, the hook) the cores are shared and the same
    renders took 79 ms on the wall clock and 154 ms of CPU, so there only a regression by an
    order of magnitude fails; run it alone for the 50 ms target:
    `pytest tests/billing/test_bill_pdf.py::test_rendering_takes_under_50_ms_a_bill`."""
    bill = bill_pdf.BillPdf(
        bill_id=uuid4(),
        issuer_name="BSS Mobile",
        issuer_address=["1 Main St", "Austin, TX 78701"],
        bill_date=DEC1,
        period_start=DEC1,
        period_end=JAN1,
        payment_due_date=date(2026, 12, 31),
        currency="USD",
        account_name="Main account",
        account_id=uuid4(),
        customer_name="Olga Petrova",
        billing_address=["5 Elm St", "Austin, TX 78702", "US"],
        format_name=bill_pdf.DETAILED,
        net=Decimal("355.00"),
        amount_due=Decimal("384.29"),
        remaining=Decimal("284.29"),
        taxes=[bill_pdf.TaxLine("salesTax", Decimal("0.0825"), Decimal("29.29"))],
        charges=[
            bill_pdf.Charge(f"Plan {i}", DEC1, JAN1, Decimal("71.00"), Decimal("76.86"))
            for i in range(5)
        ],
        applied=[bill_pdf.Applied("Payment received Nov 25, 2026", Decimal("100.00"))],
    )
    bill_pdf.render(bill)  # fonts load once
    start = time.process_time()
    for _ in range(1000):
        bill_pdf.render(bill)
    per_bill = (time.process_time() - start) / 1000
    print(f"bill PDF render: {per_bill * 1000:.1f} ms a bill (1000 bills)")
    assert per_bill < (1.0 if os.environ.get("PYTEST_XDIST_WORKER") else 0.05)


def test_money_must_be_decimal():
    with pytest.raises(bill_pdf.RenderError):
        bill_pdf.money(1.5, "USD")  # type: ignore[arg-type]
    assert bill_pdf.money(Decimal("1234.5"), "USD") == "1,234.50 USD"
