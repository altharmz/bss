"""Delivering stored events to hub callbacks (the worker, step 6 of a wake-up).

Each due delivery is POSTed on its own, inside a short transaction that holds its row
(SKIP LOCKED): the event exactly as stored (`payload` as JSON text - money stays an
exact number), `Content-Type: application/json`, 5 s timeout, redirects never followed.
A 2xx is delivered; anything else - a status, a timeout, a refused connection, a
callback that now resolves to a private address - is retried after 1 min, 5 min, 30 min,
2 h and 12 h, then the delivery is `failed` with its last error. Six attempts at most.

Outside dev and test (app/events/callbacks.py) the callback is resolved and checked
again right before the POST, and the connection goes to that checked address (TLS still
verifies the certificate for the host name): DNS cannot point an accepted callback at
the VPN host or the shared Docker network between the check and the request.
"""

import http.client
import logging
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import psycopg

from app.clock import Clock
from app.events import callbacks
from app.repository import events as repo

log = logging.getLogger("app.events")

TIMEOUT = 5.0
BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
BATCH = 50

# (callback URL, JSON body) -> None on success, else why it failed
Sender = Callable[[str, bytes], str | None]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a checked address, with SNI and certificate checks for the host name."""

    def __init__(self, host: str, port: int, address: str, **kwargs) -> None:
        super().__init__(host, port, **kwargs)
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def post(url: str, body: bytes) -> str | None:
    """POST `body` to `url`; None on a 2xx, else the reason. Never raises."""
    try:
        addresses = callbacks.check(url)
    except callbacks.CallbackRefused as exc:
        return f"refused: {exc}"
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    connection: http.client.HTTPConnection
    if parts.scheme == "https":
        context = ssl.create_default_context()
        connection = (
            _PinnedHTTPSConnection(host, port, addresses[0], timeout=TIMEOUT, context=context)
            if addresses
            else http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=context)
        )
    else:  # dev and test only: callbacks.check refuses http elsewhere
        connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json", "User-Agent": "bss-events/1"},
        )
        response = connection.getresponse()
        response.read(65536)
        if 200 <= response.status < 300:
            return None
        return f"HTTP {response.status} {response.reason}".strip()
    except (OSError, http.client.HTTPException) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        connection.close()


def retry_at(attempts_before: int, now: datetime) -> datetime | None:
    """When to try again after attempt number `attempts_before + 1` failed; None: give up."""
    if attempts_before >= len(BACKOFF):
        return None
    return now + BACKOFF[attempts_before]


@dataclass
class Delivered:
    delivered: list[UUID] = field(default_factory=list)
    retrying: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)


def process(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    sender: Sender = post,
    limit: int = BATCH,
    checkpoint: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> Delivered:
    """Send up to `limit` due deliveries, one transaction each, oldest first."""
    result = Delivered()
    for _ in range(limit):
        if checkpoint is not None and checkpoint():
            break
        with conn.transaction():
            due = repo.due(conn, clock.now(), 1)
            if not due:
                break
            (delivery,) = due
            error = sender(delivery["callback"], delivery["body"].encode())
            now = clock.now()
            if error is None:
                repo.delivered(conn, delivery["id"], now)
                result.delivered.append(delivery["id"])
            else:
                again = retry_at(delivery["attempts"], now)
                repo.attempt_failed(conn, delivery["id"], at=now, error=error, retry_at=again)
                (result.retrying if again else result.failed).append(delivery["id"])
                log.warning(
                    "event %s to hub %s: %s (%s)",
                    delivery["event_type"],
                    delivery["hub_id"],
                    error,
                    "retrying" if again else "giving up",
                )
        if heartbeat is not None:
            heartbeat()
    return result
