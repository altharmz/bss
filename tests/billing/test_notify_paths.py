"""The notification pass's failure, stop and race paths (app/worker/notify.py, #511), against the
real test database and the logging sender: a broken detector, one event that fails, a stop
between two sends, a message sent after the pass listed it, and a bill email whose PDF is
missing."""
# ruff: noqa: F811

import logging
from datetime import date, timedelta

import pytest

from app.integrations.notify import adapter as sender
from app.repository import bill_documents
from app.worker import notify
from app.worker.notify import process_notifications
from tests import apidata
from tests.builders.billing.billing_completion import MEDIA, run, worker  # noqa: F401
from tests.builders.billing.billing_db import add, at
from tests.builders.billing.dunning import DEC1, OVERDUE, billed, day
from tests.builders.worker.logs import worker_lines
from tests.timeline import NOW, TZ

REMINDER = OVERDUE + timedelta(days=1)


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


def messages(db_conn, account):
    rows = db_conn.execute(
        "SELECT event_type, channel, state, try_times, last_error FROM notification_message"
        " WHERE billing_account_id = %s ORDER BY event_type, channel",
        (account["id"],),
    ).fetchall()
    db_conn.commit()
    return rows


def told(*accounts_and_states):
    return [
        (event, channel, state, tries, None)
        for event, state, tries in accounts_and_states
        for channel in ("email", "sms")
    ]


def notify_pass(api, worker, **kwargs):
    return process_notifications(worker, clock=api.clock, tz=TZ, **kwargs)


def test_a_broken_detector_does_not_stop_the_others(api, worker, db_conn, monkeypatch, caplog):
    account, _ = billed(api, worker, db_conn)
    assert day(api, worker, REMINDER) == [(account["id"], "reminder1")]
    real = notify.events.detectors

    def broken(conn):
        raise RuntimeError("a bug in one detector")

    with monkeypatch.context() as patch:
        patch.setattr(
            notify.events,
            "detectors",
            lambda now, tz, accounts=None: [("broken", broken), *real(now, tz, accounts)],
        )
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            assert len(set(notify_pass(api, worker))) == 2  # queued, then sent
    assert worker_lines(caplog) == ["notification detector broken"]
    assert messages(db_conn, account) == told(("dunning_reminder1", "completed", 1))


def test_an_event_that_fails_does_not_stop_the_others_and_is_told_on_the_next_pass(
    api, worker, db_conn, monkeypatch, caplog
):
    (first, _), (second, _) = billed(api, worker, db_conn, 310, 40)
    assert sorted(day(api, worker, REMINDER)) == sorted(
        [(first["id"], "reminder1"), (second["id"], "reminder1")]
    )
    real = notify.service.notify

    def failing(conn, event, *, now, tz):
        if first["id"] in event.key:
            raise RuntimeError("a bug in one template")
        return real(conn, event, now=now, tz=tz)

    with monkeypatch.context() as patch:
        patch.setattr(notify.service, "notify", failing)
        with caplog.at_level(logging.ERROR, logger="app.worker"):
            notify_pass(api, worker)
    [failed] = worker_lines(caplog)
    assert failed == f"notification for dunning:{first['id']}:{OVERDUE.isoformat()}:reminder1"
    assert messages(db_conn, first) == []
    assert messages(db_conn, second) == told(("dunning_reminder1", "completed", 1))
    notify_pass(api, worker)
    assert messages(db_conn, first) == told(("dunning_reminder1", "completed", 1))


def test_a_stop_between_sends_and_a_message_sent_after_the_listing(
    api, worker, db_conn, log_sender, monkeypatch
):
    account, _ = billed(api, worker, db_conn)
    day(api, worker, REMINDER)
    # go through every detector, then stop before the first send
    answers = iter([False] * len(notify.events.detectors(api.clock.now(), TZ)))
    assert len(set(notify_pass(api, worker, checkpoint=lambda: next(answers, True)))) == 2
    assert [m[2] for m in messages(db_conn, account)] == ["initial", "initial"]
    assert log_sender.sent == []
    listed = notify.repo.due(worker, api.clock.now())
    worker.commit()
    assert len(listed) == 2
    assert len(set(notify_pass(api, worker))) == 2
    assert len(log_sender.sent) == 2
    # a pass that listed the messages before they were sent sends nothing again
    with monkeypatch.context() as patch:
        patch.setattr(notify.repo, "due", lambda conn, now: listed)
        assert notify_pass(api, worker) == []
    assert len(log_sender.sent) == 2
    assert messages(db_conn, account) == told(("dunning_reminder1", "completed", 1))


@pytest.mark.capability("worker.billEmail")
def test_a_bill_email_without_its_pdf_is_not_sent_and_is_retried(api, worker, db_conn, log_sender):
    api.clock.set(NOW)
    offering = apidata.sellable_offering(api, value=310)
    account = apidata.account(api)
    email = api.post(MEDIA, {"name": "Email", "description": "Bill by email"}).json()
    structure = {"billStructure": {"presentationMedia": [{"id": email["id"]}]}}
    assert api.patch(account["href"], structure).status_code == 200
    add(api, account, offering, DEC1, on=at(date(2026, 11, 20)))
    db_conn.execute(
        "UPDATE customer SET details = details || jsonb_build_object('contactMedium',"
        " jsonb_build_array(jsonb_build_object('@type', 'EmailContactMedium',"
        " 'emailAddress', 'olga@example.org')))"
        " WHERE id = (SELECT customer_id FROM billing_account WHERE id = %s)",
        (account["id"],),
    )
    db_conn.commit()
    run(worker, api, DEC1)
    [bill] = api.get(f"{apidata.BILL}?billingAccount.id={account['id']}").json()
    # the email is queued from the rendered PDF; the PDF is gone before it is sent
    answers = iter([False] * len(notify.events.detectors(api.clock.now(), TZ)))
    notify_pass(api, worker, checkpoint=lambda: next(answers, True))
    assert messages(db_conn, account) == [("bill_issued", "email", "initial", 0, None)]
    db_conn.execute("ALTER TABLE customer_bill_document DISABLE TRIGGER USER")
    db_conn.execute("DELETE FROM customer_bill_document WHERE customer_bill_id = %s", (bill["id"],))
    db_conn.execute("ALTER TABLE customer_bill_document ENABLE TRIGGER USER")
    db_conn.commit()
    notify_pass(api, worker)
    assert log_sender.sent == []
    assert messages(db_conn, account) == [
        ("bill_issued", "email", "inProgress", 1, f"bill {bill['id']} has no PDF")
    ]
    # rendered again: the retry after the backoff sends it with the PDF
    bill_documents.store(db_conn, bill["id"], now=api.clock.now())
    db_conn.commit()
    api.clock.set(api.clock.now() + timedelta(minutes=1))
    notify_pass(api, worker)
    [sent] = log_sender.sent
    assert [name for name, _, _ in sent.attachments] == [f"{bill['billNo']}.pdf"]
    assert [m[2] for m in messages(db_conn, account)] == ["completed"]
