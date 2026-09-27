import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db
from app.config import settings

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("app")

app = FastAPI(title="__PROJECT__", version="0.1.0")


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(status_code=200, content={"service": "__PROJECT__", "status": "ok"})


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness + dependency check.

    Docker's HEALTHCHECK and scripts/deploy.sh both gate on this endpoint, so keep it
    cheap and keep it honest: a 200 here must mean the service can actually serve.
    """
    try:
        db.ping()
    except Exception as exc:  # noqa: BLE001 - the reason belongs in the response body
        log.warning("healthz: database unreachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "db": "up"})
