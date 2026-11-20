"""Customer sign-in: logins, one-time codes, sessions (docs/decisions/self-care.md).

Sign-in is two steps: a right email and password send a 6-digit code to the email (10 minutes,
5 attempts); the code opens a session. 10 wrong passwords in a row lock the login for 15
minutes (the account PIN's mechanics). A password is set with a link code sent to the email:
`verify_email` (an operator's invitation, 24 hours) or `reset_password` (1 hour). Sign-ins,
locks and password changes are written to `account_audit`.

Codes are sent by the request itself, not queued for the worker: the message log
(`notification_message`, TMF681) keeps the email with the code masked, so no operator can read a
live code there.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from fastapi import Request
from psycopg.rows import dict_row

from app.api.errors import TmfError, conflict, not_found
from app.care.deps import CareSession
from app.config import settings
from app.integrations.notify import NotifyError, adapter
from app.internal.users import limit_login
from app.repository import care_logins as repo
from app.repository import notifications as messages
from app.repository import security
from app.repository._sql import Row
from app.users import check_password, hash_password, password_policy

log = logging.getLogger("app.care")

COOKIE = "bss_care_session"
COOKIE_PATH = "/self-care/"
CSRF_HEADER = "x-bss-request"
OPERATOR = "self-care"  # the audit's operator for what a customer does
ROLE = "customer"

SIGN_IN_CODE_VALID = timedelta(minutes=10)
CODE_ATTEMPTS = 5
VALID = {"verify_email": timedelta(hours=24), "reset_password": timedelta(hours=1)}
PASSWORD_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)
IDLE_SLACK_SECONDS = 60  # last_seen_at is refreshed at most this often (users.md, #287)

INVALID_PASSWORD = "invalid email or password"  # noqa: S105 - a message, not a secret
INVALID_CODE = "the code is wrong, used or expired"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normal(email: str) -> str:
    return email.strip().lower()


def _throttle(tx, request: Request, email: str, now: datetime) -> None:
    """The operator sign-in throttle (20 a minute per address and per email and address), in
    its own namespace of keys."""
    limit_login(tx, request, f"care:{email}", now)


# --- sending codes ---

TEXTS = {
    "care_sign_in_code": (
        "Your sign-in code",
        "Hello {name},\n\nyour sign-in code is {code}. It is valid for 10 minutes. If you did"
        " not try to sign in, change your password.",
    ),
    "care_verify_email": (
        "Set your self-care password",
        "Hello {name},\n\nyou are invited to self-care. Set your password with this code within"
        " 24 hours: {code}",
    ),
    "care_reset_password": (
        "Reset your self-care password",
        "Hello {name},\n\nset a new password with this code within an hour: {code}\nIf you did"
        " not ask for it, ignore this message.",
    ),
}
EVENT = {
    "sign_in": "care_sign_in_code",
    "verify_email": "care_verify_email",
    "reset_password": "care_reset_password",
}


def send_code(tx, login: Row, purpose: str, code: str, now: datetime) -> bool:
    """Email the code to the login's address now, and log the message with the code masked.
    False when the sender did not accept it."""
    event = EVENT[purpose]
    subject, text = TEXTS[event]
    with tx() as conn:
        name = repo.customer_name(conn, login["customer_id"])
    params = {"name": name or "customer"}
    uid = uuid4()
    ref, error = None, None
    try:
        ref = adapter().send_email(
            str(uid), login["email"], subject, text.format(code=code, **params)
        )
    except NotifyError as exc:
        error = str(exc) or "the sender refused the message"
    except Exception as exc:  # noqa: BLE001 - the customer asks again; the log keeps why
        log.exception("self-care code %s", uid)
        error = str(exc) or type(exc).__name__
    with tx() as conn:
        messages.record_sent(
            conn,
            uid=uid,
            event_type=event,
            receiver_name=name,
            receiver_email=login["email"],
            customer_id=login["customer_id"],
            subject=subject,
            content=text.format(code="*" * min(len(code), 6), **params),
            ref=ref,
            error=error,
            now=now,
        )
    return error is None


def _not_sent() -> TmfError:
    return TmfError(503, "the code could not be sent; try again later", code="codeNotSent")


def _audit(
    conn: psycopg.Connection,
    login: Row,
    now: datetime,
    action: str,
    verification: str,
    detail: dict | None = None,
) -> None:
    security.record(
        conn,
        now=now,
        operator=OPERATOR,
        role=ROLE,
        action=action,
        outcome="done",
        verification=verification,
        customer_id=login["customer_id"],
        billing_account_id=security.first_account(conn, login["customer_id"]),
        detail={"login": str(login["id"]), **(detail or {})},
    )


def _link_code(conn: psycopg.Connection, login: Row, purpose: str, now: datetime) -> str:
    code = secrets.token_urlsafe(24)
    repo.new_code(conn, login["id"], purpose, digest(code), now, VALID[purpose])
    return code


# --- sign-in ---


def start_sign_in(tx, request: Request, email: str, password: str, now: datetime) -> datetime:
    """Check the password and email a sign-in code; the code's expiry."""
    _throttle(tx, request, email, now)
    outcome, code = "invalid", None
    with tx() as conn:
        login = repo.login_by_email(conn, email, lock=True)
        usable = login is not None and login["disabled_at"] is None
        if usable and login["locked_until"] is not None and login["locked_until"] > now:
            outcome = "locked"  # even the right password: not checked, not counted
        else:
            # an unknown, disabled or not yet activated login costs one scrypt too
            stored = login["password_hash"] if usable else None
            valid = check_password(password, stored)
            if usable and stored is not None and not valid:
                until = now + LOCKOUT
                counted = repo.password_failed(
                    conn, login["id"], maximum=PASSWORD_ATTEMPTS, until=until
                )
                if counted["locked_until"] == until:
                    _audit(
                        conn,
                        login,
                        now,
                        "care_locked",
                        "none",
                        {"attempts": PASSWORD_ATTEMPTS, "lockedUntil": until.isoformat()},
                    )
            elif usable and valid:
                repo.password_ok(conn, login["id"])
                code = f"{secrets.randbelow(10**6):06d}"
                repo.new_code(
                    conn, login["id"], "sign_in", hash_password(code), now, SIGN_IN_CODE_VALID
                )
                outcome = "sent"
    if outcome == "locked":
        raise TmfError(403, "too many wrong passwords: try again later", code="loginLocked")
    if outcome == "invalid":
        raise TmfError(401, INVALID_PASSWORD, code="invalidCredentials")
    assert login is not None and code is not None
    if not send_code(tx, login, "sign_in", code, now):
        raise _not_sent()
    return now + SIGN_IN_CODE_VALID


