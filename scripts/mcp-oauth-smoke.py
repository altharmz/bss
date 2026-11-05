"""MCP smoke of the OAuth login, the way an MCP client does it, as an `mcp` app user.

Usage: python3 scripts/mcp-oauth-smoke.py <base url> [local|caddy]
  MCP_SMOKE_USER, MCP_SMOKE_PASSWORD     an active `mcp` user (required, else SKIPPED)
  MCP_SMOKE_UI_USER, MCP_SMOKE_UI_PASSWORD  optional: an `admin`/`user`, refused on /login

make mcp-smoke runs it with throwaway local users it disables afterwards; deploy.sh with the
smoke user from .env.smoke. Register a client, authorize with PKCE S256, log in, consent to
the read scope only, exchange the code; tools/list, one read tool, a write tool refused,
refresh, revoke, and the revoked token refused.

It reads no business data beyond get_context and changes none. What it writes: one client
registration with its pending request, code and tokens in the connector's store, and the
smoke user's last_login_at in the app. It revokes its grant at the end; the registration
stays (no deletion endpoint; docs/decisions/mcp.md). Secrets are never printed.
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

REDIRECT = "http://127.0.0.1:9/callback"  # never contacted: the redirect is read, not followed
CLIENT_NAME = "bss-mcp-smoke"
WRITE_TOOL = "create_individual"  # any write tool: a read token is refused before it applies


def fail(message: str) -> None:
    sys.exit(f"\033[1;31mMCP OAUTH SMOKE FAILED:\033[0m {message}")


def ok(message: str) -> None:
    print(f"  ok  {message}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def send(method: str, url: str, *, form=None, body=None, token=None):
    """-> (status, headers, text); never raises on an HTTP status."""
    headers = {"Accept": "application/json, text/event-stream"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # main() admits only http(s):// base URLs
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with OPENER.open(request, timeout=30) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()
    except urllib.error.URLError as error:
        fail(f"{method} {urlsplit(url).path} did not answer: {error.reason}")


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


class Smoke:
    def __init__(self, base: str) -> None:
        self.base = base
        self.ids = 0

    def url(self, path: str) -> str:
        return self.base + path

    def register(self) -> str:
        status, _, text = send(
            "POST",
            self.url("/register"),
            body={
                "client_name": CLIENT_NAME,
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        if status != 201:
            fail(f"POST /register answered {status}")
        return json.loads(text)["client_id"]

    def login_link(self, client_id: str, challenge: str, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": "read write",
                "resource": self.url("/mcp"),
            }
        )
        status, headers, _ = send("GET", self.url(f"/authorize?{query}"))
        location = headers.get("Location", "")
        if status != 302 or not location.startswith(self.url("/login?request=")):
            fail(f"GET /authorize answered {status}, not the login page")
        return parse_qs(urlsplit(location).query)["request"][0]

    def login(self, request_id: str, user: str, password: str):
        form = {
            "request": request_id,
            "user": user,
            "password": password,
            "scope": "read",
            "action": "approve",
        }
        return send("POST", self.url("/login"), form=form)

    def token(self, client_id: str, **form):
        status, _, text = send("POST", self.url("/token"), form={"client_id": client_id, **form})
        return status, json.loads(text) if text.startswith("{") else {}

    def rpc(self, token: str, method: str, params=None):
        self.ids += 1
        message = {"jsonrpc": "2.0", "id": self.ids, "method": method}
        if params is not None:
            message["params"] = params
        return send("POST", self.url("/mcp"), body=message, token=token)


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
    mode = sys.argv[2] if len(sys.argv) > 2 else "local"
    if not base.startswith(("http://", "https://")) or mode not in ("local", "caddy"):
        sys.exit("usage: mcp-oauth-smoke.py <base url> [local|caddy]")
    user = os.environ.get("MCP_SMOKE_USER", "")
    password = os.environ.get("MCP_SMOKE_PASSWORD", "")
    if not user or not password:
        print(
            "mcp oauth smoke: SKIPPED - no smoke user (MCP_SMOKE_USER and MCP_SMOKE_PASSWORD;"
            " in production from .env.smoke, see docs/mcp.md)"
        )
        return
    smoke = Smoke(base)

    # 1. registration, authorize with PKCE, login and consent to read only, the code
    client_id = smoke.register()
    ok("POST /register -> 201 (a public client)")
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    request_id = smoke.login_link(client_id, challenge, state)
    ok("GET /authorize -> 302 to the login page")
    status, headers, _ = smoke.login(request_id, user, password)
    location = headers.get("Location", "")
    if status != 302 or not location.startswith(REDIRECT + "?"):
        fail(f"POST /login as the smoke user answered {status} (an active `mcp` user?)")
    query = parse_qs(urlsplit(location).query)
    if query.get("state") != [state] or "code" not in query:
        fail("the login redirect lacks the code or the state")
    ok("POST /login as the mcp smoke user, scope read -> 302 with code and state")
    status, issued = smoke.token(
        client_id,
        grant_type="authorization_code",
        code=query["code"][0],
        code_verifier=verifier,
        redirect_uri=REDIRECT,
    )
    if status != 200 or issued.get("scope") != "read":
        fail(f"POST /token answered {status}, scope {issued.get('scope')!r} (expected read)")
    access, refresh = issued["access_token"], issued["refresh_token"]
    ok("POST /token -> 200, an access and a refresh token, scope read")

    try:
        # 2. tools: the list, one read tool, a write tool refused
        status, _, text = smoke.rpc(access, "tools/list")
        if status != 200 or '"tools"' not in text:
            fail(f"tools/list answered {status}")
        ok("tools/list -> 200")
        status, _, text = smoke.rpc(access, "tools/call", {"name": "get_context", "arguments": {}})
        if status != 200 or '"isError":false' not in text.replace(" ", ""):
            fail(f"get_context answered {status}")
        ok("get_context (read) -> 200")
        status, _, _ = smoke.rpc(access, "tools/call", {"name": WRITE_TOOL, "arguments": {}})
        if status != 403:
            fail(f"{WRITE_TOOL} with a read token answered {status}, expected 403")
        ok(f"{WRITE_TOOL} (write) with the read token -> 403, nothing applied")

        # 3. refresh rotates the pair
        status, refreshed = smoke.token(
            client_id, grant_type="refresh_token", refresh_token=refresh
        )
        if status != 200 or refreshed.get("access_token") in (None, access):
            fail(f"the refresh answered {status}")
        access, refresh = refreshed["access_token"], refreshed["refresh_token"]
        ok("refresh -> 200, a new pair")
    finally:
        # 4. revoke the grant whatever happened above
        status, _, _ = send(
            "POST", smoke.url("/revoke"), form={"client_id": client_id, "token": refresh}
        )
    if status != 200:
        fail(f"POST /revoke answered {status}")
    status, _, _ = smoke.rpc(access, "tools/list")
    if status != 401:
        fail(f"tools/list with a revoked grant answered {status}, expected 401")
    ok("POST /revoke (refresh token) -> 200; its access token -> 401")

    # 5. a UI role is refused like a wrong password (only with a UI user to try)
    ui_user = os.environ.get("MCP_SMOKE_UI_USER", "")
    ui_password = os.environ.get("MCP_SMOKE_UI_PASSWORD", "")
    if ui_user and ui_password:
        _, challenge = pkce()
        request_id = smoke.login_link(client_id, challenge, secrets.token_urlsafe(16))
        status, headers, text = smoke.login(request_id, ui_user, ui_password)
        if status != 401 or "Location" in headers or "Wrong user name or password" not in text:
            fail(f"POST /login as a UI user answered {status}, expected 401")
        ok("POST /login as a UI user (right password) -> 401, like a wrong password")
    elif mode == "local":
        fail("local mode needs MCP_SMOKE_UI_USER and MCP_SMOKE_UI_PASSWORD")
    else:
        print("  --  UI-user refusal not checked here (covered by make mcp-smoke)")
    print(f"mcp oauth smoke: all checks passed ({base}, {mode})")


if __name__ == "__main__":
    main()
