"""The payment worker's failure and race paths (#480), against the real test database.

A scripted gateway stands in for the adapter: it raises or answers what each test needs,
so every path ends in a checked command state, balance and set of financial rows, and a
resend always carries the command's own key."""

from datetime import timedelta
from decimal import Decimal as D
from uuid import UUID

import pytest

from app.clock import FixedClock
from app.integrations.payment import ChargeResult, PaymentDeclined
from app.integrations.payment.stub import StubPayment
from app.repository import funding
from app.repository import payment_commands as commands
from app.worker import payments
from tests.builders.prepaid.api_topup import prepaid
from tests.builders.prepaid.payment_gateway import rule, save, topup
from tests.builders.prepaid.prepaid_posting import TZ


class Gateway(StubPayment):
    """The stub, with scripted failures: `on_charge` / `on_result` are raised or returned
    once per call, in order; afterwards it behaves as the stub."""

    def __init__(self, on_charge=(), on_result=()):
        super().__init__()
        self.on_charge = list(on_charge)
        self.on_result = list(on_result)
        self.results: list[str] = []

    def charge(self, key, token, amount, currency):
        if self.on_charge:
            self.attempts.append(key)
            raise self.on_charge.pop(0)
        super().charge(key, token, amount, currency)

    def result(self, key):
        self.results.append(key)
        if self.on_result:
            scripted = self.on_result.pop(0)
            if isinstance(scripted, Exception):
                raise scripted
            return scripted
        return super().result(key)


@pytest.fixture
def gateway(monkeypatch):
    fake = Gateway()
    monkeypatch.setattr(payments, "adapter", lambda: fake)
    return fake


def process(db_conn, api, minutes=0, **kwargs):
    acted = payments.process_payments(
        db_conn,
        clock=FixedClock(api.clock.now() + timedelta(minutes=minutes)),
        tz=TZ,
        **kwargs,
    )
    db_conn.commit()
    return acted


def command(db_conn, cid):
    db_conn.commit()
    return commands.get(db_conn, UUID(cid))


def balance(db_conn, acc):
    spendable = funding.load(db_conn, UUID(acc["id"])).spendable
    db_conn.commit()
    return spendable


def money_rows(db_conn, acc):
    """The gateway's TMF676 payments and TMF654 top-ups on the account (not its opening funds)."""
    db_conn.commit()
    paid = db_conn.execute(
        "SELECT count(*) FROM payment WHERE billing_account_id = %s"
        " AND payment_method_name LIKE '%% card ending %%'",
        (acc["id"],),
    ).fetchone()[0]
    topped = db_conn.execute(
        "SELECT count(*) FROM balance_action WHERE billing_account_id = %s AND kind = 'topup'"
        " AND reason = 'Card top-up' AND payment_id IS NOT NULL",
        (acc["id"],),
    ).fetchone()[0]
    return paid, topped


def queued_topup(api, db_conn, funds="10", token="tok_visa"):
    _, acc = prepaid(api, db_conn, funds=funds)
    save(api, acc, token)
    response = topup(api, acc)
    assert response.status_code == 202, response.text
    return acc, response.json()["id"]


def test_a_decline_at_submission_is_final_and_records_nothing(api, db_conn, gateway):
    gateway.on_charge = [PaymentDeclined("insufficient_funds: try another card")]
    acc, cid = queued_topup(api, db_conn)
    assert process(db_conn, api) == [UUID(cid)]
    declined = command(db_conn, cid)
    assert declined["state"] == "declined"
    assert declined["decline_reason"] == "insufficient_funds: try another card"
    process(db_conn, api, minutes=60)
    assert gateway.attempts == [cid]  # never sent again
    assert gateway.results == []  # nothing to read
    assert balance(db_conn, acc) == D("10") and money_rows(db_conn, acc) == (0, 0)


def test_an_unexpected_error_is_retried_with_the_same_key_and_charges_once(api, db_conn, gateway):
    gateway.on_charge = [ConnectionResetError("peer reset"), RuntimeError()]
    acc, cid = queued_topup(api, db_conn)
    process(db_conn, api)
    first = command(db_conn, cid)
    assert (first["state"], first["attempts"], first["last_error"]) == ("pending", 1, "peer reset")
    process(db_conn, api, minutes=100)
    second = command(db_conn, cid)  # an error without a message is named by its type
    assert (second["state"], second["attempts"], second["last_error"]) == (
        "pending",
        2,
        "RuntimeError",
    )
    assert balance(db_conn, acc) == D("10") and money_rows(db_conn, acc) == (0, 0)
    process(db_conn, api, minutes=1000)  # accepted
    process(db_conn, api, minutes=1001)  # read
    assert command(db_conn, cid)["state"] == "confirmed"
    assert gateway.attempts == [cid, cid, cid]
    assert len(gateway.charges) == 1
    assert balance(db_conn, acc) == D("35") and money_rows(db_conn, acc) == (1, 1)