def finish_sign_in(tx, request: Request, email: str, code: str, now: datetime) -> tuple[str, Row]:
    """The emailed code opens a session: its token (for the cookie) and the session row."""
    _throttle(tx, request, email, now)
    token, session = None, None
    with tx() as conn:
        login = repo.login_by_email(conn, email, lock=True)
        pending = None
        if login is not None and login["disabled_at"] is None:
            pending = repo.open_code(conn, login["id"], "sign_in", now)
        if pending is not None:
            if check_password(code, pending["code_hash"]):
                repo.code_used(conn, pending["id"], now)
                token = secrets.token_urlsafe(32)
                expires = now + timedelta(seconds=settings.care_session_absolute_seconds)
                repo.new_session(conn, digest(token), login["id"], now, expires)
                _audit(conn, login, now, "care_sign_in", "password+code")
                session = {"login": login, "expires_at": expires}
            else:
                repo.code_failed(conn, pending["id"], maximum=CODE_ATTEMPTS, now=now)
    if token is None or session is None:
        raise TmfError(401, INVALID_CODE, code="codeInvalid")
    return token, session


def sign_out(tx, session: CareSession, now: datetime) -> None:
    with tx() as conn:
        repo.revoke_session(conn, session.token_hash, now)


# --- passwords ---


def set_password(tx, request: Request, code: str, password: str, now: datetime) -> None:
    """A link code (verify_email or reset_password) sets the password; every session of the
    login ends and every open code with it."""
    _throttle(tx, request, "link-code", now)
    login = None
    with tx() as conn:
        found = repo.link_code(conn, digest(code))
        if found is not None and found["used_at"] is None and found["expires_at"] > now:
            login = repo.login_by_id(conn, found["customer_login_id"], lock=True)
        if login is not None and login["disabled_at"] is None:
            password_policy(login["email"], password)
            repo.set_password(conn, login["id"], hash_password(password), now)
            repo.end_codes(conn, login["id"], now)
            repo.revoke_sessions(conn, login["id"], now)
            _audit(conn, login, now, "care_password_changed", "code", {"purpose": found["purpose"]})
        else:
            login = None
    if login is None:
        raise TmfError(401, INVALID_CODE, code="codeInvalid")


def request_reset(tx, request: Request, email: str, now: datetime) -> None:
    """Email a link code to an enabled login: `reset_password` once a password was set, else a
    new invitation code (`verify_email`). The answer is the same for any email."""
    _throttle(tx, request, email, now)
    login, purpose, code = None, None, None
    with tx() as conn:
        login = repo.login_by_email(conn, email, lock=True)
        if login is not None and login["disabled_at"] is None:
            purpose = "reset_password" if login["password_hash"] else "verify_email"
            code = _link_code(conn, login, purpose, now)
    if login is not None and purpose is not None and code is not None:
        if not send_code(tx, login, purpose, code, now):
            log.warning("self-care reset code for login %s was not sent", login["id"])


# --- the session check (app/auth.py) ---

SIGNUP = "/self-care/v1/signup"
OPEN = {
    ("POST", "/self-care/v1/session"),
    ("POST", "/self-care/v1/session/verify"),
    ("POST", "/self-care/v1/password"),
    ("POST", "/self-care/v1/password/reset"),
}


