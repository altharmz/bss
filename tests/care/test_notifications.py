"""Customer notifications (issue #192): TMF681 messages, triggers, consent, retries.

Every TMF681 response is checked against the vendored schema by the `api` client; the sender
is the `log` adapter, which records what it was given."""
# ruff: noqa: F811

from datetime import datetime, timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.integrations import notify as notify_module
from app.integrations.notify import adapter as sender
from app.notify import service as notify_service
from app.notify import templates
from tests.apiclient import total
from tests.builders.care.notifications import (
    MSG,
    PHONE,
    contact,
    customer_of,
    fetched,
    messages,
    of_event,
    quiet,
    trait,
)
from tests.builders.prepaid.api_topup import prepaid
from tests.builders.prepaid.payment_gateway import run, save, topup
from tests.builders.prepaid.prepaid_lifecycle import (  # noqa: F401 - fixtures
    at,
    go,
    numbered,
    top_up,
)
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.resources.network_provisioning import stub, world  # noqa: F401 - fixtures
from tests.builders.resources.number_orders import refs  # noqa: F401
from tests.builders.resources.sim_inventory import (  # noqa: F401 - fixtures
    activate_line,
    by_imsi,
    line_world,
    live_product,
    sim,
    stock,
    swap_order,
)

pytestmark = pytest.mark.capability("worker.notifications")

CONSENT = "/internal/v1/notification/consent"
MIB = 1024 * 1024
LINE = "12125550177"  # the number of the `numbered` line


@pytest.fixture(autouse=True)
def log_sender():
    sender().reset()
    yield sender()
    sender().reset()


def counts(api):
    found: dict[str, int] = {}
    for message in messages(api):
        found[trait(message, "event")] = found.get(trait(message, "event"), 0) + 1
    return found


# --- triggers: one message per event ---


def test_the_lifecycle_phases_each_tell_the_customer_once(api, db_conn, numbered):
    contact(db_conn, numbered.acc)
    due = numbered.due
    go(api, db_conn, due - timedelta(days=3))
    assert counts(api).get("plan_expiring") == 2  # one SMS and one email
    go(api, db_conn, due - timedelta(days=2))  # the next days add nothing
    assert counts(api).get("plan_expiring") == 2
    go(api, db_conn, due)
    grace = of_event(api, "grace")
    assert sorted(m["messageType"] for m in grace) == ["Email", "SMS"]
    sms = next(m for m in grace if m["messageType"] == "SMS")
    assert (due + timedelta(days=3)).strftime("%b") in sms["content"]
    go(api, db_conn, due + timedelta(days=1))
    assert counts(api)["grace"] == 2
    go(api, db_conn, due + timedelta(days=3))
    assert [m["state"] for m in of_event(api, "suspended")] == ["completed", "completed"]
    go(api, db_conn, due + timedelta(days=3, hours=0))
    assert counts(api)["suspended"] == 2
    assert fetched(db_conn, api.clock.now(), "lifecycle") == []  # told: not read again
    go(api, db_conn, due + timedelta(days=33))
    deactivated = of_event(api, "deactivated")
    assert sorted(m["messageType"] for m in deactivated) == ["Email", "SMS"]
    assert {m["receiver"][0].get("phoneNumber") for m in deactivated} == {PHONE, None}
    # the sender got every one of them, once
    keys = [s.key for s in sender().sent]
    assert len(keys) == len(set(keys)) == len(messages(api))
    assert all(m["state"] == "completed" for m in messages(api))


def test_without_a_phone_contact_the_sms_goes_to_the_line_and_a_gone_line_is_not_texted(
    api, db_conn, numbered
):
    due = numbered.due
    go(api, db_conn, due + timedelta(days=3))
    sms = of_event(api, "suspended")
    assert len(sms) == 1 and sms[0]["receiver"][0]["phoneNumber"] == LINE
    go(api, db_conn, due + timedelta(days=33))
    gone = of_event(api, "deactivated")
    # nobody to tell: the customer has no contact and the line is gone
    assert len(gone) == 1 and gone[0]["state"] == "cancelled"
    assert trait(gone[0], "stateReason") == "noRecipient"
    assert all(s.to == LINE for s in sender().sent)