def test_a_failed_or_empty_result_read_leaves_the_charge_sent_until_an_answer(
    api, db_conn, gateway
):
    acc, cid = queued_topup(api, db_conn)
    process(db_conn, api)  # submitted
    gateway.on_result = [TimeoutError("read timed out"), None]
    assert process(db_conn, api, minutes=1) == []  # the read failed
    assert process(db_conn, api, minutes=2) == []  # the gateway has not decided
    assert command(db_conn, cid)["state"] == "sent"
    assert balance(db_conn, acc) == D("10") and money_rows(db_conn, acc) == (0, 0)
    assert process(db_conn, api, minutes=3) == [UUID(cid)]
    confirmed = command(db_conn, cid)
    assert confirmed["state"] == "confirmed" and confirmed["gateway_ref"] == f"stub_ch_{cid}"
    assert gateway.results == [cid, cid, cid]
    assert gateway.attempts == [cid]  # read again, never charged again
    assert balance(db_conn, acc) == D("35") and money_rows(db_conn, acc) == (1, 1)


def test_a_result_without_a_gateway_reference_is_kept_under_the_command_id(api, db_conn, gateway):
    acc, cid = queued_topup(api, db_conn)
    process(db_conn, api)
    gateway.on_result = [ChargeResult("succeeded"), ChargeResult("declined")]
    process(db_conn, api, minutes=1)
    assert command(db_conn, cid)["gateway_ref"] == cid
    other = topup(api, acc, key="k-2").json()["id"]
    process(db_conn, api, minutes=2)
    process(db_conn, api, minutes=3)
    assert command(db_conn, other)["decline_reason"] == "declined by the gateway"


def test_a_checkpoint_stops_the_pass_before_the_next_command(api, db_conn, gateway):
    acc, cid = queued_topup(api, db_conn)
    beats = []
    stop = lambda: True  # noqa: E731
    assert process(db_conn, api, checkpoint=stop, heartbeat=lambda: beats.append(1)) == []
    assert command(db_conn, cid)["state"] == "pending" and gateway.attempts == []
    assert process(db_conn, api, heartbeat=lambda: beats.append(1)) == [UUID(cid)]
    assert beats == [1]  # one beat per command sent
    # sent: a stop now comes before its result is read
    assert process(db_conn, api, minutes=1, checkpoint=stop) == []
    assert command(db_conn, cid)["state"] == "sent" and gateway.results == []
    assert process(db_conn, api, minutes=2, heartbeat=lambda: beats.append(2)) == [UUID(cid)]
    assert beats == [1, 2]  # and one per result read
    assert balance(db_conn, acc) == D("35")


def test_a_command_another_worker_handled_after_the_scan_is_not_sent(
    api, db_conn, gateway, monkeypatch
):
    acc, cid = queued_topup(api, db_conn)
    to_send = commands.to_send

    def raced_send(conn, now):
        due = to_send(conn, now)
        conn.execute("UPDATE payment_command SET state = 'cancelled' WHERE id = %s", (cid,))
        return due

    monkeypatch.setattr(commands, "to_send", raced_send)
    assert process(db_conn, api) == []
    assert gateway.attempts == []  # not sent: it was no longer pending under the lock
    assert balance(db_conn, acc) == D("10") and money_rows(db_conn, acc) == (0, 0)


def test_a_result_another_worker_recorded_after_the_scan_is_not_read_again(
    api, db_conn, gateway, monkeypatch
):
    acc, cid = queued_topup(api, db_conn)
    process(db_conn, api)  # sent
    awaiting = commands.awaiting

    def raced_read(conn):
        rows = awaiting(conn)
        conn.execute("UPDATE payment_command SET state = 'cancelled' WHERE id = %s", (cid,))
        return rows

    monkeypatch.setattr(commands, "awaiting", raced_read)
    assert process(db_conn, api, minutes=1) == []
    assert gateway.results == []  # not read: no longer sent under the lock
    assert balance(db_conn, acc) == D("10") and money_rows(db_conn, acc) == (0, 0)


