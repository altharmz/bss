# ruff: noqa: E402 - PROMETHEUS_MULTIPROC_DIR must be set before prometheus_client loads
"""Prometheus metrics for the app (issue #240; docs/operations.md, "Metrics").

One non-default registry with the process and platform collectors, the HTTP metrics of
`HttpMetricsMiddleware`, and `DbCollector`, which reads queue depth, billing freshness,
product counts and the rows of the DEFAULT usage partition from the database at scrape
time (cached for 30 s). `/metrics` is off (404) unless BSS_METRICS_TOKEN is set, then needs
`Authorization: Bearer <token>`; the check lives in `app/auth.py` so the route-protection
test covers it. Not part of the API contract:
not in the OpenAPI schema, so no MCP tool and no scope item.
"""

import logging
import os
import threading
import time as monotonic
from collections.abc import Iterable
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Multiprocess mode, always (one code path for 1 or WEB_CONCURRENCY processes, #288): each
# process writes its values to files here and a scrape adds them up. prometheus_client picks
# the mode when it is first imported, so this comes before that import. /tmp is the
# container's tmpfs: empty at every start.
MULTIPROC_DIR = os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus")  # noqa: S108
Path(MULTIPROC_DIR).mkdir(parents=True, exist_ok=True)

from fastapi import APIRouter, Depends, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector
from psycopg.rows import dict_row
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import db
from app.api.deps import get_clock
from app.clock import Clock, SystemClock
from app.config import settings
from app.status import freshness

log = logging.getLogger("app.metrics")

METRICS_PATH = "/metrics"
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
CACHE_SECONDS = 30

# The process's own metrics are registered nowhere: their values live in MULTIPROC_DIR and
# the scrape registry (below) reads every process's files. No ProcessCollector or
# PlatformCollector: they describe one process and are wrong when summed (container CPU
# and memory come from docker stats).
BUILD_INFO = Gauge(
    "bss_build_info", "Build version.", ["version"], registry=None, multiprocess_mode="livesum"
)
REQUESTS = Counter(
    "bss_http_requests_total",
    "HTTP requests by method, matched route template and status.",
    ["method", "route", "status"],
    registry=None,
)
DURATION = Histogram(
    "bss_http_request_duration_seconds",
    "HTTP request duration by method and matched route template.",
    ["method", "route"],
    buckets=BUCKETS,
    registry=None,
)
IN_PROGRESS = Gauge(
    "bss_http_requests_in_progress",
    "HTTP requests being served.",
    registry=None,
    multiprocess_mode="livesum",
)
APP_METRICS = {"bss_build_info", "bss_http_requests", "bss_http_request_duration_seconds"}
REJECTED_OVERLOAD = Counter(
    "bss_http_rejected_overload",
    "HTTP requests refused with 503 at the per-process bound BSS_MAX_IN_FLIGHT.",
    registry=None,
)
IN_FLIGHT = Gauge(
    "bss_http_in_flight",
    "HTTP requests admitted under the bound and being served (app/overload.py).",
    registry=None,
    multiprocess_mode="livesum",
)
APP_METRICS |= {"bss_http_requests_in_progress", "bss_http_rejected_overload"}
APP_METRICS |= {"bss_http_in_flight"}


class AppProcesses(Collector):
    """Every app process's values from MULTIPROC_DIR, summed, and only the app's own
    series: in tests the MCP connector's metrics share the process and the directory."""

    def __init__(self) -> None:
        self._all = MultiProcessCollector(None, MULTIPROC_DIR)

    def collect(self) -> Iterable:
        return [family for family in self._all.collect() if family.name in APP_METRICS]


# Scrape and probe traffic is not request traffic.
EXCLUDED = {METRICS_PATH, "/healthz"}


def set_build_info() -> None:
    BUILD_INFO.clear()
    BUILD_INFO.labels(settings.build).set(1)


class HttpMetricsMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware: no extra task per request, no body buffering)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXCLUDED:
            await self.app(scope, receive, send)
            return
        status = {"code": 500}  # an exception before the response start is a 500

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        IN_PROGRESS.inc()
        # perf_counter is a monotonic duration clock, not the wall clock: the rule that only
        # app/clock.py reads the wall clock is about dates and instants (tests/test_clock.py)
        started = monotonic.perf_counter()
        try:
            await self.app(scope, receive, capture)
        finally:
            elapsed = monotonic.perf_counter() - started
            IN_PROGRESS.dec()
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            REQUESTS.labels(scope["method"], route, str(status["code"])).inc()
            DURATION.labels(scope["method"], route).observe(elapsed)


# queue -> (pending SQL condition, failed SQL condition or None, timestamp column the age
# counts from). Statuses follow docs/db/<table>.md; a final business outcome (declined,
# refused, cancelled) is not a failure of the queue.
QUEUES: dict[str, tuple[str, str | None, str]] = {
    # no status column: done_at IS NULL is pending; a retry request is not a failure
    "worker_request": ("done_at IS NULL", None, "requested_at"),
    # pending = to send or retried; exhausted = automatic attempts used up, an operator retries
    "network_command": ("state = 'pending'", "state = 'exhausted'", "created_at"),
    # pending or sent (the gateway's result is awaited) are open; exhausted as above
    "payment_command": ("state IN ('pending', 'sent')", "state = 'exhausted'", "created_at"),
    # pending = not yet attempted; failedTechnical = last attempt failed, awaiting its retry
    "prepaid_command": ("state = 'pending'", "state = 'failedTechnical'", "created_at"),
    # initial = queued, inProgress = a send failed and a retry is scheduled; failed = used up
    "notification_message": (
        "state IN ('initial', 'inProgress')",
        "state = 'failed'",
        "created_at",
    ),
    # pending = to deliver (next_attempt_at set exactly while pending); failed = retries used up
    "event_delivery": ("state = 'pending'", "state = 'failed'", "created_at"),
}


