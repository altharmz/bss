"""TMF Open API surface (v5; TMF676 Payment is v4). See docs/decisions/tmf-api.md.

Each API is one module with its router, request models and row -> body mapping. Handlers
open exactly one database transaction per request (app.api.deps.Tx).
"""

from fastapi import FastAPI

from app.api import (
    account,
    accounts,
    bill,
    catalog,
    classification,
    customer,
    errors,
    inventory,
    on_demand,
    ordering,
    party,
    payment,
    presentation,
    refunds,
)


def install(app: FastAPI) -> None:
    errors.install(app)
    for module in (
        catalog,
        classification,
        party,
        customer,
        account,
        accounts,
        payment,
        refunds,
        presentation,
        inventory,
        bill,
        on_demand,
        ordering,
    ):
        app.include_router(module.router)
    app.include_router(account.schema_router)
