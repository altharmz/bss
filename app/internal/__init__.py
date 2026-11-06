"""Internal API for the admin UI (stage 5), under /internal/v1. Ours, not TMF.

What the TMF APIs cannot answer without the UI re-implementing billing rules: the
business date, the first-charge preview, an account's open products with their next
charge, and its charge and transaction history. Same conventions as app/api (TMF Error
bodies, Decimal-exact Money, one transaction per request, the injected clock), but dates
are plain business dates (YYYY-MM-DD in BILLING_TZ). The contract is
spec/internal/bss-internal.oas.yaml; tests validate every response against it.
"""

from fastapi import FastAPI

from app.internal import (
    accounts,
    billing_runs,
    bills,
    catalog,
    context,
    event_subscriptions,
    lifecycle,
    network_commands,
    payments,
    ports,
    prepaid_commands,
    sims,
    usage,
    users,
)


def install(app: FastAPI) -> None:
    for module in (
        context,
        accounts,
        catalog,
        billing_runs,
        prepaid_commands,
        lifecycle,
        network_commands,
        sims,
        ports,
        usage,
        payments,
        bills,
        users,
        event_subscriptions,
    ):
        app.include_router(module.router)
    app.include_router(payments.accounts)