def queue_query(name: str) -> str:
    """A queue's pending count, oldest pending time and failed count. Each condition is in
    its own WHERE, so the queue's partial index serves it instead of a scan of the whole
    table, completed rows included (#311)."""
    pending, failed, since = QUEUES[name]
    failed_sql = f"(SELECT count(*) FROM {name} WHERE {failed})" if failed else "0"  # noqa: S608
    return f"SELECT count(*), min({since}), {failed_sql} FROM {name} WHERE {pending}"  # noqa: S608


BILLING_STATUSES = ("ok", "running", "stale", "failed", "none")


class DbCollector(Collector):
    """Database-backed series, read at scrape time. One read-only transaction with a 2 s
    statement timeout; the result is reused for 30 s. On any error only `bss_db_up 0` is
    exported (never stale values)."""

    def __init__(self) -> None:
        self.clock: Clock = SystemClock()
        self._lock = threading.Lock()
        self._at: datetime | None = None
        self._families: list[GaugeMetricFamily] = []

    def collect(self) -> Iterable[GaugeMetricFamily]:
        now = self.clock.now()
        with self._lock:
            if self._at is None or not (
                timedelta(0) <= now - self._at < timedelta(seconds=CACHE_SECONDS)
            ):
                self._families = self._read(now)
                self._at = now
            return list(self._families)

    @staticmethod
    def _up(value: int) -> GaugeMetricFamily:
        up = GaugeMetricFamily("bss_db_up", "1 when the database answered the scrape.")
        up.add_metric([], value)
        return up

    def _read(self, now: datetime) -> list[GaugeMetricFamily]:
        try:
            return [self._up(1), *self._query(now)]
        except Exception as exc:  # noqa: BLE001 - any failure means "db down" for this scrape
            log.warning("metrics: database read failed: %s", exc)
            return [self._up(0)]

    def _query(self, now: datetime) -> list[GaugeMetricFamily]:
        with db.connection() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '2s'")
            latest = (
                conn.cursor(row_factory=dict_row)
                .execute(
                    "SELECT * FROM billing_run WHERE kind = 'daily' ORDER BY run_date DESC LIMIT 1"
                )
                .fetchone()
            )
            queues = {}
            for name in QUEUES:
                queues[name] = conn.execute(queue_query(name)).fetchone()
            products = conn.execute(
                "SELECT status::text, count(*) FROM product GROUP BY 1"
            ).fetchall()
            # the safety net of a missing partition (#282): should stay empty, so cheap
            default_rows = conn.execute("SELECT count(*) FROM usage_record_default").fetchone()
            conn.rollback()

        families = []
        _, body = freshness(
            latest,
            now,
            ZoneInfo(settings.billing_tz),
            time.fromisoformat(settings.billing_run_at),
        )
        status = GaugeMetricFamily(
            "bss_billing_last_run_status",
            "1 for the current billing freshness status.",
            labels=["status"],
        )
        for name in BILLING_STATUSES:
            status.add_metric([name], 1 if body["status"] == name else 0)
        families.append(status)
        if body["ageSeconds"] is not None:
            age = GaugeMetricFamily(
                "bss_billing_last_run_age_seconds",
                "Seconds since the latest daily run finished (or started).",
            )
            age.add_metric([], body["ageSeconds"])
            families.append(age)

        pending = GaugeMetricFamily("bss_queue_pending", "Open rows per queue.", labels=["queue"])
        oldest = GaugeMetricFamily(
            "bss_queue_oldest_pending_age_seconds", "Age of the oldest open row.", labels=["queue"]
        )
        failed = GaugeMetricFamily(
            "bss_queue_failed", "Rows that need an operator.", labels=["queue"]
        )
        for name, (count, first, failures) in queues.items():
            pending.add_metric([name], count)
            oldest.add_metric([name], max(0, (now - first).total_seconds()) if first else 0)
            if QUEUES[name][1]:
                failed.add_metric([name], failures)
        families += [pending, oldest, failed]

        by_status = GaugeMetricFamily("bss_products", "Products by status.", labels=["status"])
        for name, count in products:
            by_status.add_metric([name], count)
        families.append(by_status)

        unpartitioned = GaugeMetricFamily(
            "bss_usage_record_default_rows",
            "Usage records in the DEFAULT partition (a month without its partition); alert if > 0.",
        )
        unpartitioned.add_metric([], default_rows[0])
        families.append(unpartitioned)
        return families


DB_COLLECTOR = DbCollector()
REGISTRY = CollectorRegistry()
REGISTRY.register(AppProcesses())
REGISTRY.register(DB_COLLECTOR)

router = APIRouter()
_scrape = threading.Lock()


def render(clock: Clock) -> bytes:
    with _scrape:  # the collector reads the request's clock (tests move it)
        DB_COLLECTOR.clock = clock
        return generate_latest(REGISTRY)


@router.get(METRICS_PATH, include_in_schema=False)
def metrics(clock: Clock = Depends(get_clock)) -> Response:
    return Response(render(clock), media_type=CONTENT_TYPE)
