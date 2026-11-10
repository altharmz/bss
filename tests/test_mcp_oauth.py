"""The MCP connector, slice 2 (docs/mcp.md): OAuth 2.1 the way an MCP client does it -
discovery from a 401, dynamic client registration, authorize with PKCE S256, the login
and consent page, token, tools, refresh, revoke - and every refusal on the way."""

import base64
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

from bss_mcp.operations import OPERATIONS
from bss_mcp.passwords import hash_password
from tests.apiclient import total

PASSWORD = "correct horse battery"
USERS = {"demo": PASSWORD, "ops": "another password!"}
REDIRECT = "https://client.example/callback"
GIVEN = {"givenName": "Zoya", "familyName": "Kosmodemyanskaya"}


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def add_user(db_conn, username, password, *, must_change=False, disabled=False, role="mcp"):
    from tests.timeline import NOW

    db_conn.execute(
        "INSERT INTO app_user (username, role, password_hash, must_change_password, disabled,"
        " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (username, role, hash_password(password), must_change, disabled, NOW, NOW),
    )
    db_conn.commit()


@pytest.fixture
def oauth(db_conn, mcp):
    for name, password in USERS.items():
        add_user(db_conn, name, password)
    return mcp


def register(mcp, redirect_uris=(REDIRECT,), **over):
    body = {
        "client_name": "Test client",
        "redirect_uris": list(redirect_uris),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        **over,
    }
    return mcp.client.post("/register", json=body)


def client_id(mcp, **over) -> str:
    response = register(mcp, **over)
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


def authorize(mcp, cid, challenge, *, scope="read write", redirect=REDIRECT, **extra):
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1",
        **({"scope": scope} if scope else {}),
        **extra,
    }
    return mcp.client.get("/authorize", params=params, follow_redirects=False)


def login_link(mcp, cid, challenge, **kw) -> str:
    response = authorize(mcp, cid, challenge, **kw)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("https://mcp.test/login?request=")
    return parse_qs(urlsplit(location).query)["request"][0]


def login(mcp, request_id, *, user="demo", password=PASSWORD, scope="write", action="approve"):
    form = {"request": request_id, "user": user, "password": password, "scope": scope}
    return mcp.client.post("/login", data={**form, "action": action}, follow_redirects=False)


