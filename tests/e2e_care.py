"""Private browser-test service. Run explicitly with pytest in the e2e-care container.

The HTTP server serves the real self-care API on the pytest fixture's disposable database.
The browser runner reads the test plan and emailed code from its shared fixture directory.
"""

import json
import threading
import time
from pathlib import Path

import uvicorn
from starlette.responses import Response

from app.integrations.notify import adapter as sender
from app.main import app
from tests.test_care_signup import (  # noqa: F401 - fixtures
    enabled,
    identity_stub,
    last_code,
    log_sender,
    shop,
    stock,
    stubs,
    wake,
)

ROOT = Path("/e2e")


async def customer_app(scope, receive, send):
    if scope["path"].startswith("/self-care/v1/"):
        await app(scope, receive, send)
    else:
        await Response(status_code=404)(scope, receive, send)


def test_browser_signup(api, db_conn, shop):  # noqa: F811 - fixture
    stock(api)
    mailbox = ROOT / "care-mail.json"
    ready = ROOT / "care-plan.json"
    done = ROOT / "care-finished"
    files = [mailbox, ready, done]
    for path in files:
        path.unlink(missing_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(customer_app, host="0.0.0.0", port=8080, lifespan="off", access_log=False)  # noqa: S104 - private Compose network
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if server.started and not ready.exists():
                ready.write_text(json.dumps({"id": shop["id"]}))
            messages = sender().sent
            if messages and not mailbox.exists():
                assert messages[0].to == "browser-signup@example.test"
                mailbox.write_text(json.dumps({"code": last_code()}))
            if mailbox.exists():
                wake(api, db_conn)
            if done.exists():
                assert any(message.subject == "Your eSIM is ready" for message in messages)
                return
            time.sleep(1)
        raise AssertionError("browser signup timed out")
    finally:
        server.should_exit = True
        thread.join(10)
        for path in files:
            path.unlink(missing_ok=True)
