"""The signed-in customer: the one place a self-care request learns whose data it may reach.

`CareActor` is the customer session that app/auth.py resolved (the cookie `bss_care_session`).
Every self-care repository call takes its `customer_id`; an object of another customer answers
404, never 403, so that ids are not confirmed (docs/decisions/self-care.md).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from app.api.errors import TmfError


@dataclass(frozen=True)
class CareSession:
    login_id: UUID
    customer_id: UUID
    email: str
    token_hash: str
    expires_at: datetime


def care_actor(request: Request) -> CareSession:
    session = getattr(request.state, "care", None)
    if session is None:  # pragma: no cover - the self-care middleware answers 401 first
        raise TmfError(401, "sign in required")
    return session


CareActor = Annotated[CareSession, Depends(care_actor)]
