"""Refusals of the internal API's query parameters, ids and bodies (#501): each answers the
TMF error contract (status and message) before anything is read or written."""

from uuid import UUID

import pytest

from app.integrations.network import NetworkTemporary
from app.integrations.network import adapter as network
from app.internal import wholesale
from app.repository import notifications
from tests.builders.resources.sim_inventory import BATCH, sim

BASE = "/internal/v1"
NIL = UUID(int=7)


def refused(response, status, message):
    assert response.status_code == status, response.text
    assert message in response.json()["message"], response.text


@pytest.mark.parametrize(
    "url,message",
    [
        (f"{BASE}/network/command?state=bogus", "state must be one of"),
        (f"{BASE}/network/command?product.id={NIL}&product.id={NIL}", "given more than once"),
        (f"{BASE}/network/command?productOrder.id=nope", "productOrder.id must be a UUID"),
        (
            f"{BASE}/payment/command?billingAccount.id={NIL}&billingAccount.id={NIL}",
            "more than once",
        ),
        (f"{BASE}/payment/command?billingAccount.id=nope", "billingAccount.id must be a UUID"),
        (f"{BASE}/wholesale/invoice/{NIL}/discrepancies?kind=bogus", "kind must be one of"),
        (f"{BASE}/wholesale/invoice/{NIL}/lines?sort=bogus", "sort must be one of"),
        (
            f"{BASE}/wholesale/invoice/{NIL}/lines?sort=msisdn&sort=margin",
            "sort given more than once",
        ),
        (f"{BASE}/dunning/accounts?step=none&step=none", "step given more than once"),
        (f"{BASE}/security/audit?product.id={NIL}&product.id={NIL}", "product.id given more than"),
        (f"{BASE}/security/audit?action=x&action=y", "action given more than once"),
        (f"{BASE}/usage/summary?product.id={NIL}&date=yesterday", "invalid value for date"),
        (f"{BASE}/subscriber?q={'x' * 201}", "q is too long"),
        (f"{BASE}/port?direction=sideways", "direction must be one of"),
    ],
)
def test_a_bad_list_filter_is_a_400(api, url, message):
    refused(api.get(url), 400, message)


def test_unknown_ids_are_a_404(api):
    refused(api.get(f"{BASE}/network/command/{NIL}"), 404, f"networkCommand {NIL} not found")
    refused(api.get(f"{BASE}/port/{NIL}"), 404, f"portRequest {NIL} not found")
    decision = api.post(f"{BASE}/careSignup/{NIL}/decision", {"decision": "approve"})
    refused(decision, 404, f"careSignup {NIL} not found")


def test_an_invoice_upload_takes_query_parameters_it_knows_only(api):
    response = api.post_raw(
        f"{BASE}/wholesale/invoice?invoiceNumber=TMO-1&dryRun=1", "msisdn\n", "text/csv"
    )
    refused(response, 400, "unknown query parameter dryRun")
    twice = api.post_raw(
        f"{BASE}/wholesale/invoice?invoiceNumber=TMO-1&invoiceNumber=TMO-2", "msisdn\n", "text/csv"
    )
    refused(twice, 400, "invoiceNumber given more than once")


def test_an_invoice_file_over_the_limit_is_a_413_declared_or_streamed(api, monkeypatch):
    monkeypatch.setattr(wholesale, "MAX_BYTES", 8)
    url = f"{BASE}/wholesale/invoice?invoiceNumber=TMO-1"
    declared = api.post_raw(url, "msisdn,charge\n", "text/csv")  # Content-Length 14
    refused(declared, 413, "the invoice file is larger than")

    def chunks():  # no Content-Length: only the read body shows the size
        yield b"msisdn,"
        yield b"charge\n"

    streamed = api.request("POST", url, content=chunks(), headers={"Content-Type": "text/csv"})
    refused(streamed, 413, "the invoice file is larger than")
    assert api.get(f"{BASE}/wholesale/invoice").json() == []


def test_dunning_settings_cannot_be_removed_or_out_of_range(api):
    url = f"{BASE}/dunning/settings"
    before = api.get(url).json()
    refused(api.patch(url, {"reminder2Days": None}), 422, "reminder2Days cannot be removed")
    refused(api.patch(url, {"minimumOverdue": -1}), 422, "must be zero or more")
    refused(api.patch(url, {"minimumOverdue": 10**10}), 422, "below 10000000000")
    assert api.get(url).json() == before


