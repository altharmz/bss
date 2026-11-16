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
# Customer self-care: a separate session check (app/care/auth.py), 404 while it is off.
CARE_PREFIX = "/self-care/"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


CREDENTIAL_CHECK_PATH = "/internal/v1/service/credential-check"
LOG_LEVEL_PATH = "/internal/v1/service/log-level"
SERVICE_POLL_OPERATOR = "mcp-poll"
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


METRICS_PATH = "/metrics"
# last_seen_at is refreshed at most this often; the idle expiry is accurate to it (#287)
IDLE_SLACK_SECONDS = 60


def check_metrics_token(request: Request) -> None:
    """GET /metrics (app/metrics.py): off without BSS_METRICS_TOKEN, else a bearer token.
    No session and no CSRF header: the scraper has neither."""
    token = settings.bss_metrics_token
    if not token:
        raise TmfError(404, "metrics are disabled")
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), token):
        raise TmfError(401, "unauthorized")


def authenticate(request: Request, tx: Tx, clock: ClockDep) -> None:
    if (request.method, request.url.path) in OPEN:
        return
    if (request.method, request.url.path) == ("GET", METRICS_PATH):
        check_metrics_token(request)
        return
    path = request.url.path
    if path.startswith(CARE_PREFIX):
        # customers: their own cookie and table; nothing below applies (self-care.md)
        from app.care import auth as care  # lazily: app.care imports app.internal.users

        care.authenticate(request, tx, clock.now())
        return
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
        if path == LOG_LEVEL_PATH:
            # the connector's log-level poll: the service credential alone, read-only
            if not mcp or request.method != "GET":
                raise TmfError(403, "only the MCP service credential may read the log level")
            request.state.operator = SERVICE_POLL_OPERATOR
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
                # No row lock (#287): concurrent requests of one operator, and every session
                # of a user, run side by side. A revoke, disable or password change committed
                # before this read is seen; one committed just after it lets this request
                # finish, like a request already in flight (docs/decisions/users.md).
                # last_seen_at is written at most once a minute (below), so it may lag the
                # last request by up to 60 s: the idle limit gets those 60 s, and a session
                # ends between SESSION_IDLE_SECONDS and 60 s later, never early.
                " AND s.expires_at > %s AND s.last_seen_at > %s",
                (
                    digest(token),
                    now,
                    now - timedelta(seconds=settings.session_idle_seconds + IDLE_SLACK_SECONDS),
                ),
            )
            .fetchone()
        )
        if user is None:
            raise TmfError(401, "session expired or revoked")
        # At most one write per session a minute: a read stays a read. The idle expiry is
        # therefore accurate to 60 s; the absolute expiry stays exact.
        conn.execute(
            "UPDATE app_session SET last_seen_at = %(now)s WHERE token_hash = %(t)s"
            " AND last_seen_at < %(stale)s",
            {
                "now": now,
                "t": user["token_hash"],
                "stale": now - timedelta(seconds=IDLE_SLACK_SECONDS),
            },
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
