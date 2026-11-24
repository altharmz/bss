"""Customer self-care sign-in (issue #195): logins, codes, sessions and the scope rule.
docs/decisions/self-care.md."""
# ruff: noqa: F811

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.integrations.notify import adapter as sender
from app.main import app
from tests import apidata
from tests.builders.care.self_care import (  # noqa: F401 - fixtures
    BASE,
    EMAIL,
    PASSWORD,
    care,
    enabled,
    invite,
    last_code,
    log_sender,
    login_url,
    new_care,
    sign_in,
)


def active_customer(api, care, email=EMAIL):
    customer, _, code = invite(api, email)
    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204
    return customer


def audit(db_conn, action):
    rows = db_conn.execute(
        "SELECT operator, role, verification, customer_id FROM account_audit WHERE action = %s"
        " ORDER BY seq",
        (action,),
    ).fetchall()
    db_conn.commit()
    return rows


# --- the happy path ---


def test_invite_set_password_and_sign_in_with_a_code(api, care, db_conn):
    customer, login, code = invite(api, "Ada@Example.org")
    assert login["status"] == "invited" and login["email"] == EMAIL
    assert login["createdBy"] == "i.petrova"
    assert "password" not in str(login).lower() and code not in str(login)
    # the invitation went to the email; the message log keeps it with the code masked
    assert sender().sent[-1].to == EMAIL
    stored = db_conn.execute(
        "SELECT content, state, event_type FROM notification_message ORDER BY created_at DESC"
    ).fetchone()
    db_conn.commit()
    assert code not in stored[0] and "******" in stored[0]
    assert stored[1:] == ("completed", "care_verify_email")

    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204
    assert api.get(login_url(customer["id"])).json()["status"] == "active"

    started = care.post(BASE + "/session", {"email": "ADA@example.org", "password": PASSWORD})
    assert started.status_code == 202
    assert started.json()["codeExpiresAt"].startswith(
        (api.clock.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    )
    sign_in_code = last_code("sign-in")
    stored = db_conn.execute(
        "SELECT content FROM notification_message WHERE event_type = 'care_sign_in_code'"
    ).fetchone()
    db_conn.commit()
    assert sign_in_code not in stored[0]

    verified = care.post(BASE + "/session/verify", {"email": EMAIL, "code": sign_in_code})
    assert verified.status_code == 200, verified.text
    assert verified.json()["customer"]["id"] == customer["id"]
    cookie = verified.headers["set-cookie"]
    assert "bss_care_session=" in cookie and "HttpOnly" in cookie
    assert "Path=/self-care/" in cookie and "SameSite=strict" in cookie
    assert care.get(BASE + "/session").json()["email"] == EMAIL
    assert care.get(f"{BASE}/customer/{customer['id']}").json()["id"] == customer["id"]
    # only the token's hash is stored
    token = care.client.cookies["bss_care_session"]
    (stored_hash,) = db_conn.execute("SELECT token_hash FROM customer_session").fetchone()
    db_conn.commit()
    assert token not in stored_hash and len(stored_hash) == 64

    assert [(*r[:3], str(r[3])) for r in audit(db_conn, "care_password_changed")] == [
        ("self-care", "customer", "code", customer["id"])
    ]
    assert [r[:3] for r in audit(db_conn, "care_sign_in")] == [
        ("self-care", "customer", "password+code")
    ]
    assert api.get(login_url(customer["id"])).json()["activeSessions"] == 1


# --- refusals ---


def test_wrong_password_unknown_and_not_activated_logins_look_the_same(api, care):
    customer, _, code = invite(api, "new@example.org")  # invited, no password yet
    active_customer(api, care)
    for email, password in (
        (EMAIL, "wrong-password-123"),
        ("nobody@example.org", PASSWORD),
        ("new@example.org", PASSWORD),
    ):
        response = care.post(BASE + "/session", {"email": email, "password": password})
        assert response.status_code == 401
        assert response.json()["code"] == "invalidCredentials"
    assert sender().sent[-1].subject != "Your sign-in code"


@pytest.mark.parametrize("case", ["wrong", "expired", "used", "spent", "superseded"])
def test_a_wrong_expired_used_or_spent_code_opens_no_session(api, care, case):
    active_customer(api, care)
    assert care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD}).status_code == 202
    code = last_code("sign-in")
    wrong = "000000" if code != "000000" else "111111"
    if case == "wrong":
        code = wrong
    elif case == "expired":
        api.clock.set(api.clock.now() + timedelta(minutes=10))
    elif case == "used":
        assert (
            care.post(BASE + "/session/verify", {"email": EMAIL, "code": code}).status_code == 200
        )
    elif case == "spent":
        for _ in range(5):
            assert (
                care.post(BASE + "/session/verify", {"email": EMAIL, "code": wrong}).status_code
                == 401
            )
    elif case == "superseded":
        assert (
            care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD}).status_code == 202
        )
    response = care.post(BASE + "/session/verify", {"email": EMAIL, "code": code})
    assert response.status_code == 401
    assert response.json()["code"] == "codeInvalid"