def authenticate(request: Request, tx, now: datetime) -> None:
    """Every /self-care route: 404 while the feature is off, the CSRF header on writes, and a
    live customer session except on the sign-in, password and signup endpoints. Operator cookies and
    service credentials mean nothing here, as a customer cookie means nothing elsewhere."""
    if not settings.self_care_enabled:
        raise TmfError(404, "not found")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get(CSRF_HEADER) != "1":
            raise TmfError(403, "X-Bss-Request: 1 required")
    if (request.method, request.url.path) in OPEN:
        return
    path = request.url.path
    if path == SIGNUP or path.startswith(SIGNUP + "/"):
        return  # online signup (#377): no session yet, the signup's id and emailed code
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise TmfError(401, "sign in required")
    with tx() as conn:
        row = repo.live_session(
            conn,
            digest(token),
            now,
            # the idle limit gets the 60 s last_seen_at may lag (never ends a session early)
            now - timedelta(seconds=settings.care_session_idle_seconds + IDLE_SLACK_SECONDS),
        )
        if row is None:
            raise TmfError(401, "session expired or revoked")
        repo.touch_session(
            conn, row["token_hash"], now, now - timedelta(seconds=IDLE_SLACK_SECONDS)
        )
    request.state.care = CareSession(
        row["login_id"], row["customer_id"], row["email"], row["token_hash"], row["expires_at"]
    )
    request.state.operator = OPERATOR
    request.state.role = ROLE


# --- operator side: invite and disable ---


def view(login: Row, now: datetime, sessions: int) -> dict:
    from app.api.common import utc
    from app.api.customer import customer_href

    locked = login["locked_until"] is not None and login["locked_until"] > now
    status = (
        "disabled"
        if login["disabled_at"]
        else "locked"
        if locked
        else "active"
        if login["password_hash"]
        else "invited"
    )
    return {
        "customer": {"id": str(login["customer_id"]), "href": customer_href(login["customer_id"])},
        "email": login["email"],
        "status": status,
        "emailVerifiedAt": None
        if login["email_verified_at"] is None
        else utc(login["email_verified_at"]),
        "lockedUntil": utc(login["locked_until"]) if locked else None,
        "failedAttempts": login["failed_attempts"],
        "disabledAt": None if login["disabled_at"] is None else utc(login["disabled_at"]),
        "activeSessions": sessions,
        "createdBy": login["created_by"],
        "createdAt": utc(login["created_at"]),
        "updatedAt": utc(login["updated_at"]),
    }


def _customer_exists(conn: psycopg.Connection, customer_id: UUID, raw: str) -> None:
    if conn.execute("SELECT 1 FROM customer WHERE id = %s", (customer_id,)).fetchone() is None:
        raise not_found("customer", raw)


def get_login(tx, customer_id: UUID, raw: str, now: datetime) -> dict:
    with tx() as conn:
        _customer_exists(conn, customer_id, raw)
        login = repo.login_of_customer(conn, customer_id)
        if login is None:
            raise TmfError(404, f"customer {raw} has no self-care login")
        return view(login, now, len(repo.live_sessions(conn, login["id"], now)))


def invite(tx, customer_id: UUID, raw: str, email: str, operator: str, now: datetime) -> dict:
    """An operator invites the customer: a login (or a fresh start of an inactive one) and a
    link code to its email to set the password."""
    if not settings.self_care_enabled:
        raise conflict("self-care is disabled (BSS_SELF_CARE_ENABLED)")
    try:
        with tx() as conn:
            _customer_exists(conn, customer_id, raw)
            existing = repo.login_of_customer(conn, customer_id, lock=True)
            if (
                existing is not None
                and existing["disabled_at"] is None
                and existing["password_hash"]
            ):
                raise conflict("the customer has an active self-care login: disable it first")
            login = repo.invite(conn, customer_id, email, operator, now)
            repo.end_codes(conn, login["id"], now)
            repo.revoke_sessions(conn, login["id"], now)
            code = _link_code(conn, login, "verify_email", now)
    except psycopg.errors.UniqueViolation as exc:
        raise conflict("the email is used by another self-care login") from exc
    if not send_code(tx, login, "verify_email", code, now):
        raise _not_sent()
    return view(login, now, 0)


def disable(tx, customer_id: UUID, raw: str, now: datetime) -> dict:
    """Disable the login: no sign-in, its sessions and open codes end at once."""
    with tx() as conn:
        _customer_exists(conn, customer_id, raw)
        login = repo.login_of_customer(conn, customer_id, lock=True)
        if login is None:
            raise TmfError(404, f"customer {raw} has no self-care login")
        login = repo.disable(conn, login["id"], now)
        repo.end_codes(conn, login["id"], now)
        repo.revoke_sessions(conn, login["id"], now)
        return view(login, now, 0)


def session_view(conn_or_tx, session: CareSession) -> dict:
    from app.api.common import utc

    with conn_or_tx() as conn:
        row = (
            conn.cursor(row_factory=dict_row)
            .execute("SELECT name FROM customer WHERE id = %s", (session.customer_id,))
            .fetchone()
        )
    return {
        "customer": {"id": str(session.customer_id), "name": row["name"] if row else None},
        "email": session.email,
        "expiresAt": utc(session.expires_at),
    }
