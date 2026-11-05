"""TMF Open API surface (v5; TMF676 Payment and TMF654 Prepay Balance are v4).
See docs/decisions/tmf-api.md.

Each API is one module with its router, request models and row -> body mapping. Handlers
open exactly one database transaction per request (app.api.deps.Tx).
"""

from fastapi import FastAPI

from app.api import (
    account,
    accounts,
    adjust,
    balance,
    bill,
    catalog,
    catalog_jobs,
    classification,
    customer,
    errors,
    history,
    hub,
    inventory,
    on_demand,
    ordering,
    party,
    payment,
    payment_method,
    presentation,
    refunds,
    reserve,
    resource_inventory,
    topup,
    transfer,
)


def install(app: FastAPI) -> None:
    errors.install(app)
    for module in (
        catalog,
        classification,
        catalog_jobs,
        party,
        customer,
        account,
        accounts,
        payment,
        payment_method,
        refunds,
        presentation,
        inventory,
        bill,
        on_demand,
        ordering,
        resource_inventory,
        balance,
        topup,
        adjust,
        reserve,
        transfer,
        history,
    ):
        app.include_router(module.router)
    app.include_router(account.schema_router)
    for router in hub.ROUTERS:
        app.include_router(router)
