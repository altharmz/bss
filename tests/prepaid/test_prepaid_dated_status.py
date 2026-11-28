"""Dated prepaid suspend/resume through the billing owner, and number-only changes
(docs/ui.md)."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.billing.status_change import post_status_change
from app.repository import prepaid_commands as commands
from app.worker import db as worker_db
from tests import apidata, timeline
from tests.apidata import ORDER
from tests.builders.orders.api_order_cancel import CANCEL, cancel_body
from tests.builders.orders.api_suspension import status_item
from tests.builders.prepaid.prepaid_orders import purchase, shop
from tests.builders.prepaid.prepaid_posting import TZ
from tests.builders.prepaid.prepaid_renewal_producer import pay, tick
from tests.builders.prepaid.prepaid_status_orders import count, errors, view
from tests.builders.prepaid.prepaid_status_orders import granted as granted_two  # noqa: F401
from tests.builders.prepaid.prepaid_terminations import terminate
from tests.builders.resources.api_resource_inventory import number
from tests.builders.resources.number_orders import refs
from tests.builders.tmf.events import stored, subscribe

granted = granted_two


def today(api):
    return api.clock.now().astimezone(TZ).date()


def at_dawn(day):
    return datetime.combine(day, time(0, 5), TZ)


def dated(api, acc, product, status, day, item_id="1"):
    body = apidata.order_body(
        acc,
        status_item(product, status, item_id),
        requestedStartDate=timeline.iso(timeline.midnight(day)),
    )
    response = api.post(ORDER, body)
    assert response.status_code == 201, response.text
    return response.json()


def command_of(conn, order):
    return commands.get(conn, UUID(order["id"]))


@pytest.mark.capability("worker.prepaidDatedStatus")
def test_dated_suspend_waits_for_its_day_and_keeps_the_paid_interval(api, db_conn, granted):
    acc, (first, second) = granted
    day = today(api) + timedelta(days=3)
    subscribe(api, "TMF637", "ProductStateChangeEvent")
    order = dated(api, acc, first, "suspended", day)
    assert order["state"] == "inProgress" and errors(order) == []
    assert "completionDate" not in order
    cmd = command_of(db_conn, order)
    assert (cmd["kind"], cmd["due_date"], cmd["state"]) == ("order", day, "pending")
    assert cmd["request"]["status_changes"] == [{"product_id": first["id"], "status": "suspended"}]
    assert view(api, acc, first)["status"] == "active"
    pending = view(api, acc, first)["pendingChanges"]
    assert [(c["kind"], c["status"], c["date"]) for c in pending] == [
        ("status", "suspended", day.isoformat())
    ]
    charges = count(db_conn, "applied_customer_billing_rate")

    assert (
        command_of(db_conn, order)["id"]
        not in tick(db_conn, at_dawn(day - timedelta(days=1))).prepaid_commands
    )
    assert api.get(order["href"]).json()["state"] == "inProgress"

    result = tick(db_conn, at_dawn(day))
    assert result.prepaid_commands == [cmd["id"]]
    done = api.get(order["href"]).json()
    assert done["state"] == "completed" and "completionDate" in done
    api.clock.set(at_dawn(day))
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("suspended", "manual")
    assert shown["paidThrough"] == first["due"].isoformat() and shown["pendingChanges"] == []
    assert count(db_conn, "funded_interval") == 2
    assert count(db_conn, "applied_customer_billing_rate") == charges
    assert command_of(db_conn, order)["result"] == {"outcome": "statusChanged"}
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["suspended"]

    assert post_status_change(db_conn, cmd["id"], now=at_dawn(day), tz=TZ) == {
        "outcome": "statusChanged"
    }
    db_conn.commit()
    assert command_of(db_conn, order)["attempts"] == 1
    assert [e["event"]["product"]["status"] for _, e in stored(db_conn, "TMF637")] == ["suspended"]


def test_a_dated_status_order_cannot_be_posted_without_billing_ownership(api, db_conn, granted):
    acc, (first, _) = granted
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "suspended", day)
    worker_db.release_ownership(db_conn)
    with pytest.raises(ValueError, match="prepaid posting requires billing ownership"):
        post_status_change(db_conn, UUID(order["id"]), now=at_dawn(day), tz=TZ)
    assert api.get(order["href"]).json()["state"] == "inProgress"
    assert command_of(db_conn, order)["attempts"] == 0


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("renewal", "status change requires a prepaid order command"),
        ("order", "status command must match its entire pending order"),
    ],
)
def test_a_command_cannot_apply_another_orders_status_change(api, db_conn, granted, kind, message):
    acc, (first, _) = granted
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "suspended", day)
    cid = uuid4()
    commands.enqueue(
        db_conn,
        cid,
        UUID(acc["id"]),
        kind=kind,
        due_date=day,
        request=command_of(db_conn, order)["request"],
        now=api.clock.now(),
    )
    db_conn.commit()
    with pytest.raises(ValueError, match=message):
        post_status_change(db_conn, cid, now=at_dawn(day), tz=TZ)
    assert api.get(order["href"]).json()["state"] == "inProgress"
    assert view(api, acc, first)["status"] == "active"
    assert commands.get(db_conn, cid)["attempts"] == 0


def test_dated_resume_after_expiry_queues_the_renewal(api, db_conn, granted):
    acc, (first, second) = granted
    for product in (first, second):
        assert (
            api.post(ORDER, apidata.order_body(acc, status_item(product, "suspended"))).status_code
            == 201
        )
    day = first["due"] + timedelta(days=3)
    order = dated(api, acc, first, "active", day)
    assert order["state"] == "inProgress"
    renewal = commands.renewal_id(first["grant"])
    assert commands.get(db_conn, renewal) is None  # nothing queued before the day

    tick(db_conn, at_dawn(day))
    assert api.get(order["href"]).json()["state"] == "completed"
    queued = commands.get(db_conn, renewal)
    assert (queued["kind"], queued["due_date"], queued["state"]) == (
        "renewal",
        first["due"],
        "pending",
    )
    status = dict(db_conn.execute("SELECT id::text, status FROM product").fetchall())
    assert status[first["id"]] == "suspended"  # funding-suspended until paid

    pay(db_conn, UUID(acc["id"]), "12", at_dawn(day))
    assert renewal in tick(db_conn, at_dawn(day) + timedelta(minutes=1)).prepaid_commands
    assert db_conn.execute(
        "SELECT lower(period) FROM funded_interval WHERE command_id = %s", (renewal,)
    ).fetchone() == (day,)
    status = dict(db_conn.execute("SELECT id::text, status FROM product").fetchall())
    assert status[first["id"]] == "active"


def test_cancel_before_its_day_releases_everything_and_after_it_is_refused(api, db_conn, granted):
    acc, (first, second) = granted
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "suspended", day)
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert api.get(order["href"]).json()["state"] == "cancelled"
    assert command_of(db_conn, order)["result"] == {"outcome": "cancelled"}
    assert count(db_conn, "product_status_change") == 0
    assert tick(db_conn, at_dawn(day)).prepaid_commands == []
    assert view(api, acc, first)["status"] == "active"

    again = dated(api, acc, first, "suspended", day)  # nothing waits any more
    tick(db_conn, at_dawn(day))
    assert api.get(again["href"]).json()["state"] == "completed"
    api.clock.set(at_dawn(day) + timedelta(days=1))
    assert api.post(CANCEL, cancel_body(again)).status_code == 409


def test_cancel_on_its_day_undoes_the_applied_change(api, db_conn, granted):
    acc, (first, _) = granted
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "suspended", day)
    tick(db_conn, at_dawn(day))
    api.clock.set(at_dawn(day))
    assert view(api, acc, first)["status"] == "suspended"
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    shown = view(api, acc, first)
    assert (shown["status"], shown["suspensionCause"]) == ("active", None)


def test_one_change_waits_and_a_termination_excludes_it(api, db_conn, granted):
    acc, (first, second) = granted
    day = today(api) + timedelta(days=3)
    dated(api, acc, first, "suspended", day)
    other = dated(api, acc, first, "suspended", day + timedelta(days=1))
    assert other["state"] == "rejected" and errors(other) == ["openModifyItem"]
    now = api.post(ORDER, apidata.order_body(acc, status_item(first, "suspended"))).json()
    assert now["state"] == "rejected" and errors(now) == ["openModifyItem"]
    delete = api.post(ORDER, apidata.order_body(acc, apidata.delete_item(first["id"], "1"))).json()
    assert delete["state"] == "rejected" and errors(delete) == ["openModifyItem"]

    terminate(api, acc, second)
    ended = dated(api, acc, second, "suspended", day)
    assert ended["state"] == "rejected" and errors(ended) == ["productAlreadyEnded"]


def test_unchanged_is_checked_against_the_status_that_will_hold(api, db_conn, granted):
    acc, (first, _) = granted
    day = today(api) + timedelta(days=3)
    assert (
        api.post(ORDER, apidata.order_body(acc, status_item(first, "suspended"))).status_code == 201
    )
    same = dated(api, acc, first, "suspended", day)
    assert same["state"] == "rejected" and errors(same) == ["statusUnchanged"]
    resumed = dated(api, acc, first, "active", day)
    assert resumed["state"] == "inProgress"


def test_revalidation_on_the_day_fails_the_order(api, db_conn, granted):
    acc, (first, second) = granted
    assert (
        api.post(ORDER, apidata.order_body(acc, status_item(first, "suspended"))).status_code == 201
    )
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "active", day)
    assert api.patch(f"{apidata.ACCOUNT}/{acc['id']}", {"state": "Suspended"}).status_code == 200
    subscribe(api, "TMF622", "ProductOrderStateChangeEvent")

    tick(db_conn, at_dawn(day))
    body = api.get(order["href"]).json()
    assert body["state"] == "failed"
    assert errors(body) == ["accountNotActive"]
    assert [i["state"] for i in body["productOrderItem"]] == ["failed"]
    assert count(db_conn, "product_status_change") == 1  # the earlier, applied suspension
    api.clock.set(at_dawn(day))
    assert view(api, acc, first)["suspensionCause"] == "manual"
    assert command_of(db_conn, order)["result"] == {
        "outcome": "failed",
        "reasons": ["accountNotActive"],
    }
    events = [e["event"] for _, e in stored(db_conn, "TMF622")]
    assert [e["productOrder"]["state"] for e in events] == ["failed"]
    assert api.post(CANCEL, cancel_body(body)).status_code == 409


def test_a_product_that_ended_meanwhile_fails_the_order(api, db_conn, granted):
    acc, (first, _) = granted
    day = today(api) + timedelta(days=2)
    order = dated(api, acc, first, "suspended", day)
    db_conn.execute("UPDATE product SET status = 'terminated' WHERE id = %s", (first["id"],))
    db_conn.commit()
    tick(db_conn, at_dawn(day))
    body = api.get(order["href"]).json()
    assert body["state"] == "failed" and errors(body) == ["productAlreadyEnded"]
    assert count(db_conn, "product_status_change") == 0


@pytest.fixture
def numbered(api, db_conn, monkeypatch):
    from app.worker import loop

    monkeypatch.setattr(loop, "scheduled", lambda *args: SimpleNamespace(gap=None, dates=[]))
    off, acc = shop(api, db_conn)
    order = purchase(api, off, acc)
    assert tick(db_conn, api.clock.now()).prepaid_commands == [UUID(order["id"])]
    ids = [r[0] for r in db_conn.execute("SELECT id::text FROM product ORDER BY id").fetchall()]
    return acc, *({"id": i, "href": f"{apidata.PRODUCT}/{i}"} for i in ids)


def number_item(product, uid):
    return {
        "id": "1",
        "action": "modify",
        "product": {
            "id": product["id"],
            "@type": "ProductRefOrValue",
            "realizingResource": refs(uid),
        },
    }


@pytest.mark.parametrize("ahead", [0, 3])
def test_prepaid_number_only_change_runs_as_on_postpaid(api, db_conn, numbered, ahead):
    acc, product, other = numbered
    new = number(api, "12025550124")
    commands_before = count(db_conn, "prepaid_command")
    intervals = count(db_conn, "funded_interval")
    day = today(api) + timedelta(days=ahead)
    body = apidata.order_body(acc, number_item(product, new["id"]))
    body["requestedStartDate"] = timeline.iso(timeline.midnight(day))
    order = api.post(ORDER, body).json()
    assert order["state"] == "completed" and errors(order) == []
    assert count(db_conn, "prepaid_command") == commands_before
    assert count(db_conn, "funded_interval") == intervals
    assert db_conn.execute(
        "SELECT effective_from FROM number_assignment"
        " WHERE subscriber_number_id::text = %s AND cancelled_at IS NULL",
        (new["id"],),
    ).fetchone() == (day,)
    api.clock.set(timeline.midnight(day))
    assert api.get(product["href"]).json()["realizingResource"][0]["id"] == new["id"]


def test_number_change_mixed_with_a_purchase_is_still_refused(api, db_conn, numbered):
    acc, product, other = numbered
    new = number(api, "12025550125")
    mixed = api.post(
        ORDER,
        apidata.order_body(
            acc, number_item(product, new["id"]), apidata.delete_item(other["id"], "2")
        ),
    ).json()
    assert mixed["state"] == "rejected" and errors(mixed) == ["prepaidLifecycleUnavailable"]


def test_a_dated_number_change_can_be_cancelled_before_its_day(api, db_conn, numbered):
    acc, product, _ = numbered
    new = number(api, "12025550126")
    body = apidata.order_body(acc, number_item(product, new["id"]))
    body["requestedStartDate"] = timeline.iso(timeline.midnight(today(api) + timedelta(days=3)))
    order = api.post(ORDER, body).json()
    assert api.post(CANCEL, cancel_body(order)).status_code == 201
    assert api.get(order["href"]).json()["state"] == "cancelled"