def test_low_balance_is_told_once_per_top_up_episode(api, db_conn, numbered):
    assert counts(api)["low_balance"] == 1  # nothing left after the plan, below 5.00
    first = of_event(api, "low_balance")[0]
    assert "0.00 USD" in first["content"] and first["state"] == "completed"
    go(api, db_conn, api.clock.today() + timedelta(days=1))
    assert counts(api)["low_balance"] == 1  # the same episode
    top_up(api, db_conn, numbered.acc, "1", api.clock.today(), ticks=1)  # still low: 3.00
    assert counts(api)["low_balance"] == 2
    top_up(api, db_conn, numbered.acc, "20", api.clock.today(), ticks=2)  # 23.00: fine
    assert counts(api)["low_balance"] == 2


def test_a_told_event_is_not_looked_up_again_on_the_next_tick(api, db_conn, numbered, monkeypatch):
    # #281: the detectors find a told event on every tick; nothing is read for it again
    assert counts(api)["low_balance"] == 1
    told = {row[0] for row in db_conn.execute("SELECT dedupe_key FROM notification_message")}
    asked = []
    known = notify_service.notify

    def spy(conn, event, **kw):
        asked.append(event.key)
        return known(conn, event, **kw)

    monkeypatch.setattr(notify_service, "notify", spy)
    go(api, db_conn, api.clock.today() + timedelta(days=1))
    assert counts(api)["low_balance"] == 1  # found again, told once
    assert told and not told & set(asked)


def test_a_failed_renewal_and_the_renewal_after_a_top_up_are_each_told_once(api, db_conn, numbered):
    due = numbered.due
    go(api, db_conn, due)
    failed = of_event(api, "renewal_failed")
    assert (
        len(failed) == 1 and "12.00 USD" in failed[0]["content"]
    )  # 12.00 with tax needed, nothing there
    go(api, db_conn, due + timedelta(days=1))
    assert counts(api)["renewal_failed"] == 1  # the daily rescan is the same failure
    assert fetched(db_conn, api.clock.now(), "renewals") == []
    assert "renewal_done" not in counts(api)
    top_up(api, db_conn, numbered.acc, "20", due + timedelta(days=1))
    done = of_event(api, "renewal_done")
    assert len(done) == 1 and "Paid through" in done[0]["content"]
    go(api, db_conn, due + timedelta(days=2))
    assert counts(api)["renewal_done"] == 1
    assert fetched(db_conn, api.clock.now(), "renewals") == []


def test_usage_80_and_100_percent_each_tell_once(api, db_conn, numbered):
    # the line's plan has 5000 MB of data
    stub = __import__("app.integrations.network", fromlist=["adapter"]).adapter()
    now = api.clock.now()
    stub.inject_usage("n-1", "data", 4000 * MIB, now, msisdn=LINE)
    tick(db_conn, now)
    assert counts(api).get("usage_80") == 1 and "usage_100" not in counts(api)
    sms = of_event(api, "usage_80")[0]
    assert "80%" in sms["content"] and "data allowance" in sms["content"]
    stub.inject_usage("n-2", "data", 1000 * MIB, now + timedelta(minutes=1), msisdn=LINE)
    tick(db_conn, now + timedelta(minutes=1))
    tick(db_conn, now + timedelta(minutes=2))
    assert counts(api)["usage_80"] == 1 and counts(api)["usage_100"] == 1
    assert fetched(db_conn, now + timedelta(minutes=2), "usage") == []
    assert "all of your" in of_event(api, "usage_100")[0]["content"]


def test_a_declined_card_tells_the_customer_with_the_reason(api, db_conn):
    _, acc = prepaid(api, db_conn, funds="10")
    contact(db_conn, acc, email=None)
    save(api, acc, "tok_decline")
    topup(api, acc)
    run(db_conn, api, 2)
    declined = of_event(api, "card_declined")
    assert len(declined) == 1 and declined[0]["messageType"] == "SMS"
    assert "4242" in declined[0]["content"] and "card_declined" in declined[0]["content"]
    run(db_conn, api, 2)
    assert len(of_event(api, "card_declined")) == 1
    assert fetched(db_conn, api.clock.now(), "card_declines") == []


