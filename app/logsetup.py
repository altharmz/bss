"""Structured logging shared by the app, the worker and the MCP connector (issue #273).

JSON lines on stdout, one per event; a request ID (contextvar) on every line of a request;
a redaction safety net on the handler; third-party loggers pinned at WARNING because their
DEBUG output can carry SQL parameters and headers. Decisions: docs/decisions/infra.md,
"Logging". Rule number one is still that no code passes headers, cookies, bodies,
passwords or tokens to a log call: `RedactFilter` is only the net under it.
"""

import json
import logging
import re
import sys
import threading
import time as monotonic
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import IO, Any

import psycopg
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

# our loggers get LOG_LEVEL (#274 changes only these); everything else stays at WARNING
OUR_LOGGERS = ("app", "bss_mcp", "uvicorn.error")
REQUEST_ID_HEADER = "X-Request-Id"
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# no request line at INFO for these (the same set as app.metrics.EXCLUDED)
QUIET_PATHS = {"/metrics", "/healthz"}
EXTRA_FIELDS = ("method", "route", "status", "duration_ms", "operator")
REDACTED = "[redacted]"

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_HEADER_VALUE = re.compile(
    r"""(?ix)\b(authorization|set-cookie|cookie|x-bss-service)(["']?\s*[:=]\s*["']?)[^\r\n"']*"""
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_PAIR = re.compile(
    r"""(?ix)(["']?\b(?:[\w-]*?(?:password|passwd|token|secret)|pin|code)["']?\s*[:=]\s*["']?)"""
    r"""[^\s"',&;}]+"""
)


def redact(text: str) -> str:
    text = _HEADER_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    return _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{REDACTED}", text)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class RedactFilter(logging.Filter):
    """Masks secrets in the message (arguments already merged) and in string extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        for field in EXTRA_FIELDS:
            value = record.__dict__.get(field)
            if isinstance(value, str):
                record.__dict__[field] = redact(value)
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, process: str) -> None:
        super().__init__()
        self.process = process

    def format(self, record: logging.LogRecord) -> str:
        # logging's own timestamp, UTC, milliseconds: not a wall-clock read of ours
        ts = monotonic.strftime("%Y-%m-%dT%H:%M:%S", monotonic.gmtime(record.created))
        entry: dict[str, Any] = {
            "ts": f"{ts}.{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "process": self.process,
        }
        rid = getattr(record, "request_id", None)
        if rid:
            entry["request_id"] = rid
        for field in EXTRA_FIELDS:
            value = record.__dict__.get(field)
            if value is not None:
                entry[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


_HANDLER_MARK = "_bss_json_handler"


def configure(process: str, stream: IO[str] | None = None) -> logging.Handler:
    """Replaces logging.basicConfig in each process; safe to call more than once."""
    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(JsonFormatter(process))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    level = settings.log_level.upper()
    for name in OUR_LOGGERS:
        logging.getLogger(name).setLevel(level)
    # uvicorn's own handlers would print plain text; its records go through ours instead
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # the request line replaces it
    return handler


def effective_level(
    base_level: str | None, debug_until: datetime | None, env_level: str, now: datetime
) -> str:
    """DEBUG while now < debug_until (half-open: at the instant it is over), else the base
    level from the database, else the LOG_LEVEL of the environment."""
    if debug_until is not None and now < debug_until:
        return "DEBUG"
    return (base_level or env_level).upper()


class LevelPoller:
    """Keeps the level of OUR_LOGGERS equal to the runtime setting (issue #274).

    `observe` takes a reading of the setting, `outage` a failed one. Between readings the
    process's own clock still ends an expired debug: a failed poll keeps the level, but never
    past `debug_until`. Third-party loggers are never touched. Start with `start` (a daemon
    thread polling `fetch`) or drive `observe` / `outage` yourself (the MCP's async task)."""

    def __init__(
        self,
        now: Callable[[], datetime],
        env_level: str,
        fetch: Callable[[], tuple[str | None, datetime | None]] | None = None,
        interval: float = 15,
    ) -> None:
        self.now = now
        self.env_level = env_level.upper()
        self.fetch = fetch
        self.interval = interval
        self.level = self.env_level
        self.base_level: str | None = None
        self.debug_until: datetime | None = None
        self.down = False
        self.log = logging.getLogger("app.logsetup")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def observe(self, base_level: str | None, debug_until: datetime | None) -> None:
        if self.down:
            self.down = False
            self.log.warning("log level setting readable again")
        self.base_level, self.debug_until = base_level, debug_until
        self._apply()

    def outage(self, exc: BaseException) -> None:
        if not self.down:
            self.down = True
            self.log.warning(
                "log level setting unreadable (%s); keeping %s", type(exc).__name__, self.level
            )
        self._apply()

    def _apply(self) -> None:
        level = effective_level(self.base_level, self.debug_until, self.env_level, self.now())
        if level == self.level:
            return
        until = f" until {self.debug_until.isoformat()}" if level == "DEBUG" else ""
        # one WARNING per change and process, logged while the old level still applies to it
        self.log.warning("log level %s -> %s%s", self.level, level, until)
        self.level = level
        for name in OUR_LOGGERS:
            logging.getLogger(name).setLevel(level)

    def poll_once(self) -> None:
        assert self.fetch is not None
        try:
            base_level, debug_until = self.fetch()
        except Exception as exc:  # noqa: BLE001 - any failure keeps the level
            self.outage(exc)
        else:
            self.observe(base_level, debug_until)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def start(self) -> "LevelPoller":
        self._thread = threading.Thread(target=self._run, name="log-level-poller", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        close = getattr(self.fetch, "close", None)
        if close is not None:
            close()


class DbLevelSource:
    """Reads log_setting over its own autocommit connection (reconnects after an error)."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.database_url
        self.conn: psycopg.Connection | None = None

    def __call__(self) -> tuple[str | None, datetime | None]:
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg.connect(
                    self.url, autocommit=True, connect_timeout=settings.db_connect_timeout
                )
            row = self.conn.execute("SELECT base_level, debug_until FROM log_setting").fetchone()
        except Exception:
            self.close()
            raise
        return (row[0], row[1]) if row else (None, None)

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001, S110 - closing a broken connection
                pass
            self.conn = None


def start_db_poller(now: Callable[[], datetime]) -> LevelPoller | None:
    """The app's and the worker's poller (None when LOG_POLL_SECONDS is 0)."""
    if settings.log_poll_seconds == 0:
        return None
    return LevelPoller(
        now, settings.log_level, DbLevelSource(), interval=settings.log_poll_seconds
    ).start()


class RequestLogMiddleware:
    """Pure ASGI, outermost. Takes or makes the request ID, returns it as X-Request-Id and
    logs one line per request: method, route template, status, duration, operator. Never the
    query string, headers or body.

    The contextvar is deliberately not reset: every request runs in its own task, and the
    500 handler and Starlette's outer error middleware run after this one has returned
    through an exception - they still need the ID."""

    def __init__(self, app: ASGIApp, logger_name: str = "app.request") -> None:
        self.app = app
        self.log = logging.getLogger(logger_name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = ""
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                inbound = value.decode("latin-1")
                break
        rid = inbound if VALID_REQUEST_ID.match(inbound) else uuid.uuid4().hex
        request_id.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        status = {"code": 500}  # an exception before the response start is a 500

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k != b"x-request-id"]
                headers.append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        started = monotonic.perf_counter()  # a monotonic duration clock, as in app.metrics
        try:
            await self.app(scope, receive, capture)
        finally:
            elapsed = monotonic.perf_counter() - started
            code = status["code"]
            if scope["path"] in QUIET_PATHS:
                level = logging.DEBUG
            elif code >= 500:
                level = logging.ERROR
            else:
                level = logging.INFO
            self.log.log(
                level,
                "%s %s %s",
                scope["method"],
                getattr(scope.get("route"), "path", None) or "unmatched",
                code,
                extra={
                    "method": scope["method"],
                    "route": getattr(scope.get("route"), "path", None) or "unmatched",
                    "status": code,
                    "duration_ms": round(elapsed * 1000, 1),
                    "operator": (scope.get("state") or {}).get("operator"),
                },
            )
