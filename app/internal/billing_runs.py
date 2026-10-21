"""Billing runs for the admin UI (stage 4, docs/archive/stage4-design.md section 7), under
/internal/v1/billingRun. Ours, not TMF.

Read: the runs newest first, one run, a run's per-account outcomes - every count derived
from billing_run_account and the run's members (accounts opened before 00:00 of its date).
Write: **the API never bills.** A catch-up or a retry is a worker_request row; the worker
(which owns billing) carries it out within a minute.

- POST /billingRun/catchUp {upTo}: upTo must be the business date or earlier, and today
  only from BILLING_RUN_AT on (Q5: the run of D is due at 00:30, not at 00:00) - else 422.
  202 with the queued request and the dates it will run; 200 with no dates (and nothing
  queued) when nothing is due.
- POST /billingRun/{id}/retry: any failed run (Q3), 409 otherwise or when a retry of it is
  already queued; 202 with the request. The worker keeps every account's dates in order:
  retrying D+1 retries only the accounts that failed on D+1.
"""

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import field_validator

from app.api.common import TmfModel, money, parse, parse_id, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import bad_request, conflict, not_found, unprocessable
from app.api.listing import parse_page
from app.api.tmfjson import TmfJSONResponse
from app.config import settings
from app.internal.context import no_query, operator_of
from app.repository import billing, worker_requests
from app.repository._sql import Row
from app.worker.plan import catch_up_dates

BASE = "/internal/v1/billingRun"
router = APIRouter(prefix=BASE)

OUTCOMES = ("ok", "failed", "blocked")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _when(value: datetime | None) -> datetime | None:
    return None if value is None else utc(value)


def run_body(row: Row, charges: list[Row]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "runDate": row["run_date"],
        "status": row["status"],
        "failureKind": row["failure_kind"],
        "trigger": row["trigger"],
        "attempt": row["attempt"],
        "startedAt": utc(row["started_at"]),
        "finishedAt": _when(row["finished_at"]),
        "heartbeatAt": _when(row["heartbeat_at"]),
        "accounts": {
            "total": row["total"],
            "ok": row["ok"],
            "failed": row["failed"],
            "blocked": row["blocked"],
        },
        "charges": {
            "count": sum(c["n"] for c in charges),
            "amounts": [money(c["amount"], c["currency"]) for c in charges],
        },
        "error": row["error"],
        "isLatest": row["is_latest"],
    }


def request_body(row: Row, run: Row | None) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "upTo": row["up_to"],
        "run": None if run is None else {"id": str(run["id"]), "runDate": run["run_date"]},
        "requestedAt": utc(row["requested_at"]),
        "requestedBy": row["requested_by"],
        "pickedAt": _when(row["picked_at"]),
        "doneAt": _when(row["done_at"]),
    }


def _page(items: list[dict[str, Any]], total: int) -> TmfJSONResponse:
    return TmfJSONResponse(
        items, headers={"X-Total-Count": str(total), "X-Result-Count": str(len(items))}
    )


@router.get("")
def list_runs(request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request)
    with tx() as conn:
        rows, total = billing.page_runs(conn, offset=offset, limit=limit)
        charges = billing.run_charges(conn, [row["id"] for row in rows])
    return _page([run_body(row, charges[row["id"]]) for row in rows], total)


@router.get("/{run_id}")
def get_run(run_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        row = billing.run_view(conn, parse_id("billingRun", run_id))
        if row is None:
            raise not_found("billingRun", run_id)
        charges = billing.run_charges(conn, [row["id"]])
    return TmfJSONResponse(run_body(row, charges[row["id"]]))


@router.get("/{run_id}/accounts")
def run_accounts(run_id: str, request: Request, tx: Tx) -> TmfJSONResponse:
    offset, limit = parse_page(request, extra=("status",))
    statuses = request.query_params.getlist("status")
    if len(statuses) > 1:
        raise bad_request("status given more than once")
    status = statuses[0] if statuses else None
    if status is not None and status not in OUTCOMES:
        raise bad_request(f"status must be one of {', '.join(OUTCOMES)}")
    with tx() as conn:
        uid = parse_id("billingRun", run_id)
        if billing.get_daily_run_by_id(conn, uid) is None:
            raise not_found("billingRun", run_id)
        rows, total = billing.page_run_outcomes(
            conn, uid, status=status, offset=offset, limit=limit
        )
    items = [
        {
            "billingAccount": {"id": str(r["billing_account_id"]), "name": r["account_name"]},
            "status": r["status"],
            "error": r["error"],
            "waitsFor": None
            if r["waits_for_run_id"] is None
            else {"id": str(r["waits_for_run_id"]), "runDate": r["waits_for_date"]},
            "attempts": r["attempts"],
            "finishedAt": utc(r["finished_at"]),
        }
        for r in rows
    ]
    return _page(items, total)


class CatchUp(TmfModel):
    up_to: date

    @field_validator("up_to", mode="before")
    @classmethod
    def _business_date(cls, value: Any) -> Any:
        if not isinstance(value, str) or not ISO_DATE.match(value):
            raise ValueError("must be a business date YYYY-MM-DD")
        return value


@router.post("/catchUp")
def catch_up(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    data = parse(CatchUp, body)
    tz = ZoneInfo(settings.billing_tz)
    today, now = clock.today(), clock.now()
    if data.up_to > today:
        raise unprocessable(f"upTo must be {today} or earlier (the business date)")
    due = time.fromisoformat(settings.billing_run_at)
    if data.up_to == today and now.astimezone(tz).time() < due:
        raise unprocessable(
            f"the run of {today} is due at {settings.billing_run_at} ({tz.key}), not before"
        )
    with tx() as conn:
        held = billing.held_back_by(conn)
        dates = catch_up_dates(
            billing.latest_run_date(conn), held["run_date"] if held else None, data.up_to
        )
        if not dates:
            return TmfJSONResponse({"dates": []})
        queued = worker_requests.insert_request(
            conn,
            kind="catchUp",
            up_to=data.up_to,
            requested_at=now,
            requested_by=operator_of(request),
        )
    return TmfJSONResponse({"request": request_body(queued, None), "dates": dates}, status_code=202)


@router.post("/{run_id}/retry")
def retry(run_id: str, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        run = billing.get_daily_run_by_id(conn, parse_id("billingRun", run_id))
        if run is None:
            raise not_found("billingRun", run_id)
        if run["status"] != "failed":
            raise conflict(
                f"only a failed run can be retried; the run of {run['run_date']} is {run['status']}"
            )
        if worker_requests.pending_retry(conn, run["id"]) is not None:
            raise conflict(f"a retry of the run of {run['run_date']} is already queued")
        queued = worker_requests.insert_request(
            conn,
            kind="retry",
            run_id=run["id"],
            requested_at=clock.now(),
            requested_by=operator_of(request),
        )
    return TmfJSONResponse({"request": request_body(queued, run)}, status_code=202)
