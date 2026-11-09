"""Application sessions, own password changes and administrator user management."""

import secrets
from datetime import timedelta
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.common import parse
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, conflict, not_found, unprocessable
from app.api.tmfjson import TmfJSONResponse
from app.auth import COOKIE, CSRF_HEADER, digest, require_admin
from app.config import settings
from app.internal.context import no_query
from app.users import audit, check_password, hash_password, lock_users, password_policy

router = APIRouter(prefix="/internal/v1")
# One process; bounded window state, driven by the injected clock. A forwarded
# address is trusted only with APP_TRUST_FORWARDED (client_address).


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Body):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(max_length=1024)


class Password(Body):
    currentPassword: SecretStr = Field(max_length=1024)
    newPassword: SecretStr = Field(max_length=1024)


Role = Literal["admin", "user", "mcp"]
# An `mcp` user logs in through the MCP connector only; `admin` and `user` through the UI only.
UI_ROLES = {"admin", "user"}


class NewUser(Login):
    username: str = Field(pattern=r"^[A-Za-z0-9._@-]{1,64}$")
    role: Role


class EditUser(Body):
    role: Role | None = None
    disabled: bool | None = None
    password: SecretStr | None = Field(default=None, max_length=1024)


def must_change(role, ui_value):
    """An `mcp` user cannot sign in to the UI to change its password: it uses the one the
    administrator set. UI roles change an administrator's password at first sign-in."""
    return False if role == "mcp" else ui_value


def view(user):
    return {
        key: user[column]
        for key, column in {
            "username": "username",
            "role": "role",
            "disabled": "disabled",
            "mustChangePassword": "must_change_password",
            "createdAt": "created_at",
            "updatedAt": "updated_at",
            "lastLoginAt": "last_login_at",
        }.items()
    }


def session_view(user):
    return {
        "username": user["username"],
        "role": user["role"],
        "mustChangePassword": user["must_change_password"],
    }


def client_address(request):
    """The last X-Forwarded-For entry behind Caddy (APP_TRUST_FORWARDED), else the peer;
    the same rule as bss_mcp/login.py's client_ip."""
    if settings.app_trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def limit_login(tx, request, username, now):
    """20 attempts a minute per (user, address) and per address, and at most 10000 distinct
    keys in the last minute. Kept in `sign_in_attempt`, so every app process shares one count
    (#288), in a transaction of its own committed before the credentials are checked: a
    refused password still counts. Rows older than a minute go in the same transaction."""
    # No bare username key: nobody can lock a named user out from another address.
    address = client_address(request)
    keys = [f"user|{username.casefold()}|{address}", f"address|{address}"]
    with tx() as conn:
        # one address at a time, so two concurrent attempts cannot both see 19
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (keys[1],))
        conn.execute("DELETE FROM sign_in_attempt WHERE at <= %s", (now - timedelta(minutes=1),))
        (distinct,) = conn.execute("SELECT count(DISTINCT key) FROM sign_in_attempt").fetchone()
        counts = dict(
            conn.execute(
                "SELECT key, count(*) FROM sign_in_attempt WHERE key = ANY(%s) GROUP BY key",
                (keys,),
            ).fetchall()
        )
        if distinct > 10000 or any(counts.get(key, 0) >= 20 for key in keys):
            raise TmfError(429, "too many sign-in attempts", headers={"Retry-After": "60"})
        conn.execute(
            "INSERT INTO sign_in_attempt (key, at) SELECT unnest(%s::text[]), %s", (keys, now)
        )


@router.post("/session")
def create_session(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    if request.headers.get(CSRF_HEADER) != "1":
        raise TmfError(403, "X-Bss-Request: 1 required")
    data = parse(Login, body)
    now = clock.now()
    limit_login(tx, request, data.username, now)
    with tx() as conn:
        user = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT * FROM app_user WHERE username = %s FOR UPDATE", (data.username,))
            .fetchone()
        )
        valid = check_password(
            data.password.get_secret_value(), user["password_hash"] if user else None
        )
        # an `mcp` user is refused exactly like a wrong password (after the same scrypt)
        if not valid or user["disabled"] or user["role"] not in UI_ROLES:
            raise TmfError(401, "invalid username or password")
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM app_session WHERE expires_at <= %s", (now,))
        conn.execute(
            "INSERT INTO app_session (token_hash, username, created_at, last_seen_at, expires_at)"
            " VALUES (%s,%s,%s,%s,%s)",
            (
                digest(token),
                data.username,
                now,
                now,
                now + timedelta(seconds=settings.session_absolute_seconds),
            ),
        )
        conn.execute(
            "UPDATE app_user SET last_login_at = %s WHERE username = %s", (now, data.username)
        )
    response = TmfJSONResponse(session_view(user))
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.app_env not in {"dev", "test"},
        samesite="strict",
        max_age=settings.session_absolute_seconds,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/session")
