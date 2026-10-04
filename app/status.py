"""GET /billing-status: the freshness signal for the external monitor (stage 4,
docs/stage4-design.md section 8; D-W7, D-W12, D-W19, Q6).

Unauthenticated (a Caddy carve-out like /healthz), so it exposes only the latest run's
date, a status and an age - no amounts, no names, no counts. Deliberately not part of
/healthz: a late billing run must not mark the app unhealthy (the deploy gate and
Docker's healthcheck use /healthz). One indexed query.

    200 {"date": "2026-10-01", "status": "ok", "ageSeconds": 3600}
    503 {"date": "2026-09-30", "status": "stale" | "failed" | "running", "ageSeconds": ...}
    503 {"date": null, "status": "none", "ageSeconds": null}       no run at all (Q6)

The expected date is today once the local time (BILLING_TZ) is past BILLING_RUN_AT plus
a grace of 2 hours, yesterday before that (D-W19). The latest run decides:

    failed                                   503 failed (accounts or the whole run)
    running, for less than the grace         200 running (in progress, not yet overdue)
    running, for the grace or longer         503 running
    completed, the expected date or later    200 ok
    completed, an earlier date               503 stale

ageSeconds: since the run finished, or since it started if it has not.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import ClockDep, Tx
from app.config import settings
from app.internal.context import no_query
from app.repository._sql import Row, fetch_one

GRACE = timedelta(hours=2)

router = APIRouter()


def expected_date(local_now: datetime, run_at: time) -> date:
    """The latest run date that should be completed at `local_now` (BILLING_TZ)."""
    today = local_now.date()
    deadline = datetime.combine(today, run_at, tzinfo=local_now.tzinfo) + GRACE
    return today if local_now >= deadline else today - timedelta(days=1)


def freshness(
    latest: Row | None, now: datetime, tz: ZoneInfo, run_at: time
) -> tuple[int, dict[str, Any]]:
    """The HTTP status and the body for the latest run (pure)."""
    if latest is None:
        return 503, {"date": None, "status": "none", "ageSeconds": None}
    since = latest["finished_at"] or latest["started_at"]
    age = max(0, int((now - since).total_seconds()))
    if latest["status"] == "failed":
        status, code = "failed", 503
    elif latest["status"] == "running":
        overdue = now - latest["started_at"] >= GRACE
        status, code = "running", 503 if overdue else 200
    elif latest["run_date"] >= expected_date(now.astimezone(tz), run_at):
        status, code = "ok", 200
    else:
        status, code = "stale", 503
    return code, {"date": latest["run_date"].isoformat(), "status": status, "ageSeconds": age}


@router.get("/billing-status")
def billing_status(request: Request, tx: Tx, clock: ClockDep) -> JSONResponse:
    no_query(request)
    with tx() as conn:
        latest = fetch_one(conn, "SELECT * FROM billing_run ORDER BY run_date DESC LIMIT 1")
    code, body = freshness(
        latest,
        clock.now(),
        ZoneInfo(settings.billing_tz),
        time.fromisoformat(settings.billing_run_at),
    )
    return JSONResponse(status_code=code, content=body)
