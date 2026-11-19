"""A bound on the requests one app process holds (issue #341; docs/decisions/infra.md,
"Request bound").

Requests that arrive faster than the process answers them used to queue for the threadpool
and the DB pool until the container ran out of memory. `InFlightLimitMiddleware` counts the
requests in flight and answers one beyond `BSS_MAX_IN_FLIGHT` at once with 503 and
`Retry-After`, without touching the database. It counts requests, not connections: Caddy
keeps idle keep-alive connections open, so uvicorn's `--limit-concurrency` would refuse
requests while the app is idle.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from app import metrics
from app.api.errors import error_response
from app.config import settings

# A probe failing under load would restart the container; a scrape is how we see the load.
EXEMPT = {"/healthz", metrics.METRICS_PATH}
RETRY_AFTER = "1"
MESSAGE = "the server is at capacity; retry shortly"


class InFlightState:
    """The process's count of admitted requests. One event loop per process, and the
    count only changes between awaits, so a plain integer is exact."""

    def __init__(self) -> None:
        self.count = 0


STATE = InFlightState()


class InFlightLimitMiddleware:
    """Pure ASGI. Sits inside the request log and the HTTP metrics, so a rejected request is
    still logged and counted (as 503, route "unmatched")."""

    def __init__(self, app: ASGIApp, state: InFlightState = STATE) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in EXEMPT:
            await self.app(scope, receive, send)
            return
        if self.state.count >= settings.bss_max_in_flight:
            metrics.REJECTED_OVERLOAD.inc()
            response = error_response(
                503, MESSAGE, headers={"Retry-After": RETRY_AFTER}, code="overloaded"
            )
            await response(scope, receive, send)
            return
        self.state.count += 1
        metrics.IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, send)
        finally:  # success, exception and a client gone away (cancellation) alike
            self.state.count -= 1
            metrics.IN_FLIGHT.dec()
