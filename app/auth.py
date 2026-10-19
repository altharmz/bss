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
        if path.startswith((SESSION_PATH, "/internal/v1/users")):
            raise TmfError(403, "service principals cannot manage users or sessions")
        if mcp and not re.fullmatch(r"mcp:[a-z0-9][a-z0-9._-]{0,47}", principal):
            raise TmfError(403, "invalid MCP principal")
        if smoke and request.method not in {"GET", "HEAD"}:
            raise TmfError(403, "smoke credential is read-only")
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
                " WHERE s.token_hash = %s AND NOT u.disabled AND s.expires_at > %s"
                " AND s.last_seen_at > %s FOR UPDATE OF u, s",
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