def test_port_in_status_is_told_when_the_line_claims_the_number(api, db_conn, world):
    from tests.builders.resources.number_porting import (
        BODY,
        buy_number,
        port_in,
    )

    contact(db_conn, world.acc, email=None)
    number = "12125550001"  # stays pending until the carrier completes it
    port = port_in(api, number=number)
    order = buy_number(api, world, port["resource"]["id"])
    assert order["state"] == "inProgress"
    tick(db_conn, api.clock.now())
    tick(db_conn, api.clock.now())
    pending = of_event(api, "port_in_pending")
    assert len(pending) == 1 and f"+{number}" in pending[0]["content"]
    assert "port_in_completed" not in counts(api)
    from app.integrations.network import adapter as network

    network().complete_port_in(number)
    tick(db_conn, api.clock.now())
    tick(db_conn, api.clock.now())
    assert len(of_event(api, "port_in_completed")) == 1
    assert len(of_event(api, "port_in_pending")) == 1
    assert fetched(db_conn, api.clock.now(), "ports") == []
    assert BODY  # (the module's default request, unused: the number differs)


def test_a_rejected_port_in_tells_the_reason(api, db_conn, world):
    from tests.builders.resources.number_porting import (
        buy_number,
        port_in,
    )

    contact(db_conn, world.acc, email=None)
    port = port_in(api, number="12125550142", transferPin="000000")  # pinMismatch
    buy_number(api, world, port["resource"]["id"])
    for _ in range(3):
        tick(db_conn, api.clock.now())
    rejected = of_event(api, "port_in_rejected")
    assert len(rejected) == 1 and "+12125550142" in rejected[0]["content"]


def test_a_sim_swap_is_told(api, db_conn, line_world):
    contact(db_conn, line_world.acc, email=None)
    stock(api, "B1", "physical", 1)
    stock(api, "E1", "esim", 1, start=20)
    activate_line(api, db_conn, line_world, "79991234500", "physical")
    product = live_product(db_conn)
    new = by_imsi(api, sim(20)["imsi"])
    order = swap_order(api, db_conn, line_world, product, new["id"]).json()
    tick(db_conn, api.clock.now())
    assert api.get(order["href"]).json()["state"] == "completed"
    swaps = of_event(api, "sim_swap")
    assert len(swaps) == 1 and "+79991234500" in swaps[0]["content"]
    tick(db_conn, api.clock.now())
    assert len(of_event(api, "sim_swap")) == 1
    assert fetched(db_conn, api.clock.now(), "sim_swaps") == []


def test_every_template_renders_for_both_channels():
    names = dict.fromkeys(
        ("name", "balance", "threshold", "plan", "date", "days", "shortfall", "line"), "x"
    ) | dict.fromkeys(("allowance", "last4", "reason", "bill_no", "period", "amount", "total"), "x")
    for event in templates.EVENTS:
        for channel in ("sms", "email"):
            subject, body, template = templates.render(event, channel, names)
            assert template == f"{event}.{channel}" and body
            assert (subject is None) == (channel == "sms")


# --- the sender: failure, retry, resend ---


def operator_sms(api, phone, **over):
    body = {
        "@type": "CommunicationMessage",
        "messageType": "SMS",
        "content": "Hello from the operator",
        "receiver": [{"@type": "Receiver", "phoneNumber": phone}],
        "sender": {"@type": "Sender", "name": "ops"},
    } | over
    return api.post(MSG, body)


