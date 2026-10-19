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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "user"], default="admin")
    args = parser.parse_args()
    import re

    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", args.username):
        parser.error("invalid username")
    password = (
        getpass.getpass("Password: ")
        if sys.stdin.isatty()
        else sys.stdin.readline(1026).rstrip("\r\n")
    )
    password_policy(args.username, password)
    now = SystemClock().now()
    with db.connection() as conn, conn.transaction():
        lock_users(conn)
        conn.execute(
            "INSERT INTO app_user (username, role, password_hash, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s)",
            (args.username, args.role, hash_password(password), now, now),
        )
        audit(conn, args.username, "bootstrap", "create", now)


if __name__ == "__main__":
    main()
