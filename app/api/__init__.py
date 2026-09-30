"""TMF Open API surface (v5; TMF676 Payment is v4). See CLAUDE.md, "TMF API conventions".

Each API is one module with its router, request models and row -> body mapping. Handlers
open exactly one database transaction per request (app.api.deps.Tx).
"""

from fastapi import FastAPI

from app.api import catalog, errors


def install(app: FastAPI) -> None:
    errors.install(app)
    app.include_router(catalog.router)
