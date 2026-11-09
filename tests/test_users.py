"""Authentication boundary and user lifecycle through real sessions and database."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE, OPEN, authenticate, digest
from app.config import settings
from app.main import app
from app.routes import routes
from tests import contract

SESSION = "/internal/v1/session"
USERS = "/internal/v1/users"
PASSWORD = "test-password-123"


def login(client, username="i.petrova", password=PASSWORD):
    return client.post(SESSION, json={"username": username, "password": password})


@pytest.fixture
def client(api):
    with TestClient(app, headers={"X-Bss-Request": "1"}, raise_server_exceptions=False) as raw:
        yield raw


def create(client, username="alice", role="user"):
    response = client.post(USERS, json={"username": username, "role": role, "password": PASSWORD})
    assert response.status_code == 201, response.text
    assert "password" not in response.text
    contract.assert_valid("INTERNAL", "User", response.json())
    return response.json()


def activate(client, username="alice"):
    assert login(client, username).status_code == 200
    response = client.post(
        SESSION + "/password",
        json={
            "currentPassword": PASSWORD,
            "newPassword": "replacement-password",
        },
    )
    assert response.status_code == 204, response.text
    assert login(client, username, "replacement-password").status_code == 200


def test_every_route_inherits_authentication():
    for route, dependencies in routes(app):
        assert authenticate in {dep.dependency for dep in dependencies}, route.path
    assert OPEN == {
        ("GET", "/"),
        ("GET", "/healthz"),
        ("GET", "/billing-status"),
        ("POST", SESSION),
    }


def test_metrics_is_token_protected_not_open(client, monkeypatch):
    # never in OPEN: authenticate() answers it with the bearer-token rule, not a session
    assert ("GET", "/metrics") not in OPEN
    assert client.get("/metrics").status_code == 404  # disabled by default
    monkeypatch.setattr(settings, "bss_metrics_token", "t0ken")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer t0ken"}).status_code == 200
    # a session cookie does not open it
    login(client)
    assert client.get("/metrics").status_code == 401
    assert client.post("/metrics").status_code in {401, 405}


def test_new_routes_also_inherit_authentication(client):
    before = len(app.routes)
    try:
        app.add_api_route("/auth-probe", lambda: {"unsafe": True})
        assert client.get("/auth-probe").status_code == 401
    finally:
        del app.routes[before:]
        app.openapi_schema = None


def test_cookie_identity_and_hash_only_storage(client, db_conn):
    assert client.get("/internal/v1/context", headers={"X-Remote-User": "admin"}).status_code == 401
    response = login(client)
    assert response.status_code == 200, response.text
    contract.assert_valid("INTERNAL", "Session", response.json())
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    token = client.cookies.get(COOKIE)
    assert (
        db_conn.execute(
            "SELECT count(*) FROM app_session WHERE token_hash = %s", (digest(token),)
        ).fetchone()[0]
        == 1
    )
    assert (
        db_conn.execute(
            "SELECT count(*) FROM app_session WHERE token_hash = %s", (token,)
        ).fetchone()[0]
        == 0
    )
    assert (
        client.get("/internal/v1/context", headers={"X-Remote-User": "forged"}).json()["operator"]
        == "i.petrova"
    )
    assert client.get(SESSION).json() == {
        "username": "i.petrova",
        "role": "admin",
        "mustChangePassword": False,
    }
    assert client.delete(SESSION).status_code == 204
    assert client.get(SESSION).status_code == 401


def test_secure_cookie_outside_dev(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "staging")
    assert "Secure" in login(client).headers["set-cookie"]


def test_csrf_header_required_on_login_and_session_writes(client):
    client.headers.pop("X-Bss-Request")
    assert login(client).status_code == 403
    client.headers["X-Bss-Request"] = "1"
    assert login(client).status_code == 200
    client.headers.pop("X-Bss-Request")
    assert client.delete(SESSION).status_code == 403
    assert client.get(SESSION).status_code == 200
    assert client.post(USERS, json={}).status_code == 403


@pytest.mark.parametrize("username,password", [("missing", PASSWORD), ("i.petrova", "wrong")])
def test_invalid_passwords_are_indistinguishable(client, username, password):
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json()["message"] == "invalid username or password"


def forwarded(client, value, username="i.petrova", password=PASSWORD):
    return client.post(
        SESSION,
        json={"username": username, "password": password},
        headers={"X-Forwarded-For": value},
    )


def clear_attempts(conn):
    conn.execute("DELETE FROM sign_in_attempt")
    conn.commit()


def test_rate_limit_by_address_across_usernames(client, api, db_conn):
    clear_attempts(db_conn)  # The API fixture has already performed its own sign-in.
    for _ in range(20):
        assert login(client, "missing", "wrong").status_code == 401
    assert login(client, "another", "wrong").status_code == 429
    api.clock.set(api.clock.now() + timedelta(seconds=61))
    assert login(client).status_code == 200


def test_rate_limit_does_not_lock_a_user_out_from_elsewhere(client, monkeypatch, db_conn):
    clear_attempts(db_conn)
    monkeypatch.setattr(settings, "app_trust_forwarded", True)
    for _ in range(20):
        assert forwarded(client, "1.1.1.1", password="wrong").status_code == 401
    assert forwarded(client, "1.1.1.1").status_code == 429
    assert forwarded(client, "2.2.2.2").status_code == 200


def test_rate_limit_keys_on_the_last_forwarded_entry(client, monkeypatch, db_conn):
    clear_attempts(db_conn)
    monkeypatch.setattr(settings, "app_trust_forwarded", True)
    # Caddy replaces the header; a client's own entries could only come first
    for i in range(20):
        response = forwarded(client, f"10.0.0.{i}, 5.6.7.8", f"u{i}", "wrong")
        assert response.status_code == 401
    assert forwarded(client, "5.6.7.8", "other", "wrong").status_code == 429
    assert forwarded(client, "5.6.7.8, 9.9.9.9").status_code == 200


def test_rate_limit_ignores_forwarded_unless_trusted(client, db_conn):
    clear_attempts(db_conn)
    for i in range(20):
        assert forwarded(client, f"10.0.0.{i}", f"u{i}", "wrong").status_code == 401
    assert forwarded(client, "10.0.1.1").status_code == 429


@pytest.mark.parametrize("seconds", [8 * 3600, 7 * 86400])
def test_session_expiry(client, api, seconds):
    assert login(client).status_code == 200
    if seconds > 8 * 3600:
        for _ in range(23):
            api.clock.set(api.clock.now() + timedelta(hours=7))
            assert client.get(SESSION).status_code == 200
        seconds -= 23 * 7 * 3600
    api.clock.set(api.clock.now() + timedelta(seconds=seconds))
    assert client.get(SESSION).status_code == 401


def test_must_change_password_and_user_role(client):
    assert login(client).status_code == 200
    create(client)
    assert login(client, "alice").json()["mustChangePassword"] is True
    assert client.get("/internal/v1/context").status_code == 403
    assert client.get(USERS).status_code == 403
    activate(client)
    assert client.get("/internal/v1/context").status_code == 200
    assert client.get(USERS).status_code == 403
    assert client.post(USERS, json={}).status_code == 403
    assert client.patch(USERS + "/i.petrova", json={"role": "user"}).status_code == 403
    # Regular users retain the ordinary TMF write capabilities.
    response = client.post(
        "/tmf-api/partyManagement/v5/individual",
        json={
            "givenName": "Alice",
            "familyName": "Example",
        },
    )
    assert response.status_code == 201, response.text


def test_disable_reset_and_audit(client, db_conn):
    assert login(client).status_code == 200
    create(client)
    with TestClient(app, headers={"X-Bss-Request": "1"}) as alice:
        activate(alice)
        assert client.patch(USERS + "/alice", json={"disabled": True}).status_code == 200
        assert alice.get(SESSION).status_code == 401
        assert login(alice, "alice", "replacement-password").status_code == 401
        assert client.patch(USERS + "/alice", json={"disabled": False}).status_code == 200
        assert login(alice, "alice", "replacement-password").status_code == 200
        assert (
            client.patch(USERS + "/alice", json={"password": "another-password"}).status_code == 200
        )
        assert alice.get(SESSION).status_code == 401
        assert login(alice, "alice", "another-password").json()["mustChangePassword"] is True
    rows = db_conn.execute(
        "SELECT operator, action FROM app_user_audit WHERE username = 'alice'"
    ).fetchall()
    assert sorted(rows) == sorted(
        [
            ("i.petrova", "create"),
            ("alice", "changePassword"),
            ("i.petrova", "disabled"),
            ("i.petrova", "disabled"),
            ("i.petrova", "resetPassword"),
        ]
    )
    users = client.get(USERS).json()
    assert {u["username"] for u in users} == {"i.petrova", "alice"}
    for user in users:
        contract.assert_valid("INTERNAL", "User", user)


def test_concurrent_demotions_keep_an_active_admin(client):
    from concurrent.futures import ThreadPoolExecutor

    assert login(client).status_code == 200
    create(client, "admin2", "admin")
    with TestClient(app, headers={"X-Bss-Request": "1"}) as other:
        activate(other, "admin2")

        def demote(raw, username):
            return raw.patch(USERS + "/" + username, json={"role": "user"}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(demote, client, "i.petrova")
            second = pool.submit(demote, other, "admin2")
            assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [200, 409]


def test_duplicate_unknown_and_empty_user_updates(client):
    assert login(client).status_code == 200
    create(client)
    assert (
        client.post(
            USERS,
            json={
                "username": "alice",
                "role": "user",
                "password": PASSWORD,
            },
        ).status_code
        == 409
    )
    assert client.patch(USERS + "/missing", json={"disabled": True}).status_code == 404
    for body, status in [
        ({}, 422),
        ({"role": None}, 422),
        ({"disabled": "yes"}, 422),
        ({"extra": True}, 400),
        ({"password": "short"}, 422),
    ]:
        assert client.patch(USERS + "/alice", json=body).status_code == status


def test_last_admin_and_self_disable(client):
    assert login(client).status_code == 200
    assert client.patch(USERS + "/i.petrova", json={"role": "user"}).status_code == 409
    assert client.patch(USERS + "/i.petrova", json={"disabled": True}).status_code == 409
    create(client, "admin2", "admin")
    response = client.patch(USERS + "/i.petrova", json={"role": "user"})
    assert response.status_code == 200
    assert client.get(USERS).status_code == 401  # own session was revoked too


def test_password_change_revokes_all_sessions(client):
    assert login(client).status_code == 200
    with TestClient(app, headers={"X-Bss-Request": "1"}) as other:
        assert login(other).status_code == 200
        response = client.post(
            SESSION + "/password",
            json={
                "currentPassword": "wrong",
                "newPassword": "new-password-123",
            },
        )
        assert response.status_code == 403
        assert other.get(SESSION).status_code == 200
        response = client.post(
            SESSION + "/password",
            json={
                "currentPassword": PASSWORD,
                "newPassword": "new-password-123",
            },
        )
        assert response.status_code == 204
        assert other.get(SESSION).status_code == 401
        assert client.get(SESSION).status_code == 401
        assert login(client, password=PASSWORD).status_code == 401
        assert login(client, password="new-password-123").status_code == 200


def test_disabled_user_is_checked_on_every_request(client, db_conn):
    assert login(client).status_code == 200
    db_conn.execute("UPDATE app_user SET disabled = true WHERE username = 'i.petrova'")
    db_conn.commit()
    assert client.get(SESSION).status_code == 401
    assert login(client).status_code == 401


@pytest.mark.parametrize(
    "body,status",
    [
        ({"username": "bad user", "role": "user", "password": PASSWORD}, 422),
        ({"username": "alice", "role": "user", "password": "short"}, 422),
        ({"username": "long-username", "role": "user", "password": "long-username"}, 422),
        ({"username": "alice", "role": "owner", "password": PASSWORD}, 422),
        ({"username": "alice", "role": "user", "password": PASSWORD, "extra": True}, 400),
    ],
)
def test_user_input(client, body, status):
    assert login(client).status_code == 200
    assert client.post(USERS, json=body).status_code == status


def test_service_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, "bss_smoke_secret", "test-smoke-credential")
    mcp = {"X-Bss-Service": "test-service-credential", "X-Remote-User": "mcp-token:demo"}
    assert client.get("/internal/v1/context", headers=mcp).json()["operator"] == "mcp-token:demo"
    assert client.get(USERS, headers=mcp).status_code == 403
    assert client.get(SESSION, headers=mcp).status_code == 403
    assert (
        client.get("/internal/v1/context", headers={**mcp, "X-Remote-User": "admin"}).status_code
        == 403
    )
    assert (
        client.get("/internal/v1/context", headers={**mcp, "X-Bss-Service": "wrong"}).status_code
        == 401
    )
    smoke = {"X-Bss-Service": "test-smoke-credential"}
    assert client.get("/internal/v1/context", headers=smoke).status_code == 200
    assert (
        client.post("/tmf-api/partyManagement/v5/individual", headers=smoke, json={}).status_code
        == 403
    )
    monkeypatch.setattr(settings, "bss_service_secret", "")
    assert client.get("/internal/v1/context", headers=mcp).status_code == 401


def test_no_interactive_api_docs(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --- the MCP login backend and credential versions (issue #146) ---

CHECK = "/internal/v1/service/credential-check"
SERVICE = {"X-Bss-Service": "test-service-credential"}
REPLACED = "replacement-password"


def check(client, username, password, headers=SERVICE):
    return client.post(CHECK, json={"username": username, "password": password}, headers=headers)


def version(db_conn, username="alice"):
    db_conn.rollback()  # a fresh snapshot
    return db_conn.execute(
        "SELECT credential_version FROM app_user WHERE username = %s", (username,)
    ).fetchone()[0]


def as_mcp(client, username="alice", at=1, path="/internal/v1/context"):
    headers = {**SERVICE, "X-Remote-User": f"mcp:{username}", "X-Bss-Credential-Version": str(at)}
    return client.get(path, headers=headers)


def test_credential_check_accepts_an_active_mcp_users_password(client, db_conn):
    assert login(client).status_code == 200
    create(client, role="mcp")  # no forced change: it keeps the administrator's password
    client.cookies.clear()
    response = check(client, "alice", PASSWORD)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "username": "alice",
        "role": "mcp",
        "credentialVersion": version(db_conn),
    }
    contract.assert_valid("INTERNAL", "CredentialCheck", response.json())
    db_conn.rollback()
    last = db_conn.execute("SELECT last_login_at FROM app_user WHERE username='alice'").fetchone()
    assert last[0] is not None


def test_credential_check_does_not_tell_unknown_wrong_disabled_and_ui_roles_apart(client):
    assert login(client).status_code == 200
    create(client, role="mcp")
    create(client, "bob", role="mcp")
    assert client.patch(f"{USERS}/bob", json={"disabled": True}).status_code == 200
    create(client, "carol")
    client.cookies.clear()
    activate(client, "carol")  # an active `user` with a password of its own
    client.cookies.clear()
    answers = [
        check(client, "nobody", PASSWORD),
        check(client, "alice", "wrong-password-123"),
        check(client, "bob", PASSWORD),
        check(client, "i.petrova", PASSWORD),  # the right password of an admin
        check(client, "carol", REPLACED),  # the right password of a user
    ]
    assert {a.status_code for a in answers} == {401}
    assert {a.json()["code"] for a in answers} == {"invalidCredentials"}
    assert len({a.json()["message"] for a in answers}) == 1


def test_credential_check_refuses_a_ui_user_who_must_change_the_password_as_401(client):
    # passwordChangeRequired is gone: an `mcp` user is never flagged, a UI role never passes
    assert login(client).status_code == 200
    create(client)  # a `user`, created with must_change_password
    client.cookies.clear()
    response = check(client, "alice", PASSWORD)
    assert response.status_code == 401
    assert response.json()["code"] == "invalidCredentials"


def test_credential_check_is_for_the_mcp_service_credential_only(client, monkeypatch):
    monkeypatch.setattr(settings, "bss_smoke_secret", "test-smoke-credential")
    assert check(client, "i.petrova", PASSWORD, headers={}).status_code == 401
    assert check(client, "i.petrova", PASSWORD, {"X-Bss-Service": "wrong"}).status_code == 401
    smoke = {"X-Bss-Service": "test-smoke-credential"}
    assert check(client, "i.petrova", PASSWORD, smoke).status_code == 403
    assert login(client).status_code == 200  # a session, with the CSRF header the client sends
    create(client, role="mcp")
    assert check(client, "alice", PASSWORD, headers={}).status_code == 403
    client.cookies.clear()
    assert check(client, "alice", PASSWORD).status_code == 200


def test_credential_check_is_rate_limited(client):
    # the fixture's own sign-in already counted once: the limit is 20 a minute per address
    statuses = [check(client, "i.petrova", "wrong-password-123").status_code for _ in range(21)]
    assert statuses[:19] == [401] * 19 and set(statuses[19:]) == {429}


def test_the_credential_version_changes_with_each_revoking_event(client, db_conn):
    assert login(client).status_code == 200
    create(client)
    seen = [version(db_conn)]
    assert client.patch(f"{USERS}/alice", json={"role": "admin"}).status_code == 200
    seen.append(version(db_conn))
    assert (
        client.patch(f"{USERS}/alice", json={"password": "another-password-1"}).status_code == 200
    )
    seen.append(version(db_conn))
    assert client.patch(f"{USERS}/alice", json={"disabled": True}).status_code == 200
    seen.append(version(db_conn))
    assert client.patch(f"{USERS}/alice", json={"disabled": False}).status_code == 200
    seen.append(version(db_conn))
    client.cookies.clear()
    assert login(client, "alice", "another-password-1").status_code == 200
    changed = client.post(
        SESSION + "/password",
        json={"currentPassword": "another-password-1", "newPassword": REPLACED},
    )
    assert changed.status_code == 204
    seen.append(version(db_conn))
    assert seen == sorted(set(seen)) and len(seen) == 6  # strictly increasing


def test_an_mcp_user_principal_needs_its_current_credential_version(client, db_conn):
    assert login(client).status_code == 200
    create(client, role="mcp")
    at = version(db_conn)
    ok = as_mcp(client, at=at)
    assert ok.status_code == 200 and ok.json()["operator"] == "mcp:alice"
    stale = as_mcp(client, at=at - 1)
    assert stale.status_code == 401 and stale.json()["code"] == "credentialRevoked"
    assert as_mcp(client, "nobody", at=1).json()["code"] == "credentialRevoked"
    headers = {**SERVICE, "X-Remote-User": "mcp:alice"}  # no version at all
    assert client.get("/internal/v1/context", headers=headers).status_code == 403
    for bad in ("x", "-1", "1.0", " 1", ""):
        assert as_mcp(client, at=bad).status_code == 403, bad
    assert as_mcp(client, "bad name", at=at).status_code == 403
    assert client.patch(f"{USERS}/alice", json={"disabled": True}).status_code == 200
    gone = as_mcp(client, at=version(db_conn))  # the current version, but disabled
    assert gone.status_code == 401 and gone.json()["code"] == "credentialRevoked"


def test_a_static_token_principal_is_not_checked_against_users(client):
    headers = {**SERVICE, "X-Remote-User": "mcp-token:ops-bot"}
    response = client.get("/internal/v1/context", headers=headers)
    assert response.status_code == 200 and response.json()["operator"] == "mcp-token:ops-bot"
    for bad in ("mcp-token:Upper", "mcp-token:", "mcp-token:" + "a" * 49, "other:x", ""):
        assert (
            client.get(
                "/internal/v1/context", headers={**SERVICE, "X-Remote-User": bad}
            ).status_code
            == 403
        ), bad


def test_the_longest_mcp_user_principal_is_a_valid_operator(client, db_conn):
    assert login(client).status_code == 200
    name = "u" * 64
    create(client, name, role="mcp")
    assert as_mcp(client, name, at=version(db_conn, name)).json()["operator"] == "mcp:" + name


# --- role mcp: the only role for MCP connections (issue #173) ---


def must_change(db_conn, username="alice"):
    db_conn.rollback()
    return db_conn.execute(
        "SELECT must_change_password FROM app_user WHERE username = %s", (username,)
    ).fetchone()[0]


def test_an_mcp_user_cannot_sign_in_to_the_ui_like_a_wrong_password(client):
    assert login(client).status_code == 200
    create(client, role="mcp")
    client.cookies.clear()
    refused = login(client, "alice", PASSWORD)  # the right password
    wrong = login(client, "alice", "wrong-password-123")
    assert refused.status_code == wrong.status_code == 401
    assert refused.json() == wrong.json()
    assert "set-cookie" not in refused.headers


def test_a_session_of_a_user_who_became_mcp_is_not_accepted(client, db_conn):
    assert login(client).status_code == 200
    create(client)
    client.cookies.clear()
    activate(client)  # alice holds a session
    assert client.get(SESSION).status_code == 200
    # the role changed behind the API's back (the API also deletes sessions): second line
    db_conn.execute(
        "UPDATE app_user SET role = 'mcp', must_change_password = false WHERE username = 'alice'"
    )
    db_conn.commit()
    assert client.get(SESSION).status_code == 401


def test_a_role_change_away_from_mcp_ends_a_live_grant(client, db_conn):
    assert login(client).status_code == 200
    create(client, role="mcp")
    assert as_mcp(client, at=version(db_conn)).status_code == 200
    changed = client.patch(f"{USERS}/alice", json={"role": "user", "password": REPLACED})
    assert changed.status_code == 200
    gone = as_mcp(client, at=version(db_conn))  # even with the new version: not an mcp user
    assert gone.status_code == 401 and gone.json()["code"] == "credentialRevoked"


def test_the_role_alone_ends_a_grant_without_a_version_change(client, db_conn):
    assert login(client).status_code == 200
    create(client, role="mcp")
    at = version(db_conn)
    db_conn.execute("UPDATE app_user SET role = 'user' WHERE username = 'alice'")
    db_conn.commit()
    gone = as_mcp(client, at=at)
    assert gone.status_code == 401 and gone.json()["code"] == "credentialRevoked"


def test_must_change_password_follows_the_role(client, db_conn):
    assert login(client).status_code == 200
    assert create(client, role="mcp")["mustChangePassword"] is False
    reset = client.patch(f"{USERS}/alice", json={"password": REPLACED})
    assert reset.status_code == 200 and reset.json()["mustChangePassword"] is False
    to_ui = client.patch(f"{USERS}/alice", json={"role": "user", "password": PASSWORD})
    assert to_ui.status_code == 200 and to_ui.json()["mustChangePassword"] is True
    create(client, "bob")
    client.cookies.clear()
    activate(client, "bob")
    client.cookies.clear()
    assert login(client).status_code == 200
    assert must_change(db_conn, "bob") is False
    to_mcp = client.patch(f"{USERS}/bob", json={"role": "mcp", "password": PASSWORD})
    assert to_mcp.status_code == 200 and to_mcp.json()["mustChangePassword"] is False
    assert create(client, "carol", role="admin")["mustChangePassword"] is True


@pytest.mark.parametrize(("start", "target"), [("user", "mcp"), ("admin", "mcp"), ("mcp", "user")])
def test_a_role_change_across_mcp_needs_a_new_password(client, db_conn, start, target):
    assert login(client).status_code == 200
    create(client, role=start)
    at = version(db_conn)
    refused = client.patch(f"{USERS}/alice", json={"role": target})
    assert refused.status_code == 422, refused.text
    db_conn.rollback()
    row = db_conn.execute("SELECT role FROM app_user WHERE username = 'alice'").fetchone()
    assert row[0] == start and version(db_conn) == at  # nothing changed


def test_a_role_change_within_the_ui_roles_needs_no_password(client):
    assert login(client).status_code == 200
    create(client)
    assert client.patch(f"{USERS}/alice", json={"role": "admin"}).status_code == 200
    assert client.patch(f"{USERS}/alice", json={"role": "user"}).status_code == 200


def test_the_schema_refuses_an_mcp_user_who_must_change_the_password(client, db_conn):
    import psycopg

    assert login(client).status_code == 200
    create(client, role="mcp")
    db_conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute("UPDATE app_user SET must_change_password = true WHERE username = 'alice'")
    db_conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute("UPDATE app_user SET role = 'robot' WHERE username = 'alice'")
    db_conn.rollback()


def test_the_bootstrap_cli_creates_an_mcp_user_without_a_forced_change(api, db_conn, monkeypatch):
    import io
    import sys

    from app import users

    monkeypatch.setattr(sys, "argv", ["app.users", "create", "robot", "--role", "mcp"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    users.main()
    db_conn.rollback()
    row = db_conn.execute(
        "SELECT role, must_change_password FROM app_user WHERE username = 'robot'"
    ).fetchone()
    assert row == ("mcp", False)


# --- the CLI's disable, which retires the smoke users (issue #174) ---


def cli(monkeypatch, *argv):
    import sys

    from app import users

    monkeypatch.setattr(sys, "argv", ["app.users", *argv])
    users.main()


def test_the_cli_disables_a_user_like_the_users_screen(client, db_conn, monkeypatch):
    assert login(client).status_code == 200
    create(client, role="mcp")
    at = version(db_conn)
    assert as_mcp(client, at=at).status_code == 200
    cli(monkeypatch, "disable", "alice")
    db_conn.rollback()
    row = db_conn.execute("SELECT disabled FROM app_user WHERE username = 'alice'").fetchone()
    assert row[0] is True and version(db_conn) == at + 1
    assert as_mcp(client, at=at + 1).status_code == 401  # its grants end
    actions = db_conn.execute(
        "SELECT operator, action FROM app_user_audit WHERE username = 'alice' ORDER BY occurred_at"
    ).fetchall()
    assert actions[-1] == ("cli", "disabled")


def test_the_cli_disable_revokes_sessions(client, db_conn, monkeypatch):
    assert login(client).status_code == 200
    create(client)
    client.cookies.clear()
    activate(client)
    assert client.get(SESSION).status_code == 200
    cli(monkeypatch, "disable", "alice")
    assert client.get(SESSION).status_code == 401


def test_the_cli_keeps_the_last_admin_and_refuses_an_unknown_user(api, db_conn, monkeypatch):
    with pytest.raises(SystemExit, match="last active administrator"):
        cli(monkeypatch, "disable", "i.petrova")
    with pytest.raises(SystemExit, match="no such user"):
        cli(monkeypatch, "disable", "nobody")
    db_conn.rollback()
    row = db_conn.execute("SELECT disabled FROM app_user WHERE username = 'i.petrova'").fetchone()
    assert row[0] is False


def test_the_throttle_counts_the_attempts_of_every_app_process(client, api, db_conn):
    """The count lives in sign_in_attempt (#288): attempts another app process recorded
    (rows written here directly) count, and the 21st within a minute is refused."""
    clear_attempts(db_conn)
    now = api.clock.now()
    for i in range(10):  # another process's ten failed attempts from this address
        db_conn.execute(
            "INSERT INTO sign_in_attempt (key, at) VALUES (%s, %s), (%s, %s)",
            (
                "user|missing|testclient",
                now - timedelta(seconds=30 - i),
                "address|testclient",
                now - timedelta(seconds=30 - i),
            ),
        )
    db_conn.commit()
    for _ in range(10):
        assert login(client, "missing", "wrong").status_code == 401
    assert login(client, "missing", "wrong").status_code == 429


def test_the_throttle_deletes_attempts_older_than_a_minute(client, api, db_conn):
    clear_attempts(db_conn)
    old = api.clock.now() - timedelta(minutes=2)
    db_conn.execute(
        "INSERT INTO sign_in_attempt (key, at) SELECT 'address|elsewhere', %s"
        " FROM generate_series(1, 30)",
        (old,),
    )
    db_conn.commit()
    assert login(client, "missing", "wrong").status_code == 401
    keys = db_conn.execute("SELECT key, count(*) FROM sign_in_attempt GROUP BY key ORDER BY key")
    assert keys.fetchall() == [("address|testclient", 1), ("user|missing|testclient", 1)]