def test_four_wrong_codes_leave_the_right_one_usable(api, care):
    active_customer(api, care)
    assert care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD}).status_code == 202
    code = last_code("sign-in")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert (
            care.post(BASE + "/session/verify", {"email": EMAIL, "code": wrong}).status_code == 401
        )
    assert care.post(BASE + "/session/verify", {"email": EMAIL, "code": code}).status_code == 200


def test_ten_wrong_passwords_lock_the_login_for_fifteen_minutes(api, care, db_conn):
    customer = active_customer(api, care)
    db_conn.execute("DELETE FROM sign_in_attempt")
    db_conn.commit()
    for attempt in range(10):
        response = care.post(BASE + "/session", {"email": EMAIL, "password": "wrong-password-1"})
        assert response.status_code == 401, attempt
    login = api.get(login_url(customer["id"])).json()
    assert login["status"] == "locked" and login["failedAttempts"] == 0
    # locked: even the right password is refused, and not counted
    locked = care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD})
    assert locked.status_code == 403 and locked.json()["code"] == "loginLocked"
    assert [r[:3] for r in audit(db_conn, "care_locked")] == [("self-care", "customer", "none")]
    api.clock.set(api.clock.now() + timedelta(minutes=15))
    assert sign_in(care).status_code == 200
    assert api.get(login_url(customer["id"])).json()["failedAttempts"] == 0


def test_a_right_password_resets_the_wrong_count(api, care):
    customer = active_customer(api, care)
    for _ in range(9):
        care.post(BASE + "/session", {"email": EMAIL, "password": "wrong-password-1"})
    assert api.get(login_url(customer["id"])).json()["failedAttempts"] == 9
    assert sign_in(care).status_code == 200
    assert api.get(login_url(customer["id"])).json()["failedAttempts"] == 0


def test_sign_in_is_throttled_with_the_operator_throttle(api, care, db_conn):
    active_customer(api, care)
    db_conn.execute("DELETE FROM sign_in_attempt")
    db_conn.commit()
    for _ in range(20):
        care.post(BASE + "/session", {"email": "x@example.org", "password": "wrong-password-1"})
    response = care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    (keys,) = db_conn.execute(
        "SELECT count(*) FROM sign_in_attempt WHERE key LIKE 'user|care:%%'"
    ).fetchone()
    db_conn.commit()
    assert keys == 20


def test_a_disabled_login_cannot_sign_in_and_its_sessions_end(api, care, db_conn):
    customer = active_customer(api, care)
    assert sign_in(care).status_code == 200
    other = new_care(api)
    assert sign_in(other).status_code == 200
    disabled = api.delete(login_url(customer["id"]))
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled" and disabled.json()["activeSessions"] == 0
    assert care.get(BASE + "/session").status_code == 401
    assert other.get(BASE + "/session").status_code == 401
    response = care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 401
    # a reset asks for nothing on a disabled login
    sent = len(sender().sent)
    assert care.post(BASE + "/password/reset", {"email": EMAIL}).status_code == 204
    assert len(sender().sent) == sent


def test_a_code_that_was_not_sent_is_a_503_and_logged_failed(api, care, db_conn):
    customer = apidata.customer(api)
    response = api.post(login_url(customer["id"]), {"email": "fail@example.test"})
    assert response.status_code == 503 and response.json()["code"] == "codeNotSent"
    (state,) = db_conn.execute("SELECT state FROM notification_message").fetchone()
    db_conn.commit()
    assert state == "failed"
    # the invitation can be sent again
    sender().recover("fail@example.test")
    assert api.post(login_url(customer["id"]), {"email": "fail@example.test"}).status_code == 201