def test_a_confirmation_for_an_account_without_a_bucket_records_no_money(
    api, db_conn, gateway, monkeypatch
):
    acc, cid = queued_topup(api, db_conn)
    process(db_conn, api)  # sent
    monkeypatch.setattr(payments.balance_actions, "account_bucket", lambda conn, aid: None)
    with pytest.raises(ValueError, match="has no prepaid bucket"):
        process(db_conn, api, minutes=1)
    db_conn.rollback()
    # the TMF676 payment written before the bucket lookup went with the transaction
    assert money_rows(db_conn, acc) == (0, 0)
    assert command(db_conn, cid)["state"] == "sent"  # read again on a later wake-up


def test_an_auto_top_up_skips_an_expired_default_card(api, db_conn, gateway):
    _, acc = prepaid(api, db_conn, funds="5")
    card = save(api, acc)
    assert rule(api, acc).status_code == 200
    db_conn.execute(
        "UPDATE payment_method SET expiration_date = %s WHERE id = %s",
        (api.clock.now() - timedelta(days=1), card["id"]),
    )
    db_conn.commit()
    assert payments.start_auto_topups(db_conn, now=api.clock.now()) == []
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM payment_command").fetchone()[0] == 0


def test_an_auto_top_up_started_by_another_worker_after_the_scan_is_not_doubled(
    api, db_conn, gateway, monkeypatch
):
    _, acc = prepaid(api, db_conn, funds="5")
    card = save(api, acc)
    assert rule(api, acc).status_code == 200
    candidates = commands.auto_candidates

    def raced(conn):
        rows = candidates(conn)
        commands.insert(
            conn,
            account_id=UUID(acc["id"]),
            method_id=UUID(card["id"]),
            purpose="auto_topup",
            amount=D("50"),
            currency="USD",
            client_key=None,
            request={"raced": True},
            now=api.clock.now(),
        )
        return rows

    monkeypatch.setattr(commands, "auto_candidates", raced)
    assert payments.start_auto_topups(db_conn, now=api.clock.now()) == []
    db_conn.commit()
    rows = db_conn.execute("SELECT purpose, request FROM payment_command").fetchall()
    assert rows == [("auto_topup", {"raced": True})]  # only the other worker's charge


def renewal_candidate(acc, required):
    return {
        "command_id": None,
        "account_id": UUID(acc["id"]),
        "currency": "USD",
        "required": D(required),
    }


def test_a_renewal_funded_since_the_scan_is_not_charged(api, db_conn, gateway, monkeypatch):
    _, acc = prepaid(api, db_conn, funds="20")
    save(api, acc)
    monkeypatch.setattr(commands, "renewal_candidates", lambda conn: [renewal_candidate(acc, "20")])
    assert payments.start_renewal_charges(db_conn, now=api.clock.now()) == []
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM payment_command").fetchone()[0] == 0


def test_a_renewal_charge_already_in_flight_is_not_doubled(api, db_conn, gateway, monkeypatch):
    _, acc = prepaid(api, db_conn, funds="5")
    card = save(api, acc)
    commands.insert(
        db_conn,
        account_id=UUID(acc["id"]),
        method_id=UUID(card["id"]),
        purpose="renewal",
        amount=D("3"),
        currency="USD",
        client_key=None,
        request={"raced": True},
        now=api.clock.now(),
    )
    db_conn.commit()
    monkeypatch.setattr(commands, "renewal_candidates", lambda conn: [renewal_candidate(acc, "20")])
    assert payments.start_renewal_charges(db_conn, now=api.clock.now()) == []
    db_conn.commit()
    rows = db_conn.execute("SELECT amount FROM payment_command").fetchall()
    assert rows == [(D("3"),)]  # the one in flight, not a second for the 15 shortfall


def test_a_renewal_with_an_expired_default_card_is_not_charged(api, db_conn, gateway, monkeypatch):
    _, acc = prepaid(api, db_conn, funds="5")
    card = save(api, acc)
    db_conn.execute(
        "UPDATE payment_method SET expiration_date = %s WHERE id = %s",
        (api.clock.now(), card["id"]),  # expiring now counts as expired
    )
    db_conn.commit()
    monkeypatch.setattr(commands, "renewal_candidates", lambda conn: [renewal_candidate(acc, "20")])
    assert payments.start_renewal_charges(db_conn, now=api.clock.now()) == []
    db_conn.commit()
    assert db_conn.execute("SELECT count(*) FROM payment_command").fetchone()[0] == 0
