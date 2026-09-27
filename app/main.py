import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api import get_optional_player
from app.api import router as api_router
from app.config import settings

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("app")

app = FastAPI(title="vps-lab", version="0.1.0")

app.include_router(api_router)

STATIC_DIR = Path(__file__).parent / "static"
PAGES_DIR = Path(__file__).parent / "pages"

# Only assets (css, etc.) live under /static and are safe to serve directly. The HTML
# pages live outside this mount, in PAGES_DIR, so they can only be reached through the
# routes below - which enforce the login/redirect rules - never by requesting the file
# path straight from the static mount.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_model=None)
def game_page(
    player: dict | None = Depends(get_optional_player),
) -> FileResponse | RedirectResponse:
    if player is None:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(PAGES_DIR / "game.html")


@app.get("/login", response_model=None)
def login_page(
    player: dict | None = Depends(get_optional_player),
) -> FileResponse | RedirectResponse:
    if player is not None:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(PAGES_DIR / "login.html")


@app.get("/leaderboard")
def leaderboard_page() -> FileResponse:
    return FileResponse(PAGES_DIR / "leaderboard.html")


@app.get("/account", response_model=None)
def account_page(
    player: dict | None = Depends(get_optional_player),
) -> FileResponse | RedirectResponse:
    if player is None:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(PAGES_DIR / "account.html")


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
