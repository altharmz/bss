"""Postpaid dunning for operators (docs/decisions/dunning.md, issue #385). Ours, not TMF:
TMF678 bills carry `paymentDueDate` and a state only, and TMF has no dunning API.

- `GET/PATCH /internal/v1/dunning/settings`: the step days after O (the first overdue date)
  and the smallest overdue amount that counts. The worker derives the steps on each pass, so a
  change applies to the steps not yet taken.
- `GET /internal/v1/dunning/accounts`: the accounts in dunning, oldest O first, with the amount
  past due, the last step and, once suspended, whether the account is a deactivation candidate
  (suspended more than 60 days: postpaid lines are never ended automatically). Filters: `step`,
  `deactivationCandidate=true` and `billingAccount.id` (#466, the bill list's name).
- `GET/PATCH /internal/v1/billingAccount/{id}/dunning`: the account's dunning state (O, the
  steps taken, the next step and its date) and its exemption, set with an audit row.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from pydantic import Field, field_validator

from app.api.common import CENT, TmfModel, money, parse, parse_id, utc
from app.api.deps import ClockDep, MergePatchBody, Tx
from app.api.errors import bad_request, not_found, unprocessable
from app.api.guards import actor_of
from app.api.listing import page_response, parse_page
from app.api.tmfjson import TmfJSONResponse
from app.billing import dunning as plan
from app.internal.context import no_query
from app.repository import dunning as repo
from app.repository import parties
from app.repository import security as audit
from app.repository._sql import Row

router = APIRouter(prefix="/internal/v1")

# a postpaid account suspended longer than this is listed as a deactivation candidate
DEACTIVATION_CANDIDATE_DAYS = 60
LAST_STEPS = ("none", *plan.STEPS)
MINIMUM_LIMIT = Decimal("10000000000")

Days = Annotated[int, Field(ge=0, le=365, strict=True)]


class SettingsChange(TmfModel):
    reminder1_days: Days | None = None
    reminder2_days: Days | None = None
    suspend_days: Days | None = None
    # a plain Decimal in the schema (the MCP tools send it as a decimal string); unset by default
    minimum_overdue: Decimal = Field(default=None)

    @field_validator("minimum_overdue", mode="before")
    @classmethod
    def _exact_cents(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ValueError("must be a JSON number")
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount >= MINIMUM_LIMIT:
            raise ValueError("must be zero or more, below 10000000000")
        if amount.quantize(CENT) != amount:
            raise ValueError("must have at most 2 decimal places (amounts are never rounded)")
        return amount.quantize(CENT)


class Exemption(TmfModel):
    exempt: bool
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def settings_body(row: Row) -> dict[str, Any]:
    return {
        "reminder1Days": row["reminder1_days"],
        "reminder2Days": row["reminder2_days"],
        "suspendDays": row["suspend_days"],
        "minimumOverdue": row["minimum_overdue"],
        "updatedAt": None if row["updated_at"] is None else utc(row["updated_at"]),
    }


@router.get("/dunning/settings")
def get_dunning_settings(request: Request, tx: Tx) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(settings_body(repo.get_row(conn)))


@router.patch("/dunning/settings")
def patch_dunning_settings(
    body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Change any of the steps' days or the minimum (at least one); the days stay increasing."""
    no_query(request)
    data = parse(SettingsChange, body)
    changes = {}
    for key, name in (
        ("reminder1Days", "reminder1_days"),
        ("reminder2Days", "reminder2_days"),
        ("suspendDays", "suspend_days"),
        ("minimumOverdue", "minimum_overdue"),
    ):
        if name in data.model_fields_set:
            if getattr(data, name) is None:
                raise unprocessable(f"{key} cannot be removed")
            changes[key] = getattr(data, name)
    if not changes:
        raise bad_request("send at least one setting to change")
    with tx() as conn:
        row = repo.get_row(conn, lock=True)
        days = [
            changes.get(key, row[name])
            for key, name in (
                ("reminder1Days", "reminder1_days"),
                ("reminder2Days", "reminder2_days"),
                ("suspendDays", "suspend_days"),
            )
        ]
        if not days[0] < days[1] < days[2]:
            raise unprocessable(
                "the steps must be increasing: reminder1Days < reminder2Days < suspendDays"
                f" (they would be {days[0]}, {days[1]}, {days[2]})"
            )
        return TmfJSONResponse(settings_body(repo.update(conn, changes, clock.now())))


def _one(request: Request, name: str, allowed: tuple[str, ...]) -> str | None:
    given = request.query_params.getlist(name)
    if len(given) > 1:
        raise bad_request(f"{name} given more than once")
    if given and given[0] not in allowed:
        raise bad_request(f"{name} must be one of {', '.join(allowed)}")
    return given[0] if given else None


