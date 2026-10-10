"""GET /internal/v1/context: what the UI's top bar and sidebar show about the system."""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.api.common import OPERATOR, operator_of, utc
from app.api.deps import ClockDep
from app.api.errors import bad_request
from app.api.tmfjson import TmfJSONResponse
from app.config import settings

BASE = "/internal/v1"
router = APIRouter(prefix=BASE)

__all__ = ["BASE", "OPERATOR", "no_query", "operator_of", "router"]


def no_query(request: Request) -> None:
    for name in request.query_params:
        raise bad_request(f"unknown query parameter {name}")


@router.get("/context")
def get_context(request: Request, clock: ClockDep) -> TmfJSONResponse:
    no_query(request)
    tz = ZoneInfo(settings.billing_tz)
    now = clock.now()
    offset = now.astimezone(tz).strftime("%z")  # +0300
    return TmfJSONResponse(
        {
            "businessDate": clock.today(),
            "now": utc(now),
            "timeZone": tz.key,
            "utcOffset": f"{offset[:3]}:{offset[3:]}",
            "environment": settings.app_env,
            "operator": operator_of(request),
            "currency": settings.ui_currency,
            "billingRunAt": settings.billing_run_at,
            "paymentTermDays": settings.bill_payment_term_days,
        }
    )