def test_a_message_that_reached_nobody_cannot_be_resent(api, db_conn):
    """An event with no reachable contact leaves one cancelled noRecipient row (notify)."""
    row = notifications.insert(
        db_conn,
        event_type="renewal_failed",
        category="transactional",
        channel="sms",
        template="renewal_failed.sms",
        state="cancelled",
        state_reason="noRecipient",
        receiver_name="Ada",
        receiver_email=None,
        receiver_phone=None,
        customer_id=None,
        billing_account_id=None,
        product_id=None,
        subject=None,
        content="Your renewal failed",
        now=api.clock.now(),
    )
    db_conn.commit()
    resent = api.post(f"{BASE}/notification/message/{row['id']}/resend", None)
    refused(resent, 409, "the message had no receiver address")
    (count,) = db_conn.execute("SELECT count(*) FROM notification_message").fetchone()
    db_conn.commit()
    assert count == 1


# --- SIM stock imports ---


def csv_import(api, text, query="batch=C1&simType=physical"):
    return api.post_raw(f"{BATCH}?{query}", text, content_type="text/csv")


def stocked(api):
    return api.get(f"{BATCH}").json()


def test_a_sim_import_refuses_what_it_cannot_read(api):
    before = stocked(api)
    refused(api.post_raw(BATCH, "iccid,imsi\n", "text/plain"), 415, "Content-Type must be")
    row = f"{sim(1)['iccid']},{sim(1)['imsi']}"
    refused(csv_import(api, f"iccid,imsi\n{row},extra\n"), 422, "line 2: more values than columns")
    refused(csv_import(api, f"iccid,imsi,colour\n{row},red\n"), 422, "unknown columns colour")
    refused(
        csv_import(api, f"iccid,imsi\n{row}\n", "batch=C1&simType=physical&supplier=x"),
        400,
        "unknown query parameter supplier",
    )
    too_many = {"batch": "B1", "simType": "physical", "items": [sim(1)] * 5001}
    refused(api.post(BATCH, too_many), 422, "a batch holds at most 5000 SIMs")
    not_objects = {"batch": "B1", "simType": "physical", "items": [sim(1)["iccid"]]}
    refused(api.post(BATCH, not_objects), 422, "every item must be an object")
    dollar = {**sim(2, "esim"), "matchingId": "AB$C"}
    refused(
        api.post(BATCH, {"batch": "E1", "simType": "esim", "items": [dollar]}),
        422,
        "smdpAddress and matchingId must not contain '$'",
    )
    assert stocked(api) == before


def test_esim_generation_refusals_and_an_unreachable_network(api, monkeypatch):
    url = f"{BASE}/sim/esim-generate"
    refused(api.post(url, {"batch": "G1", "count": 1, "kind": "x"}), 400, "unknown attribute kind")
    refused(api.post(url, {"batch": "no spaces", "count": 1}), 422, "batch must be 1..60")

    def down(batch, count):
        raise NetworkTemporary("stub: the SM-DP+ is unavailable")

    monkeypatch.setattr(network(), "generate_esim_profiles", down)
    refused(api.post(url, {"batch": "G1", "count": 1}), 503, "the SM-DP+ is unavailable")
    assert stocked(api) == []


# --- the number pool ---


def test_a_number_pool_request_keeps_numbers_already_held_and_waits_out_the_carrier(
    api, monkeypatch
):
    pool = f"{BASE}/number/pool"
    first = api.post(pool, {"areaCode": "212", "count": 2})
    assert first.status_code == 201, first.text
    network().pool_given.clear()  # the carrier hands the same two numbers out again
    again = api.post(pool, {"areaCode": "212", "count": 3})
    assert again.status_code == 201, again.text
    held = {n["value"] for n in first.json()["numbers"]}
    (new,) = again.json()["numbers"]  # the two it holds are not made again
    assert new["value"] not in held and again.json()["requested"] == 3

    def down(area_code, count):
        raise NetworkTemporary("stub: the carrier is unavailable")

    monkeypatch.setattr(network(), "request_numbers", down)
    refused(api.post(pool, {"areaCode": "212", "count": 1}), 503, "the carrier is unavailable")