def code_from(response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(REDIRECT + "?")
    query = parse_qs(urlsplit(location).query)
    assert query["state"] == ["st-1"]
    return query["code"][0]


def token(mcp, cid, **form):
    return mcp.client.post("/token", data={"client_id": cid, **form})


def tokens(mcp, *, scope="write", requested="read write", user="demo", password=PASSWORD) -> dict:
    """A complete authorization: register, authorize, log in, exchange the code."""
    cid = client_id(mcp)
    verifier, challenge = pkce()
    request_id = login_link(mcp, cid, challenge, scope=requested)
    code = code_from(login(mcp, request_id, scope=scope, user=user, password=password))
    response = token(
        mcp,
        cid,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri=REDIRECT,
    )
    assert response.status_code == 200, response.text
    return {"client_id": cid, **response.json()}


# --- the whole flow, as a client runs it ---


def test_the_full_flow_from_discovery_to_revocation(oauth, caplog):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    mcp = oauth

    # 1. discovery: the 401 points to the protected-resource metadata ...
    challenge_header = mcp.rpc("tools/list", None, token=None).headers["www-authenticate"]
    resource_url = re.search(r'resource_metadata="([^"]+)"', challenge_header).group(1)
    resource = mcp.client.get(urlsplit(resource_url).path).json()
    assert resource["resource"] == "https://mcp.test/mcp"
    assert resource["scopes_supported"] == ["read", "write"]
    # ... which names the authorization server, whose metadata names the endpoints
    [issuer] = resource["authorization_servers"]
    meta = mcp.client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == issuer == "https://mcp.test"  # exact, no trailing slash
    assert meta["authorization_endpoint"] == "https://mcp.test/authorize"
    assert meta["token_endpoint"] == "https://mcp.test/token"
    assert meta["registration_endpoint"] == "https://mcp.test/register"
    assert meta["revocation_endpoint"] == "https://mcp.test/revoke"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert set(meta["grant_types_supported"]) == {"authorization_code", "refresh_token"}

    # 2. registration, 3. authorize + login + consent, 4. token
    issued = tokens(mcp)
    assert issued["token_type"] == "Bearer" and issued["expires_in"] == 3600
    assert issued["scope"] == "read write"
    access, refresh = issued["access_token"], issued["refresh_token"]

    # 5. tools with the access token: the principal is the user
    assert len(mcp.tools(access)) == len(OPERATIONS)
    is_error, answer = mcp.result(access, "create_individual", {"body": GIVEN})
    assert not is_error and answer["status"] == 201

    # 6. refresh: a new pair; the old refresh token is spent (rotation)
    refreshed = token(mcp, issued["client_id"], grant_type="refresh_token", refresh_token=refresh)
    assert refreshed.status_code == 200, refreshed.text
    new = refreshed.json()
    assert new["access_token"] != access and new["refresh_token"] != refresh
    again = token(mcp, issued["client_id"], grant_type="refresh_token", refresh_token=refresh)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert mcp.rpc("tools/list", None, new["access_token"]).status_code == 200

    # 7. revoking the refresh token revokes the grant: its access tokens stop working
    revoked = mcp.client.post(
        "/revoke", data={"client_id": issued["client_id"], "token": new["refresh_token"]}
    )
    assert revoked.status_code == 200
    assert mcp.rpc("tools/list", None, new["access_token"]).status_code == 401
    assert mcp.rpc("tools/list", None, access).status_code == 401
    after = token(
        mcp, issued["client_id"], grant_type="refresh_token", refresh_token=new["refresh_token"]
    )
    assert after.status_code == 400

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "bss_mcp.audit"]
    events = [(line["event"], line["outcome"]) for line in lines]
    assert ("register", "ok") in events and ("login", "ok, scope write") in events
    assert ("token", "issued") in events and ("refresh", "issued") in events
    assert ("revoke", "refresh revoked") in events
    assert ("tool_call", 201) in events
    assert all(line.get("principal") in (None, "mcp:demo") for line in lines)
    for secret in (PASSWORD, access, refresh, new["access_token"], new["refresh_token"]):
        assert secret not in caplog.text


def test_revoking_an_access_token_leaves_the_refresh_token(oauth):
    issued = tokens(oauth)
    revoked = oauth.client.post(
        "/revoke", data={"client_id": issued["client_id"], "token": issued["access_token"]}
    )
    assert revoked.status_code == 200, revoked.text
    assert oauth.rpc("tools/list", None, issued["access_token"]).status_code == 401
    refreshed = token(
        oauth,
        issued["client_id"],
        grant_type="refresh_token",
        refresh_token=issued["refresh_token"],
    )
    assert refreshed.status_code == 200


# --- scopes chosen on the consent screen ---


def test_read_consent_gives_a_read_token(api, oauth):
    issued = tokens(oauth, scope="read")
    assert issued["scope"] == "read"
    listed = oauth.tools(issued["access_token"])
    assert "list_customers" in listed and "create_individual" not in listed
    assert all(t["annotations"]["readOnlyHint"] for t in listed.values())
    response = oauth.call(issued["access_token"], "create_individual", {"body": GIVEN})
    assert response.status_code == 403
    assert int(total(api.get("/tmf-api/partyManagement/v5/individual"), api)) == 0


def test_a_client_asking_for_read_cannot_be_given_write(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge, scope="read")
    page = oauth.client.get(f"/login?request={request_id}").text
    assert 'value="write"' not in page and "read access only" in page
    assert login(oauth, request_id, scope="write").status_code == 400


