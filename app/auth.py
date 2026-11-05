"""Authentication dependency on every application route, including future routes."""

import hashlib
import hmac
import re
from datetime import timedelta

from fastapi import Request
from psycopg.rows import dict_row

from app.api.deps import ClockDep, Tx
from app.api.errors import TmfError
from app.config import settings

COOKIE = "bss_session"
CSRF_HEADER = "x-bss-request"
SESSION_PATH = "/internal/v1/session"
OPEN = {("GET", "/"), ("GET", "/healthz"), ("GET", "/billing-status")}
OPEN |= {("POST", SESSION_PATH)}
SESSION_ACTIONS = {SESSION_PATH, SESSION_PATH + "/password"}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


CREDENTIAL_CHECK_PATH = "/internal/v1/service/credential-check"
# The two principal forms the MCP connector sends with the service secret: an OAuth
# grant's app user, whose credential version is checked on every request, and a static
# token's name, which is not a user.
USER_PRINCIPAL = re.compile(r"mcp:([A-Za-z0-9._@-]{1,64})")
TOKEN_PRINCIPAL = re.compile(r"mcp-token:[a-z0-9][a-z0-9._-]{0,47}")


def check_principal(tx, principal: str, version: str) -> None:
    if TOKEN_PRINCIPAL.fullmatch(principal):
        return
    match = USER_PRINCIPAL.fullmatch(principal)
    if match is None or not version.isascii() or not version.isdigit() or len(version) > 9:
        raise TmfError(403, "invalid MCP principal")
    with tx() as conn:
        row = conn.execute(
            "SELECT credential_version, disabled, role FROM app_user WHERE username = %s",
            (match.group(1),),
        ).fetchone()
    # the version bump already ends a grant on a role change; the role is the second line
    if row is None or row[1] or row[2] != "mcp" or row[0] != int(version):
        raise TmfError(401, "the credential was revoked", code="credentialRevoked")


def authenticate(request: Request, tx: Tx, clock: ClockDep) -> None:
    if (request.method, request.url.path) in OPEN:
        return
    path = request.url.path
    supplied = request.headers.get("x-bss-service", "")
    if supplied:
        principal = request.headers.get("x-remote-user", "")
        mcp = bool(settings.bss_service_secret) and hmac.compare_digest(
            supplied, settings.bss_service_secret
        )
        smoke = bool(settings.bss_smoke_secret) and hmac.compare_digest(
            supplied, settings.bss_smoke_secret
        )
        if not mcp and not smoke:
            raise TmfError(401, "invalid service credential")
        if path == CREDENTIAL_CHECK_PATH:
            # the connector's login backend: the service credential alone, no principal
            if not mcp:
                raise TmfError(403, "only the MCP service credential may check credentials")
            request.state.operator = "mcp-login"
            request.state.role = "service"
            return
        if path.startswith((SESSION_PATH, "/internal/v1/users")):
            raise TmfError(403, "service principals cannot manage users or sessions")
        if smoke and request.method not in {"GET", "HEAD"}:
            raise TmfError(403, "smoke credential is read-only")
        if mcp:
            check_principal(tx, principal, request.headers.get("x-bss-credential-version", ""))
        request.state.operator = principal if mcp else "smoke"
        request.state.role = "service"
        return
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise TmfError(401, "sign in required")
    now = clock.now()
    with tx() as conn:
        user = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "SELECT u.*, s.token_hash FROM app_session s JOIN app_user u USING (username)"
                # an `mcp` user can hold no UI session (the sign-in refuses it too)
                " WHERE s.token_hash = %s AND NOT u.disabled AND u.role <> 'mcp'"
                " AND s.expires_at > %s AND s.last_seen_at > %s FOR UPDATE OF u, s",
                (digest(token), now, now - timedelta(seconds=settings.session_idle_seconds)),
            )
            .fetchone()
        )
        if user is None:
            raise TmfError(401, "session expired or revoked")
        conn.execute(
            "UPDATE app_session SET last_seen_at = %s WHERE token_hash = %s",
            (now, user["token_hash"]),
        )
    request.state.operator = user["username"]
    request.state.role = user["role"]
    request.state.must_change_password = user["must_change_password"]
    request.state.token_hash = user["token_hash"]
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get(CSRF_HEADER) != "1":
            raise TmfError(403, "X-Bss-Request: 1 required")
    if user["must_change_password"] and path not in SESSION_ACTIONS:
        raise TmfError(403, "change your password first")


def require_admin(request: Request) -> None:
    if getattr(request.state, "role", None) != "admin":
        raise TmfError(403, "administrator role required")
