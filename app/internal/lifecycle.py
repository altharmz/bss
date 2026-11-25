"""Prepaid lifecycle settings for operators, under /internal/v1/lifecycle/settings.
Ours, not TMF: TMF has no lifecycle policy (docs/decisions/prepaid.md).

The durations of grace, suspension and number aging and the pay-as-you-go zero-balance
trigger. The worker derives every transition from them on each tick, so a change applies to
every transition not yet taken; transitions already taken are not undone.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api.common import TmfModel, parse, utc
from app.api.deps import ClockDep, JsonBody, Tx
from app.api.roles import require_admin_or_service
from app.api.tmfjson import TmfJSONResponse
from app.internal.context import no_query
from app.repository import lifecycle as repo
from app.repository._sql import Row

# the Settings page only, admin-only like it (#467)
router = APIRouter(
    prefix="/internal/v1/lifecycle", dependencies=[Depends(require_admin_or_service)]
)

Days = Annotated[int, Field(ge=0, le=3650, strict=True)]


class SettingsChange(TmfModel):
    grace_days: Annotated[int, Field(ge=0, le=365, strict=True)] | None = None
    suspension_days: Days | None = None
    number_aging_days: Days | None = None
    payg_zero_balance_days: Annotated[int, Field(ge=0, le=365, strict=True)] | None = None


def settings_body(row: Row) -> dict[str, Any]:
    return {
        "graceDays": row["grace_days"],
        "suspensionDays": row["suspension_days"],
        "numberAgingDays": row["number_aging_days"],
        "paygZeroBalanceDays": row["payg_zero_balance_days"],
        "updatedAt": None if row["updated_at"] is None else utc(row["updated_at"]),
    }


@router.get("/settings")
def get_settings(request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(settings_body(repo.get_row(conn)))


@router.put("/settings")
def put_settings(body: JsonBody, request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    """Change any of the durations (each optional; at least one)."""
    no_query(request)
    data = parse(SettingsChange, body)
    changes = {
        key: getattr(data, name)
        for key, name in (
            ("graceDays", "grace_days"),
            ("suspensionDays", "suspension_days"),
            ("numberAgingDays", "number_aging_days"),
            ("paygZeroBalanceDays", "payg_zero_balance_days"),
        )
        if name in data.model_fields_set and getattr(data, name) is not None
    }
    with tx() as conn:
        if not changes:
            from app.api.errors import bad_request

            raise bad_request("send at least one duration to change")
        repo.get_row(conn, lock=True)
        return TmfJSONResponse(settings_body(repo.update(conn, changes, clock.now())))
