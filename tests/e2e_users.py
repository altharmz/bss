"""Local browser-test identities; generated credentials never enter source or logs."""

import argparse
import json
import secrets
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app import db
from app.clock import FixedClock, SystemClock
from app.config import settings
from app.users import audit, hash_password, lock_users

FILE = Path("/e2e/credentials.json")
CATALOG_FILE = Path("/e2e/numbered-offering.json")


def numbered_offering(client):
    """A fresh, unordered offering; the browser attaches its UI-created specification.

    Like the demo seed, prepare prices with an in-process clock at business midnight.
    Real-time API calls cannot create a price valid earlier today, and a price created
    just before an offering cannot subsequently attach without becoming backdated.
    """
    name = f"Numbered browser fixture {secrets.token_hex(8)}"
    response = client.request(
        "POST",
        "/tmf-api/productCatalogManagement/v5/productOffering",
        json={
            "@type": "ProductOffering",
            "name": name,
            "lifecycleStatus": "Active",
            "isSellable": True,
            "productOfferingPrice": [
                {
                    "@type": "ProductOfferingPrice",
                    "name": name,
                    "lifecycleStatus": "Active",
                    "priceType": "recurring",
                    "recurringChargePeriodType": "monthly",
                    "price": {"unit": settings.ui_currency, "value": 1},
                }
            ],
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"browser catalog fixture failed: HTTP {response.status_code}")
    return {"id": response.json()["id"], "currency": settings.ui_currency}


def prepare_numbered_catalog():
    from fastapi.testclient import TestClient

    from app.api.deps import get_clock
    from app.main import app

    clock = FixedClock(
        datetime.combine(SystemClock().today(), time.min, ZoneInfo(settings.billing_tz))
    )
    previous_secret = settings.bss_service_secret
    previous_clock = app.dependency_overrides.get(get_clock)
    settings.bss_service_secret = secrets.token_urlsafe(32)
    app.dependency_overrides[get_clock] = lambda: clock
    try:
        with TestClient(
            app,
            headers={
                "X-Bss-Service": settings.bss_service_secret,
                "X-Remote-User": "mcp-token:e2e",
            },
        ) as client:
            return numbered_offering(client)
    finally:
        settings.bss_service_secret = previous_secret
        if previous_clock is None:
            app.dependency_overrides.pop(get_clock, None)
        else:
            app.dependency_overrides[get_clock] = previous_clock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "cleanup"])
    parser.add_argument("--numbered-offering", action="store_true")
    args = parser.parse_args()
    if settings.app_env != "dev" or urlparse(settings.database_url).hostname != "db":
        parser.error("browser fixtures require APP_ENV=dev and the local db")
    now = SystemClock().now()
    with db.connection() as conn, conn.transaction():
        lock_users(conn)
        if args.action == "prepare":
            if FILE.exists():
                parser.error("clean up the previous browser fixtures first")
            if args.numbered_offering:
                fixture = prepare_numbered_catalog()
                CATALOG_FILE.write_text(json.dumps(fixture))
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
    if args.action == "cleanup":
        CATALOG_FILE.unlink(missing_ok=True)
        for name in ("care-plan.json", "care-mail.json", "care-finished"):
            (FILE.parent / name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