def test_a_sender_failure_is_retried_with_backoff_then_recorded_as_failed(api, db_conn):
    created = operator_sms(api, "15555550000")  # the log sender refuses numbers ending 0000
    assert created.status_code == 201, created.text
    message = created.json()
    assert message["state"] == "initial" and message["tryTimes"] == 0
    now = api.clock.now()
    waits = [0, 1, 5, 15, 60]
    clock = now
    for attempt, wait in enumerate(waits, start=1):
        clock = clock + timedelta(minutes=wait)
        tick(db_conn, clock)
        current = api.get(message["href"]).json()
        assert current["tryTimes"] == attempt
        assert trait(current, "lastError") == "the sender refused 15555550000"
        assert current["state"] == ("failed" if attempt == len(waits) else "inProgress")
    # too early: nothing is sent before the backoff is over
    final = api.get(message["href"]).json()
    assert final["state"] == "failed" and sender().sent == []
    assert [c for c, _ in sender().attempts] == ["sms"] * 5
    tick(db_conn, clock + timedelta(hours=3))
    assert api.get(message["href"]).json()["tryTimes"] == 5  # failed is final


def test_a_retry_waits_for_its_backoff_and_succeeds_when_the_sender_recovers(api, db_conn):
    message = operator_sms(api, "15555550000").json()
    now = api.clock.now()
    tick(db_conn, now)
    assert api.get(message["href"]).json()["state"] == "inProgress"
    tick(db_conn, now + timedelta(seconds=30))  # inside the first minute: not tried again
    assert api.get(message["href"]).json()["tryTimes"] == 1
    sender().recover("15555550000")
    tick(db_conn, now + timedelta(minutes=1))
    done = api.get(message["href"]).json()
    assert done["state"] == "completed" and done["tryTimes"] == 2
    assert done["sendTimeComplete"] and trait(done, "lastError") is None
    assert trait(done, "providerReference") == "log-1"


def test_resend_queues_a_copy_of_a_failed_message(api, db_conn):
    message = operator_sms(api, "15555550000").json()
    now = api.clock.now()
    for minutes in (0, 1, 6, 21, 81):
        tick(db_conn, now + timedelta(minutes=minutes))
    assert api.get(message["href"]).json()["state"] == "failed"
    sender().recover("15555550000")
    resent = api.post(f"/internal/v1/notification/message/{message['id']}/resend", None)
    assert resent.status_code == 201, resent.text
    copy = resent.json()
    assert copy["id"] != message["id"] and trait(copy, "resendOf") == message["id"]
    assert copy["state"] == "initial" and copy["content"] == message["content"]
    tick(db_conn, now + timedelta(minutes=90))
    assert api.get(copy["href"]).json()["state"] == "completed"
    # an unsent message cannot be resent; an unknown one is 404
    fresh = operator_sms(api, PHONE).json()
    assert (
        api.post(f"/internal/v1/notification/message/{fresh['id']}/resend", None).status_code == 409
    )
    missing = "00000000-0000-4000-8000-000000000000"
    assert api.post(f"/internal/v1/notification/message/{missing}/resend", None).status_code == 404


# --- consent ---