def test_a_refresh_cannot_widen_the_scope(oauth):
    issued = tokens(oauth, scope="read")
    response = token(
        oauth,
        issued["client_id"],
        grant_type="refresh_token",
        refresh_token=issued["refresh_token"],
        scope="read write",
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_scope"


# --- refusals ---


def test_a_wrong_password_issues_nothing_and_five_end_the_link(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    for _ in range(4):
        wrong = login(oauth, request_id, password="wrong password")
        assert wrong.status_code == 401 and "Wrong user name or password" in wrong.text
        assert "location" not in wrong.headers
    assert login(oauth, request_id, password="wrong password").status_code == 400  # 5th
    assert login(oauth, request_id).status_code == 400  # the link is gone, right password too


@pytest.mark.parametrize("user", ["nobody", "Demo", "a b", ""])
def test_an_unknown_user_is_refused_like_a_wrong_password(oauth, user):
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    assert login(oauth, request_id, user=user).status_code == 401


def test_another_users_password_does_not_work(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    assert login(oauth, request_id, user="ops").status_code == 401


def test_a_bad_pkce_verifier_is_refused(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    code = code_from(login(oauth, login_link(oauth, cid, challenge)))
    other_verifier, _ = pkce()
    response = token(
        oauth,
        cid,
        grant_type="authorization_code",
        code=code,
        code_verifier=other_verifier,
        redirect_uri=REDIRECT,
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    assert "code_verifier" in response.json()["error_description"]


def test_only_s256_is_accepted(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    response = authorize(oauth, cid, challenge, code_challenge_method="plain")
    assert "login?request=" not in response.headers.get("location", "")


def test_a_code_works_once(oauth):
    cid = client_id(oauth)
    verifier, challenge = pkce()
    code = code_from(login(oauth, login_link(oauth, cid, challenge)))
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT,
    }
    assert token(oauth, cid, **form).status_code == 200
    second = token(oauth, cid, **form)
    assert second.status_code == 400 and second.json()["error"] == "invalid_grant"


def test_two_exchanges_racing_for_one_code_issue_one_token_pair(oauth):
    """Both load the code before either spends it: only the first exchange may win."""
    import anyio
    from mcp.server.auth.provider import TokenError

    from bss_mcp.config import Settings
    from bss_mcp.oauth import Provider

    cid = client_id(oauth)
    _, challenge = pkce()
    code = code_from(login(oauth, login_link(oauth, cid, challenge)))
    provider = Provider(oauth.store, oauth.clock, Settings(mcp_public_url="https://mcp.test"))

    async def race() -> None:
        client = await provider.get_client(cid)
        first = await provider.load_authorization_code(client, code)
        second = await provider.load_authorization_code(client, code)
        assert first is not None and second is not None
        await provider.exchange_authorization_code(client, first)
        with pytest.raises(TokenError, match="already used"):
            await provider.exchange_authorization_code(client, second)

    anyio.run(race)


def test_a_code_belongs_to_its_client(oauth):
    cid, other = client_id(oauth), client_id(oauth)
    verifier, challenge = pkce()
    code = code_from(login(oauth, login_link(oauth, cid, challenge)))
    response = token(
        oauth,
        other,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri=REDIRECT,
    )
    assert response.status_code == 400


def test_an_unregistered_redirect_uri_is_refused_without_redirecting(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    response = authorize(oauth, cid, challenge, redirect="https://evil.example/cb")
    assert response.status_code == 400
    assert "evil" not in response.headers.get("location", "")
    assert "not registered" in response.json()["error_description"]


@pytest.mark.parametrize(
    "uri",
    [
        "http://client.example/cb",  # plain http off localhost
        "http://192.168.1.5/cb",
        "https://client.example/cb#frag",
        "ftp://client.example/cb",
        "cursor://anysphere/cb",  # custom schemes: not in the demo's policy
    ],
)
def test_registration_refuses_redirect_uris_outside_the_policy(oauth, uri):
    response = register(oauth, redirect_uris=[uri])
    assert response.status_code == 400, response.text
    assert response.json()["error"] in ("invalid_redirect_uri", "invalid_client_metadata")


@pytest.mark.parametrize(
    "uri", ["http://localhost:33418/callback", "http://127.0.0.1:6274/oauth/callback", REDIRECT]
)
def test_registration_accepts_https_and_local_http(oauth, uri):
    assert register(oauth, redirect_uris=[uri]).status_code == 201


def test_registration_is_capped(mcp_settings, request):
    mcp_settings.mcp_max_clients = 2
    mcp = request.getfixturevalue("mcp")
    assert register(mcp).status_code == 201
    assert register(mcp).status_code == 201
    third = register(mcp)
    assert third.status_code == 400 and "client limit" in third.json()["error_description"]


def test_a_foreign_resource_is_refused(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    response = authorize(oauth, cid, challenge, resource="https://other.example/mcp")
    location = response.headers.get("location", "")
    assert location.startswith(REDIRECT) and "error=invalid_request" in location


def test_deny_sends_access_denied_back(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    response = login(oauth, login_link(oauth, cid, challenge), action="deny", password="")
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["st-1"]
    assert "code" not in query


# --- expiry (the injected clock) ---


def test_an_expired_login_link_is_gone(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    oauth.clock.advance(601)
    assert oauth.client.get(f"/login?request={request_id}").status_code == 400
    assert login(oauth, request_id).status_code == 400


def test_an_expired_code_is_refused(oauth):
    cid = client_id(oauth)
    verifier, challenge = pkce()
    code = code_from(login(oauth, login_link(oauth, cid, challenge)))
    oauth.clock.advance(301)
    response = token(
        oauth,
        cid,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri=REDIRECT,
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


def test_access_tokens_expire_and_refresh_tokens_outlive_them(oauth):
    issued = tokens(oauth)
    oauth.clock.advance(3601)
    assert oauth.rpc("tools/list", None, issued["access_token"]).status_code == 401
    refreshed = token(
        oauth,
        issued["client_id"],
        grant_type="refresh_token",
        refresh_token=issued["refresh_token"],
    )
    assert refreshed.status_code == 200
    oauth.clock.advance(31 * 86400)
    late = token(
        oauth,
        issued["client_id"],
        grant_type="refresh_token",
        refresh_token=refreshed.json()["refresh_token"],
    )
    assert late.status_code == 400


# --- limits and the page itself ---


def test_the_auth_endpoints_are_rate_limited_per_ip(mcp_settings, request):
    mcp_settings.mcp_auth_rate_per_min = 3
    mcp = request.getfixturevalue("mcp")
    for _ in range(3):
        assert mcp.client.post("/token", data={}).status_code != 429
    limited = mcp.client.post("/register", json={})
    assert limited.status_code == 429 and limited.headers["retry-after"] == "20"
    assert mcp.client.get("/.well-known/oauth-authorization-server").status_code == 200  # GET


def test_the_login_page_escapes_the_client_name_and_cannot_be_framed(oauth):
    cid = client_id(oauth, client_name="<script>alert(1)</script>")
    _, challenge = pkce()
    response = oauth.client.get(f"/login?request={login_link(oauth, cid, challenge)}")
    assert "<script>alert" not in response.text and "&lt;script&gt;" in response.text
    assert "client.example" in response.text  # where the browser goes back to
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("trust", "forwarded", "expected"),
    [
        (False, "6.6.6.6", "10.0.0.9"),  # locally: the peer, whatever the header says
        (True, None, "10.0.0.9"),
        (True, "6.6.6.6, 203.0.113.7", "203.0.113.7"),  # prod: Caddy's entry is the last
    ],
)
def test_the_client_ip_trusts_x_forwarded_for_only_behind_caddy(trust, forwarded, expected):
    from starlette.requests import Request

    from bss_mcp.login import client_ip

    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    request = Request({"type": "http", "headers": headers, "client": ("10.0.0.9", 5000)})
    assert client_ip(request, trust) == expected


def test_openid_configuration_is_a_404(oauth):
    assert oauth.client.get("/.well-known/openid-configuration").status_code == 404


def test_the_root_resource_metadata_is_the_same_document(oauth):
    root = oauth.client.get("/.well-known/oauth-protected-resource").json()
    assert root == oauth.client.get("/.well-known/oauth-protected-resource/mcp").json()


# --- the app's users (issue #146) ---


def set_user(db_conn, username, sql):
    db_conn.execute(f"UPDATE app_user SET {sql} WHERE username = %s", (username,))  # noqa: S608
    db_conn.commit()


def test_an_oauth_grant_acts_as_the_app_user_in_the_apps_history(oauth, db_conn):
    issued = tokens(oauth)
    is_error, answer = oauth.result(issued["access_token"], "create_individual", {"body": GIVEN})
    assert not is_error and answer["status"] == 201
    row = db_conn.execute("SELECT last_login_at FROM app_user WHERE username='demo'").fetchone()
    assert row[0] is not None


@pytest.mark.parametrize("role", ["admin", "user"])
def test_a_ui_user_cannot_log_in_through_mcp_like_a_wrong_password(oauth, db_conn, role):
    add_user(db_conn, "ui-person", PASSWORD, role=role)
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    response = login(oauth, request_id, user="ui-person")  # the right password
    assert response.status_code == 401 and "Wrong user name or password" in response.text
    assert "location" not in response.headers


def test_a_role_change_away_from_mcp_ends_the_grant(oauth, db_conn):
    issued = tokens(oauth)
    assert oauth.call(issued["access_token"], "list_customers").status_code == 200
    set_user(db_conn, "demo", "role = 'user'")  # no version bump: the role is the second line
    assert oauth.call(issued["access_token"], "list_customers").status_code == 401


def test_a_disabled_user_cannot_sign_in(oauth, db_conn):
    set_user(db_conn, "demo", "disabled = true")
    cid = client_id(oauth)
    _, challenge = pkce()
    request_id = login_link(oauth, cid, challenge)
    response = login(oauth, request_id)
    assert response.status_code == 401 and "Wrong user name or password" in response.text


def test_the_apps_rate_limit_is_shown_as_too_many_attempts(oauth):
    cid = client_id(oauth)
    _, challenge = pkce()
    seen = set()
    for _ in range(25):  # more wrong passwords than the app allows a minute
        request_id = login_link(oauth, cid, challenge)
        seen.add(login(oauth, request_id, user="ops", password="wrong one!").status_code)
    assert 429 in seen


def test_app_user_names_are_not_limited_to_the_static_token_pattern(oauth, db_conn):
    add_user(db_conn, "Ivan.Petrov@example", PASSWORD)
    issued = tokens(oauth, user="Ivan.Petrov@example")
    assert oauth.rpc("tools/list", None, issued["access_token"]).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        "credential_version = credential_version + 1",
        "disabled = true",
    ],
)
def test_a_changed_credential_ends_the_grant_and_the_client_authorizes_again(
    oauth, db_conn, change
):
    issued = tokens(oauth)
    access = issued["access_token"]
    assert oauth.rpc("tools/list", None, access).status_code == 200
    set_user(db_conn, "demo", change)
    revoked = oauth.call(access, "list_customers")
    assert revoked.status_code == 401
    assert 'error="invalid_token"' in revoked.headers["www-authenticate"]
    assert "resource_metadata=" in revoked.headers["www-authenticate"]
    # the grant is gone: the access and refresh tokens no longer work
    assert oauth.rpc("tools/list", None, access).status_code == 401
    again = token(
        oauth,
        issued["client_id"],
        grant_type="refresh_token",
        refresh_token=issued["refresh_token"],
    )
    assert again.status_code == 400


def test_another_users_grant_survives_a_change_to_one_user(oauth, db_conn):
    mine, theirs = tokens(oauth), tokens(oauth, user="ops", password=USERS["ops"])
    set_user(db_conn, "demo", "credential_version = credential_version + 1")
    assert oauth.call(mine["access_token"], "list_customers").status_code == 401
    assert oauth.call(theirs["access_token"], "list_customers").status_code == 200


def test_a_static_token_is_the_mcp_token_principal_and_ignores_users(oauth, db_conn):
    static = oauth.token("ops-bot", "write")
    is_error, answer = oauth.result(static, "create_individual", {"body": GIVEN})
    assert not is_error and answer["status"] == 201
    set_user(db_conn, "demo", "disabled = true")
    assert oauth.call(static, "list_customers").status_code == 200


def test_the_store_drops_grants_without_a_credential_version_and_keeps_static_tokens(tmp_path):
    import sqlite3

    from bss_mcp.clock import FixedClock
    from bss_mcp.store import Store

    path = str(tmp_path / "old.db")
    clock = FixedClock()
    first = Store(path, clock)
    _, static = first.create_static_token("kept", "read")
    first.save_token(
        "bss_at_old", kind="access", grant_id="g", client_id="c", subject="mcp:demo",
        scopes=["read"], resource=None, credential_version=1, ttl=60,
    )  # fmt: skip
    first.save_code("old-code", "c", '{"subject": "mcp:demo"}', 60)  # a code without a version
    db = sqlite3.connect(path)
    db.execute("UPDATE oauth_token SET credential_version = NULL")  # what an old file holds
    db.commit()
    db.close()
    second = Store(path, clock)
    assert second.token("bss_at_old", "access") is None
    assert second.code("old-code") is None
    assert second.find_static_token(static) is not None