# --- feature switch ---


def test_self_care_off_answers_404_everywhere(api, care, monkeypatch):
    customer = active_customer(api, care)
    assert sign_in(care).status_code == 200
    monkeypatch.setattr(settings, "self_care_enabled", False)
    for method, path, body in (
        ("POST", "/session", {"email": EMAIL, "password": PASSWORD}),
        ("POST", "/session/verify", {"email": EMAIL, "code": "123456"}),
        ("GET", "/session", None),
        ("DELETE", "/session", None),
        ("POST", "/password", {"code": "x", "newPassword": PASSWORD}),
        ("POST", "/password/reset", {"email": EMAIL}),
        ("GET", f"/customer/{customer['id']}", None),
    ):
        response = care.request(method, BASE + path, json=body)
        assert response.status_code == 404, (method, path)
    # no invitation either; the operator can still see and disable a login
    other = apidata.customer(api)
    refused = api.post(login_url(other["id"]), {"email": "b@example.org"})
    assert refused.status_code == 409
    assert api.get(login_url(customer["id"])).status_code == 200
    assert api.delete(login_url(customer["id"])).status_code == 200


# --- the scope rule ---


def test_a_session_reads_only_its_own_customer(api, care):
    mine = active_customer(api, care)
    theirs = active_customer(api, new_care(api), "bob@example.org")
    assert sign_in(care).status_code == 200
    assert care.get(f"{BASE}/customer/{mine['id']}").status_code == 200
    for other in (theirs["id"], "00000000-0000-0000-0000-000000000000"):
        response = care.get(f"{BASE}/customer/{other}")
        assert response.status_code == 404, other
    assert care.get(f"{BASE}/customer/not-a-uuid").status_code == 404


def test_a_customer_session_reaches_no_operator_or_tmf_api(api, care):
    active_customer(api, care)
    assert sign_in(care).status_code == 200
    raw = care.client
    for path in (
        "/internal/v1/context",
        "/internal/v1/session",
        "/tmf-api/customer/v5/customer",
        "/tmf-api/accountManagement/v5/billingAccount",
    ):
        assert raw.get(path).status_code == 401, path
    # even with the cookie sent on every path, not only /self-care/
    token = raw.cookies["bss_care_session"]
    for path in ("/internal/v1/context", "/tmf-api/customer/v5/customer"):
        response = raw.get(path, cookies={"bss_care_session": token, "bss_session": token})
        assert response.status_code == 401, path


def test_an_operator_session_or_service_credential_is_no_customer_session(api):
    raw = api.client  # signed in as the operator
    assert raw.get(BASE + "/session").status_code == 401
    service = {"X-Bss-Service": "test-service-credential", "X-Remote-User": "mcp-token:demo"}
    assert raw.get(BASE + "/session", headers=service).status_code == 401