def get_session(request: Request):
    no_query(request)
    return TmfJSONResponse(
        {
            "username": request.state.operator,
            "role": request.state.role,
            "mustChangePassword": request.state.must_change_password,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/session")
def delete_session(request: Request, tx: Tx):
    no_query(request)
    with tx() as conn:
        conn.execute("DELETE FROM app_session WHERE token_hash = %s", (request.state.token_hash,))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.post("/session/password")
def change_password(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(Password, body)
    username = request.state.operator
    password_policy(username, data.newPassword.get_secret_value())
    limit_login(tx, request, username, clock.now())
    with tx() as conn:
        lock_users(conn)
        user = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT * FROM app_user WHERE username = %s", (username,))
            .fetchone()
        )
        if user["disabled"] or not check_password(
            data.currentPassword.get_secret_value(), user["password_hash"]
        ):
            raise TmfError(403, "current password is incorrect")
        conn.execute(
            "UPDATE app_user SET password_hash = %s, must_change_password = false, updated_at = %s,"
            " credential_version = credential_version + 1 WHERE username = %s",
            (hash_password(data.newPassword.get_secret_value()), clock.now(), username),
        )
        conn.execute("DELETE FROM app_session WHERE username = %s", (username,))
        audit(conn, username, username, "changePassword", clock.now())
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(request: Request, tx: Tx):
    no_query(request)
    with tx() as conn:
        users = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT * FROM app_user ORDER BY username")
            .fetchall()
        )
    return TmfJSONResponse([view(user) for user in users], headers={"Cache-Control": "no-store"})


@router.post("/users", dependencies=[Depends(require_admin)])
def create_user(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(NewUser, body)
    password_policy(data.username, data.password.get_secret_value())
    try:
        with tx() as conn:
            lock_users(conn)
            user = (
                conn.cursor(row_factory=dict_row)
                .execute(
                    "INSERT INTO app_user (username, role, password_hash, must_change_password,"
                    " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        data.username,
                        data.role,
                        hash_password(data.password.get_secret_value()),
                        must_change(data.role, True),
                        clock.now(),
                        clock.now(),
                    ),
                )
                .fetchone()
            )
            audit(conn, data.username, request.state.operator, "create", clock.now())
    except psycopg.errors.UniqueViolation as exc:
        raise conflict("username already exists") from exc
    return TmfJSONResponse(view(user), status_code=201)


@router.patch("/users/{username}", dependencies=[Depends(require_admin)])
def patch_user(username: str, request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    no_query(request)
    data = parse(EditUser, body)
    if not body or any(value is None for value in body.values()):
        raise unprocessable("supply role, disabled or password; null is not accepted")
    with tx() as conn:
        lock_users(conn)
        user = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT * FROM app_user WHERE username = %s", (username,))
            .fetchone()
        )
        if user is None:
            raise not_found("user", username)
        if data.disabled and username == request.state.operator:
            raise conflict("cannot disable yourself")
        role = data.role or user["role"]
        if (role in UI_ROLES) != (user["role"] in UI_ROLES) and data.password is None:
            # one password never serves both the UI and MCP
            raise unprocessable("a role change to or from mcp needs a new password")
        disabled = user["disabled"] if data.disabled is None else data.disabled
        if user["role"] == "admin" and not user["disabled"] and (role != "admin" or disabled):
            if (
                conn.execute(
                    "SELECT count(*) FROM app_user WHERE role = 'admin' AND NOT disabled"
                ).fetchone()[0]
                <= 1
            ):
                raise conflict("cannot disable or demote the last active administrator")
        stored = user["password_hash"]
        if data.password is not None:
            password_policy(username, data.password.get_secret_value())
            stored = hash_password(data.password.get_secret_value())
        updated = (
            conn.cursor(row_factory=dict_row)
            .execute(
                "UPDATE app_user SET role = %s, disabled = %s, password_hash = %s,"
                " must_change_password = %s, updated_at = %s,"
                " credential_version = credential_version + 1 WHERE username = %s RETURNING *",
                (
                    role,
                    disabled,
                    stored,
                    must_change(role, user["must_change_password"] or data.password is not None),
                    clock.now(),
                    username,
                ),
            )
            .fetchone()
        )
        conn.execute("DELETE FROM app_session WHERE username = %s", (username,))
        for action in body:
            audit(
                conn,
                username,
                request.state.operator,
                "resetPassword" if action == "password" else action,
                clock.now(),
            )
    return TmfJSONResponse(view(updated))


@router.post("/service/credential-check")
def credential_check(request: Request, body: JsonBody, tx: Tx, clock: ClockDep):
    """The MCP login's backend: is this an active app user's password? Service credential
    only (app/auth.py lets nothing else reach the handler as role "service")."""
    no_query(request)
    if request.state.role != "service":
        raise TmfError(403, "only the MCP service credential may check credentials")
    data = parse(Login, body)
    now = clock.now()
    limit_login(tx, request, data.username, now)
    with tx() as conn:
        user = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT * FROM app_user WHERE username = %s FOR UPDATE", (data.username,))
            .fetchone()
        )
        # an unknown user costs one scrypt too; unknown, wrong and disabled look the same
        valid = check_password(
            data.password.get_secret_value(), user["password_hash"] if user else None
        )
        # only `mcp` users log in through MCP; anything else looks like a wrong password.
        # An `mcp` user never has to change its password (app_user_mcp_password_set).
        if not valid or user["disabled"] or user["role"] != "mcp":
            raise TmfError(401, "invalid username or password", code="invalidCredentials")
        conn.execute(
            "UPDATE app_user SET last_login_at = %s WHERE username = %s", (now, data.username)
        )
    return TmfJSONResponse(
        {
            "username": user["username"],
            "role": user["role"],
            "credentialVersion": user["credential_version"],
        },
        headers={"Cache-Control": "no-store"},
    )
