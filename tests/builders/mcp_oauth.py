"""Shared builders of tests/test_mcp_oauth.py, used by other test modules (#423)."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

from bss_mcp.passwords import hash_password

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