def test_writes_need_the_csrf_header(api, care):
    active_customer(api, care)
    bare = TestClient(app, raise_server_exceptions=False)
    response = bare.post(BASE + "/session", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 403


# --- sessions ---


def test_idle_expiry_under_the_injected_clock(api, care):
    active_customer(api, care)
    assert sign_in(care).status_code == 200
    api.clock.set(api.clock.now() + timedelta(seconds=30 * 60))
    assert care.get(BASE + "/session").status_code == 200  # within idle + 60 s slack
    api.clock.set(api.clock.now() + timedelta(seconds=30 * 60 + 61))
    assert care.get(BASE + "/session").status_code == 401


def test_absolute_expiry_under_the_injected_clock(api, care):
    active_customer(api, care)
    assert sign_in(care).status_code == 200
    for _ in range(23):  # active all along: never idle
        api.clock.set(api.clock.now() + timedelta(minutes=29))
        assert care.get(BASE + "/session").status_code == 200
    api.clock.set(api.clock.now() + timedelta(hours=12) - timedelta(minutes=23 * 29))
    assert care.get(BASE + "/session").status_code == 401


def test_sign_out_revokes_the_session(api, care, db_conn):
    active_customer(api, care)
    assert sign_in(care).status_code == 200
    token = care.client.cookies["bss_care_session"]
    assert care.delete(BASE + "/session").status_code == 204
    replay = new_care(api)
    replay.client.cookies.set("bss_care_session", token, path="/self-care/")
    assert replay.get(BASE + "/session").status_code == 401
    (revoked,) = db_conn.execute("SELECT revoked_at FROM customer_session").fetchone()
    db_conn.commit()
    assert revoked == api.clock.now()


# --- passwords ---


def test_reset_password_by_code_ends_every_session(api, care, db_conn):
    active_customer(api, care)
    assert sign_in(care).status_code == 200
    assert care.post(BASE + "/password/reset", {"email": EMAIL}).status_code == 204
    code = last_code("link")
    assert sender().sent[-1].subject == "Reset your self-care password"
    assert (
        care.post(
            BASE + "/password", {"code": code, "newPassword": "another-long-pass"}
        ).status_code
        == 204
    )
    assert care.get(BASE + "/session").status_code == 401
    # the code is single-use; the old password is gone
    again = care.post(BASE + "/password", {"code": code, "newPassword": "yet-another-pass"})
    assert again.status_code == 401
    assert care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert sign_in(care, password="another-long-pass").status_code == 200
    assert [r[2] for r in audit(db_conn, "care_password_changed")] == ["code", "code"]


def test_reset_for_an_unknown_email_looks_the_same_and_sends_nothing(api, care):
    assert care.post(BASE + "/password/reset", {"email": "nobody@example.org"}).status_code == 204
    assert sender().sent == []


def test_reset_before_activation_sends_a_new_invitation_code(api, care):
    customer, _, first = invite(api)
    api.clock.set(api.clock.now() + timedelta(hours=24))
    expired = care.post(BASE + "/password", {"code": first, "newPassword": PASSWORD})
    assert expired.status_code == 401
    assert care.post(BASE + "/password/reset", {"email": EMAIL}).status_code == 204
    assert sender().sent[-1].subject == "Set your self-care password"
    code = last_code("link")
    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204
    assert api.get(login_url(customer["id"])).json()["status"] == "active"


@pytest.mark.parametrize("password", ["short-pass", EMAIL, EMAIL.upper()])
def test_the_operator_password_policy_applies(api, care, password):
    _, _, code = invite(api)
    response = care.post(BASE + "/password", {"code": code, "newPassword": password})
    assert response.status_code == 422
    # the code survives a refused password
    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204


# --- operator side ---


def test_invite_refusals(api, care):
    customer = active_customer(api, care)
    again = api.post(login_url(customer["id"]), {"email": "other@example.org"})
    assert again.status_code == 409  # active: disable it first
    other = apidata.customer(api)
    taken = api.post(login_url(other["id"]), {"email": EMAIL.upper()})
    assert taken.status_code == 409
    assert api.post(login_url(other["id"]), {"email": "not-an-email"}).status_code == 422
    assert api.post(login_url(other["id"]), {"email": "a@b.c", "x": 1}).status_code == 400
    missing = "00000000-0000-0000-0000-000000000000"
    assert api.post(login_url(missing), {"email": "a@b.c"}).status_code == 404
    assert api.get(login_url(other["id"])).status_code == 404
    assert api.delete(login_url(other["id"])).status_code == 404


def test_reinviting_a_disabled_login_starts_it_afresh(api, care):
    customer = active_customer(api, care)
    assert api.delete(login_url(customer["id"])).status_code == 200
    _, login, code = invite(api, "ada.new@example.org", customer)
    assert login["status"] == "invited" and login["email"] == "ada.new@example.org"
    assert care.post(BASE + "/session", {"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert care.post(BASE + "/password", {"code": code, "newPassword": PASSWORD}).status_code == 204
    assert sign_in(care, "ada.new@example.org").status_code == 200


def test_self_care_entries_raise_no_security_alert(api, care, db_conn):
    from app.notify import events

    active_customer(api, care)
    assert sign_in(care).status_code == 200
    since = api.clock.now() - timedelta(days=1)
    assert events._security(db_conn, since) == []
    db_conn.commit()