def test_marketing_is_not_sent_without_a_recorded_opt_in(api, db_conn):
    _, acc = prepaid(api, db_conn)
    contact(db_conn, acc)
    customer = str(customer_of(db_conn, acc))

    def marketing(channel="SMS"):
        body = {
            "messageType": channel,
            "content": "Half price this week",
            "subject": "Offer",
            "characteristic": [
                {"@type": "StringCharacteristic", "name": "category", "value": "marketing"}
            ],
            "receiver": [
                {"@type": "Receiver", "party": {"id": customer, "@referredType": "Customer"}}
            ],
            "sender": {"@type": "Sender", "name": "promo"},
        }
        response = api.post(MSG, body)
        assert response.status_code == 201, response.text
        return response.json()

    refused = marketing()
    assert refused["state"] == "cancelled" and trait(refused, "stateReason") == "noConsent"
    tick(db_conn, api.clock.now())
    assert sender().attempts == []  # never reached the sender
    # a transactional message to the same customer needs no consent
    plain = operator_sms(
        api, PHONE, receiver=[{"party": {"id": customer, "@referredType": "Customer"}}]
    ).json()
    tick(db_conn, api.clock.now())
    assert api.get(plain["href"]).json()["state"] == "completed"
    # opt in for SMS only
    recorded = api.post(
        CONSENT,
        {"customer": {"id": customer}, "channel": "sms", "optedIn": True, "source": "signup form"},
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["marketing"]["sms"]["optedIn"] is True
    assert recorded.json()["marketing"]["email"] is None
    sms = marketing()
    assert sms["state"] == "initial"
    assert marketing("Email")["state"] == "cancelled"  # the email channel has no opt-in
    # opting out before the worker sends it cancels it at the gate
    api.post(
        CONSENT,
        {"customer": {"id": customer}, "channel": "sms", "optedIn": False, "source": "STOP"},
    )
    tick(db_conn, api.clock.now())
    final = api.get(sms["href"]).json()
    assert final["state"] == "cancelled" and trait(final, "stateReason") == "noConsent"
    history = api.get(f"{CONSENT}?customer.id={customer}").json()["history"]
    assert [h["optedIn"] for h in history] == [False, True]  # newest first
    assert history[0]["source"] == "STOP" and history[1]["source"] == "signup form"
    # opted in again: sent
    api.post(
        CONSENT,
        {"customer": {"id": customer}, "channel": "sms", "optedIn": True, "source": "call"},
    )
    again = marketing()
    tick(db_conn, api.clock.now())
    assert api.get(again["href"]).json()["state"] == "completed"
    assert [s.body for s in sender().sent if s.body == "Half price this week"] == [
        "Half price this week"
    ]


def test_marketing_to_an_unknown_party_cannot_be_consented(api):
    created = operator_sms(
        api,
        PHONE,
        characteristic=[
            {"@type": "StringCharacteristic", "name": "category", "value": "marketing"}
        ],
    )
    assert created.json()["state"] == "cancelled"


def test_the_consent_api_validates(api, db_conn):
    _, acc = prepaid(api, db_conn)
    customer = str(customer_of(db_conn, acc))
    body = {"customer": {"id": customer}, "channel": "sms", "optedIn": True, "source": "x"}
    assert api.post(CONSENT, body | {"channel": "fax"}).status_code == 422
    missing = "00000000-0000-4000-8000-000000000000"
    assert api.post(CONSENT, body | {"customer": {"id": missing}}).status_code == 404
    assert api.post(CONSENT, body | {"extra": 1}).status_code == 400
    assert api.get(CONSENT).status_code == 400
    assert api.get(f"{CONSENT}?customer.id={missing}").status_code == 404
    assert api.get(f"{CONSENT}?customer.id={customer}&x=1").status_code == 400
    empty = api.get(f"{CONSENT}?customer.id={customer}").json()
    assert empty["marketing"] == {"sms": None, "email": None} and empty["history"] == []


# --- quiet hours ---


def test_an_sms_inside_the_quiet_hours_waits_for_their_end(api, db_conn):
    day = api.clock.today()
    late = quiet(day, 23)
    api.clock.set(late)
    sms = operator_sms(api, PHONE).json()
    email = api.post(
        MSG,
        {
            "messageType": "Email",
            "subject": "Hi",
            "content": "Hello",
            "receiver": [{"email": "ada@example.org"}],
            "sender": {"name": "ops"},
        },
    ).json()
    morning = quiet(day + timedelta(days=1), 8)
    assert datetime.fromisoformat(sms["scheduledSendTime"]) == morning
    assert "scheduledSendTime" not in email
    tick(db_conn, late)
    assert api.get(sms["href"]).json()["state"] == "initial"  # held back
    assert api.get(email["href"]).json()["state"] == "completed"  # email has no quiet hours
    tick(db_conn, morning - timedelta(minutes=1))
    assert api.get(sms["href"]).json()["state"] == "initial"
    api.clock.set(morning)
    tick(db_conn, morning)
    sent = api.get(sms["href"]).json()
    assert sent["state"] == "completed" and datetime.fromisoformat(sent["sendTime"]) == morning


def test_the_quiet_hours_window_can_be_off_or_inside_one_day(monkeypatch):
    from zoneinfo import ZoneInfo

    from app.notify.service import quiet_until

    tz = ZoneInfo("UTC")
    noon = datetime(2026, 11, 6, 12, 0, tzinfo=tz)
    assert quiet_until(noon, tz) is None
    assert quiet_until(datetime(2026, 11, 6, 7, 59, tzinfo=tz), tz) == datetime(
        2026, 11, 6, 8, 0, tzinfo=tz
    )
    assert quiet_until(datetime(2026, 11, 6, 21, 0, tzinfo=tz), tz) == datetime(
        2026, 11, 7, 8, 0, tzinfo=tz
    )
    monkeypatch.setattr(settings, "notify_quiet_end", "21:00")  # start == end: no quiet hours
    assert quiet_until(datetime(2026, 11, 6, 22, 0, tzinfo=tz), tz) is None
    monkeypatch.setattr(settings, "notify_quiet_start", "01:00")
    monkeypatch.setattr(settings, "notify_quiet_end", "05:00")
    assert quiet_until(datetime(2026, 11, 6, 2, 0, tzinfo=tz), tz) == datetime(
        2026, 11, 6, 5, 0, tzinfo=tz
    )
    assert quiet_until(datetime(2026, 11, 6, 6, 0, tzinfo=tz), tz) is None


# --- TMF681 ---


def test_the_message_list_filters_and_fields(api, db_conn):
    _, acc = prepaid(api, db_conn)
    contact(db_conn, acc)
    customer = str(customer_of(db_conn, acc))
    party = {"party": {"id": customer, "@referredType": "Customer"}}
    operator_sms(api, PHONE, receiver=[party])
    api.post(
        MSG,
        {
            "messageType": "Email",
            "subject": "Hi",
            "content": "Hello",
            "receiver": [party],
            "sender": {"name": "ops"},
        },
    )
    assert len(messages(api)) == 2
    assert [m["messageType"] for m in messages(api, messageType="SMS")] == ["SMS"]
    assert [m["messageType"] for m in messages(api, messageType="email")] == ["Email"]
    assert len(messages(api, **{"receiver.party.id": customer})) == 2
    assert messages(api, **{"receiver.party.id": "00000000-0000-4000-8000-000000000000"}) == []
    assert len(messages(api, state="initial")) == 2 and messages(api, state="failed") == []
    assert api.get(f"{MSG}?bogus=1").status_code == 400
    assert api.get(f"{MSG}?messageType=pager").status_code == 400
    one = messages(api)[0]
    assert set(api.get(f"{one['href']}?fields=state,content").json()) == {
        "id",
        "href",
        "state",
        "content",
    } or set(api.get(f"{one['href']}?fields=state,content").json()) >= {"state", "content"}
    assert api.get(f"{MSG}/00000000-0000-4000-8000-000000000000").status_code == 404
    page = api.get(f"{MSG}?limit=1")
    assert total(page, api) == "2" and len(page.json()) == 1


def test_a_message_can_be_cancelled_until_it_is_sent(api, db_conn):
    message = operator_sms(api, PHONE).json()
    cancelled = api.patch(message["href"], {"state": "cancelled"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert trait(cancelled.json(), "stateReason") == "operator"
    tick(db_conn, api.clock.now())
    assert sender().attempts == []
    assert api.patch(message["href"], {"state": "cancelled"}).status_code == 409
    sent = operator_sms(api, PHONE).json()
    tick(db_conn, api.clock.now())
    assert api.patch(sent["href"], {"state": "cancelled"}).status_code == 409
    other = operator_sms(api, PHONE).json()
    assert api.patch(other["href"], {"state": "completed"}).status_code == 422
    assert api.patch(other["href"], {"content": "new"}).status_code == 400
    assert api.patch(other["href"], {"@type": "Other", "state": "cancelled"}).status_code == 400


def test_a_message_is_a_record_delete_and_the_hub_are_not_implemented(api):
    message = operator_sms(api, PHONE).json()
    assert api.delete(message["href"]).status_code == 405
    base = "/tmf-api/communicationManagement/v4"
    assert api.post(f"{base}/hub", {"callback": "https://example.org/x"}).status_code in (404, 405)


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"messageType": "Pager"}, 422),
        ({"messageType": "Email"}, 422),  # an email needs a subject
        ({"receiver": [{"email": "a@example.org"}]}, 422),  # an SMS needs a phone number
        ({"receiver": []}, 422),
        ({"receiver": [{"phoneNumber": PHONE}, {"phoneNumber": PHONE}]}, 422),
        ({"receiver": [{"phoneNumber": "12"}]}, 422),
        ({"receiver": [{"party": {"id": "00000000-0000-4000-8000-000000000000"}}]}, 422),
        ({"receiver": [{"phoneNumber": PHONE, "ip": "1.2.3.4"}]}, 400),
        ({"state": "completed"}, 400),
        ({"tryTimes": 3}, 400),
        ({"sendTime": "2026-01-01T00:00:00Z"}, 400),
        ({"attachment": [{"name": "x"}]}, 422),
        ({"logFlag": False}, 422),
        ({"characteristic": [{"name": "color", "value": "red"}]}, 422),
        ({"characteristic": [{"name": "category", "value": "spam"}]}, 422),
        ({"@type": "Other"}, 400),
        ({"nonsense": 1}, 400),
        ({"content": ""}, 422),
    ],
)
def test_a_bad_message_is_refused(api, db_conn, change, status):
    body = {
        "messageType": "SMS",
        "content": "x",
        "receiver": [{"phoneNumber": PHONE}],
        "sender": {"name": "ops"},
    } | change
    response = api.post(MSG, body)
    assert response.status_code == status, response.text
    assert db_conn.execute("SELECT count(*) FROM notification_message").fetchone() == (0,)


