import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app import api, db, internal, logsetup, metrics, overload, status
from app.auth import authenticate
from app.care import api as care_api
from app.care import routes as care_routes
from app.care import signup as care_signup
from app.clock import SystemClock
from app.config import settings

logsetup.configure("app")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """The runtime log level: a thread re-reads it every LOG_POLL_SECONDS (issue #274)."""
    poller = logsetup.start_db_poller(SystemClock().now)
    try:
        yield
    finally:
        if poller is not None:
            poller.stop()


# operationId = the handler's function name (e.g. create_payment), not FastAPI's
# name+path+method: the MCP connector names its tools after it, and clients cap tool
# names at 64 characters (tests/test_operation_ids.py).
app = FastAPI(
    title="bss",
    version="0.1.0",
    generate_unique_id_function=lambda route: route.name,
    lifespan=lifespan,
    dependencies=[Depends(authenticate)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
api.install(app)
internal.install(app)
app.include_router(care_routes.router)
app.include_router(care_api.router)
app.include_router(care_signup.router)
app.include_router(status.router)
app.include_router(metrics.router)
app.add_middleware(overload.InFlightLimitMiddleware)  # inside: rejections logged, counted
app.add_middleware(metrics.HttpMetricsMiddleware)
app.add_middleware(logsetup.RequestLogMiddleware)  # added last = outermost
metrics.set_build_info()


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(status_code=200, content={"service": "bss", "status": "ok"})


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness + dependency check.

    Docker's HEALTHCHECK and scripts/deploy.sh both gate on this endpoint, so keep it
    cheap and keep it honest: a 200 here must mean the service can actually serve.
    The rollback drill's BSS_HEALTH_DRILL=unhealthy (app/config.py) answers 503.
    """
    if settings.bss_health_drill == "unhealthy":
        return JSONResponse(status_code=503, content={"status": "degraded", "drill": "unhealthy"})
    try:
        db.ping()
    except Exception as exc:  # noqa: BLE001 - the reason belongs in the response body
        log.warning("healthz: database unreachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "db": "up"})