def _account_filter(request: Request) -> UUID | None:
    given = request.query_params.getlist("billingAccount.id")
    if len(given) > 1:
        raise bad_request("billingAccount.id given more than once")
    try:
        return UUID(given[0]) if given else None
    except ValueError:
        raise bad_request("billingAccount.id must be a UUID") from None


def _candidate(suspended_since: date | None, today: date) -> bool:
    return (
        suspended_since is not None and (today - suspended_since).days > DEACTIVATION_CANDIDATE_DAYS
    )


@router.get("/dunning/accounts")
def list_dunning_accounts(request: Request, tx: Tx, clock: ClockDep) -> TmfJSONResponse:
    offset, limit = parse_page(
        request, extra=("step", "deactivationCandidate", "billingAccount.id")
    )
    step = _one(request, "step", LAST_STEPS)
    account = _account_filter(request)
    candidates = _one(request, "deactivationCandidate", ("true",)) is not None
    today = clock.today()
    with tx() as conn:
        minimum = repo.get_row(conn)["minimum_overdue"]
        by = today - timedelta(days=DEACTIVATION_CANDIDATE_DAYS + 1) if candidates else None
        rows, more = repo.page(
            conn,
            today,
            minimum,
            step=step,
            suspended_by=by,
            account_id=account,
            offset=offset,
            limit=limit,
        )
    items = [
        {
            "billingAccount": {"id": str(row["billing_account_id"]), "name": row["name"]},
            "overdueSince": row["overdue_since"],
            "overdueAmount": money(row["amount"], row["currency"]),
            "lastStep": row["last_step"],
            "suspendedSince": row["suspended_since"],
            "deactivationCandidate": _candidate(row["suspended_since"], today),
        }
        for row in rows
    ]
    return page_response(request, items, more, offset, limit)


def _account(conn: psycopg.Connection, account_id: str, *, lock: bool = False) -> Row:
    row = parties.get_billing_account(
        conn, parse_id("billingAccount", account_id), "update" if lock else None
    )
    if row is None:
        raise not_found("billingAccount", account_id)
    return row


def dunning_body(conn: psycopg.Connection, account: Row, today: date) -> dict[str, Any]:
    """The account's dunning state on `today`: O and the amount past due while in dunning, the
    steps taken for that O, the next step, and the products under a dunning suspension."""
    settings = repo.settings(conn)
    overdue = repo.overdue_of(conn, account["id"], today)
    o = None if overdue is None else plan.overdue_since([overdue["first_due"]])
    active = overdue is not None and plan.in_dunning(o, overdue["amount"], settings)
    steps = repo.steps_of(conn, account["id"], o) if active and o is not None else []
    taken = [row["step"] for row in steps]
    upcoming = plan.next_step(o, settings, today, taken) if active and o is not None else None
    suspended = repo.suspended_ids(conn, account["id"])
    return {
        "billingAccount": {"id": str(account["id"]), "name": account["name"]},
        "exempt": account["dunning_exempt"],
        "inDunning": active,
        "overdueSince": o if active else None,
        "overdueAmount": money(overdue["amount"], overdue["currency"]) if active else None,
        "stepsTaken": [{"step": row["step"], "date": row["business_date"]} for row in steps],
        "nextStep": None if upcoming is None else {"step": upcoming[0], "date": upcoming[1]},
        "suspendedProducts": [{"id": str(uid)} for uid in suspended],
    }


@router.get("/billingAccount/{account_id}/dunning")
def get_account_dunning(
    account_id: str, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    no_query(request)
    with tx() as conn:
        return TmfJSONResponse(dunning_body(conn, _account(conn, account_id), clock.today()))


@router.patch("/billingAccount/{account_id}/dunning")
def patch_account_dunning(
    account_id: str, body: MergePatchBody, request: Request, tx: Tx, clock: ClockDep
) -> TmfJSONResponse:
    """Exempt a postpaid account from dunning or end its exemption, with an audit row. An
    exempt account takes no step and its dunning suspensions are lifted by the next pass."""
    no_query(request)
    data = parse(Exemption, body)
    actor = actor_of(request)
    now = clock.now()
    with tx() as conn:
        account = _account(conn, account_id, lock=True)
        if account["rating_type"] != "postpaid":
            raise unprocessable("dunning applies to postpaid accounts only")
        if account["dunning_exempt"] != data.exempt:
            conn.execute(
                "UPDATE billing_account SET dunning_exempt = %s WHERE id = %s",
                (data.exempt, account["id"]),
            )
            audit.record(
                conn,
                now=now,
                operator=actor.operator,
                role=actor.role,
                action="dunning_exemption",
                outcome="done",
                verification="none",
                reason=data.reason,
                customer_id=account["customer_id"],
                billing_account_id=account["id"],
                detail={"exempt": data.exempt},
            )
            account = _account(conn, account_id)
        return TmfJSONResponse(dunning_body(conn, account, clock.today()))