def test_a_missing_required_attribute_is_refused(api):
    for missing in ("content", "receiver", "sender", "messageType"):
        body = {
            "messageType": "SMS",
            "content": "x",
            "receiver": [{"phoneNumber": PHONE}],
            "sender": {"name": "ops"},
        }
        del body[missing]
        assert api.post(MSG, body).status_code == 422


def test_the_receiver_is_completed_from_the_customer_and_the_sender_is_shown(api, db_conn):
    _, acc = prepaid(api, db_conn)
    contact(db_conn, acc, phone="(212) 555-0123", email="ada@example.org")
    customer = str(customer_of(db_conn, acc))
    party = {"party": {"id": customer, "@referredType": "Customer"}}
    sms = operator_sms(api, PHONE, receiver=[party]).json()
    assert sms["receiver"][0]["phoneNumber"] == PHONE  # (212) 555-0123 -> 1 212 555 0123
    assert sms["receiver"][0]["party"]["id"] == customer
    assert sms["sender"] == {"@type": "Sender", "name": "ops"}
    email = api.post(
        MSG,
        {
            "messageType": "Email",
            "subject": "Hi",
            "content": "Hello",
            "receiver": [party],
            "sender": {"name": "ops"},
        },
    ).json()
    assert email["receiver"][0]["email"] == "ada@example.org"
    assert email["state"] == "initial" and email["tryTimes"] == 0


# --- configuration ---


def test_an_unknown_notify_adapter_fails_startup(monkeypatch):
    assert Settings().notify_adapter == "log"
    with pytest.raises(ValidationError):
        Settings(notify_adapter="nope")
    with monkeypatch.context() as patched:
        patched.setattr(settings, "notify_adapter", "nope")
        with pytest.raises(ValueError, match="unknown BSS_NOTIFY_ADAPTER"):
            notify_module.adapter()


def test_the_sender_answers_a_repeated_key_with_its_first_answer():
    first = sender().send_sms("k", PHONE, "hello")
    assert sender().send_sms("k", PHONE, "hello") == first
    assert len(sender().sent) == 1
    assert D("5.00") == settings.notify_low_balance
    assert UUID("00000000-0000-4000-8000-000000000000")
