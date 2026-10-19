"""Local browser-test identities; generated credentials never enter source or logs."""

import argparse
import json
import secrets
from pathlib import Path
from urllib.parse import urlparse

from app import db
from app.clock import SystemClock
from app.config import settings
from app.users import audit, hash_password, lock_users

FILE = Path("/e2e/credentials.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "cleanup"])
    args = parser.parse_args()
    if settings.app_env != "dev" or urlparse(settings.database_url).hostname != "db":
        parser.error("browser fixtures require APP_ENV=dev and the local db")
    now = SystemClock().now()
    with db.connection() as conn, conn.transaction():
        lock_users(conn)
        if args.action == "prepare":
            if FILE.exists():
                parser.error("clean up the previous browser fixtures first")
            identities = {}
            for role in ("admin", "user"):
                username = f"e2e-{role}-{secrets.token_hex(8)}"
                password = secrets.token_urlsafe(32)
                conn.execute(
                    "INSERT INTO app_user (username, role, password_hash, must_change_password,"
                    " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                    (username, role, hash_password(password), role == "user", now, now),
                )
                audit(conn, username, "e2e:fixture", "create", now)
                identities[role] = {"username": username, "password": password}
            # Both local containers run as UID 1000; credentials need no other readers.
            FILE.touch(mode=0o600, exist_ok=False)
            FILE.write_text(json.dumps(identities))
        elif FILE.exists():
            for identity in json.loads(FILE.read_text()).values():
                username = identity["username"]
                if not username.startswith("e2e-"):
                    parser.error("refusing to clean up a non-fixture user")
                conn.execute("DELETE FROM app_session WHERE username = %s", (username,))
                updated = conn.execute(
                    "UPDATE app_user SET disabled = true, updated_at = %s WHERE username = %s",
                    (now, username),
                )
                # Preparation may have been interrupted before its DB commit.
                if updated.rowcount:
                    audit(conn, username, "e2e:fixture", "disabled", now)
            FILE.unlink()


if __name__ == "__main__":
    main()
