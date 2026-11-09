"""The runtime log level, under /internal/v1/logging (admin only), and the read-only path the
MCP connector polls (service credential alone). Ours, not TMF (docs/decisions/infra.md,
"Logging"; docs/decisions/internal-api.md).

DEBUG exists only as `debug_until`, at most 120 minutes ahead, so it always expires by itself.
"""

import logging
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.common import TmfModel, parse, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.errors import TmfError, bad_request
from app.api.tmfjson import TmfJSONResponse
from app.auth import SERVICE_POLL_OPERATOR, require_admin
from app.config import settings
from app.internal.context import no_query
from app.logsetup import effective_level
from app.repository import log_setting as repo
from app.repository._sql import Row

router = APIRouter(prefix="/internal/v1")
log = logging.getLogger("app.logging")

MAX_DEBUG_MINUTES = 120


class LogChange(TmfModel):
    base_level: Literal["WARNING", "INFO"] | None = None
    debug_minutes: Annotated[int, Field(strict=True)] | None = None


def level_body(row: Row, now) -> dict[str, Any]:
    until = row["debug_until"]
    return {
        "baseLevel": row["base_level"],
        "envLevel": settings.log_level.upper(),
        "effectiveLevel": effective_level(
            row["base_level"], until, settings.log_level.upper(), now
        ),
        "debugUntil": None if until is None else utc(until),
        "updatedAt": None if row["updated_at"] is None else utc(row["updated_at"]),
        "updatedBy": row["updated_by"],
    }


@router.get("/logging", dependencies=[Depends(require_admin)])
def get_logging(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(level_body(repo.get_row(conn), clock.now()))


@router.put("/logging", dependencies=[Depends(require_admin)])
def put_logging(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """Set the base level (`WARNING`, `INFO` or null = follow LOG_LEVEL) and/or start DEBUG
    for `debugMinutes` (0 ends it now). At least one field."""
    no_query(request)
    try:
        data = parse(LogChange, body)
    except TmfError as exc:
        raise bad_request(exc.message) from exc  # a bad value is a 400 here, not a 422
    sent = data.model_fields_set
    if "base_level" not in sent and data.debug_minutes is None:
        raise bad_request("send baseLevel and/or debugMinutes")
    minutes = data.debug_minutes
    if minutes is not None and not 0 <= minutes <= MAX_DEBUG_MINUTES:
        raise bad_request(f"debugMinutes is between 0 and {MAX_DEBUG_MINUTES}")
    now = clock.now()
    operator = request.state.operator
    with tx() as conn:
        old = repo.get_row(conn, lock=True)
        base = data.base_level if "base_level" in sent else old["base_level"]
        until = old["debug_until"]
        if minutes is not None:
            until = None if minutes == 0 else now + timedelta(minutes=minutes)
        new = repo.update(conn, base_level=base, debug_until=until, operator=operator, now=now)
        repo.append_change(conn, operator=operator, old=old, new=new, now=now)
        log.warning(
            "log level changed by %s: base %s -> %s, debug until %s -> %s",
            operator,
            old["base_level"],
            new["base_level"],
            old["debug_until"],
            new["debug_until"],
        )
        return TmfJSONResponse(level_body(new, now))


def require_service_poll(request: Request) -> None:
    """The connector's poll: the MCP service credential alone (app/auth.py sets the
    operator); a session, the smoke credential or a principal never reach it."""
    if getattr(request.state, "operator", None) != SERVICE_POLL_OPERATOR:
        raise TmfError(403, "only the MCP service credential may read the log level")


@router.get("/service/log-level", dependencies=[Depends(require_service_poll)])
def service_log_level(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        body = level_body(repo.get_row(conn), clock.now())
    return TmfJSONResponse(
        {
            "baseLevel": body["baseLevel"],
            "effectiveLevel": body["effectiveLevel"],
            "debugUntil": body["debugUntil"],
        }
    )
