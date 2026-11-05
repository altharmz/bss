"""User storage helpers and the first-admin CLI (password on stdin)."""

import argparse
import getpass
import sys

from app import db
from app.api.errors import unprocessable
from app.clock import SystemClock
from bss_mcp.passwords import check_password, hash_password

__all__ = ["check_password", "hash_password", "password_policy", "audit", "lock_users"]


def password_policy(username: str, password: str) -> None:
    if len(password) < 12 or len(password) > 1024 or password.casefold() == username.casefold():
        raise unprocessable("password must be 12–1024 characters and differ from the username")


def lock_users(conn) -> None:
    # Serialize administration, password changes and bootstrap: concurrent demotions
    # cannot each observe the other as the remaining administrator.
    conn.execute("LOCK TABLE app_user IN EXCLUSIVE MODE")


def audit(conn, username, operator, action, now) -> None:
    conn.execute(
        "INSERT INTO app_user_audit (username, operator, action, occurred_at) VALUES (%s,%s,%s,%s)",
        (username, operator, action, now),
    )


def disable(username: str, now) -> None:
    """Disable a user as the Users screen does: sessions revoked, MCP grants ended (version
    bump), audited; the last active administrator stays. Used to retire smoke users."""
    with db.connection() as conn, conn.transaction():
        lock_users(conn)
        row = conn.execute(
            "SELECT role, disabled FROM app_user WHERE username = %s", (username,)
        ).fetchone()
        if row is None:
            sys.exit(f"no such user: {username}")
        if row[0] == "admin" and not row[1]:
            active = conn.execute(
                "SELECT count(*) FROM app_user WHERE role = 'admin' AND NOT disabled"
            ).fetchone()[0]
            if active <= 1:
                sys.exit("cannot disable the last active administrator")
        conn.execute(
            "UPDATE app_user SET disabled = true, updated_at = %s,"
            " credential_version = credential_version + 1 WHERE username = %s",
            (now, username),
        )
        conn.execute("DELETE FROM app_session WHERE username = %s", (username,))
        audit(conn, username, "cli", "disabled", now)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "disable"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "user", "mcp"], default="admin")
    args = parser.parse_args()
    import re

    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", args.username):
        parser.error("invalid username")
    if args.action == "disable":
        disable(args.username, SystemClock().now())
        return
    password = (
        getpass.getpass("Password: ")
        if sys.stdin.isatty()
        else sys.stdin.readline(1026).rstrip("\r\n")
    )
    password_policy(args.username, password)
    now = SystemClock().now()
    with db.connection() as conn, conn.transaction():
        lock_users(conn)
        # an `mcp` user keeps the password set here; UI roles change it at first sign-in
        conn.execute(
            "INSERT INTO app_user (username, role, password_hash, must_change_password,"
            " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (args.username, args.role, hash_password(password), args.role != "mcp", now, now),
        )
        audit(conn, args.username, "bootstrap", "create", now)


if __name__ == "__main__":
    main()
