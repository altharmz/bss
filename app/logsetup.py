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
import time as monotonic
import uuid
from contextvars import ContextVar
from typing import IO, Any

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
