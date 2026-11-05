from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment - no config files."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Unset means prod: the safe reading (make seed refuses it, the worker's clock override
    # needs an explicit "test"). compose sets it everywhere: dev locally, staging on the VPS.
    app_env: str = "prod"
    log_level: str = "info"
    database_url: str = "postgresql://app:app@db:5432/app"
    # Seconds per connection attempt, and the longest a request waits for a pooled
    # connection. 3 s turned a short database stall into 500s, so 10 s (docs/decisions/infra.md).
    db_connect_timeout: int = 10
    # The API's connection pool (app/db.py): at most this many connections to postgres,
    # whatever the number of requests in flight (37 usable on the VPS's postgres).
    db_pool_size: int = Field(default=10, ge=1)
    # "Today" for billing is the calendar date in this zone (see app/clock.py).
    billing_tz: str = "Europe/Moscow"
    msisdn_quarantine_days: int = Field(default=0, ge=0)
    # Local time (BILLING_TZ, HH:MM) of the daily billing run. It must come after 00:00
    # (the run of D sees orders completed before 00:00 of D), before the 02:15 backup,
    # and well before the 03:00 reboot. Stage 4's worker reads it; the UI only shows it.
    billing_run_at: str = Field(default="00:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Days from a bill's date to its paymentDueDate. Read when a bill is written: a change
    # never moves the due date of an existing bill (docs/archive/bill-lifecycle-design.md, 6.6).
    bill_payment_term_days: int = Field(default=30, ge=1, le=365)
    # The one currency the admin UI works in (the backend stays multi-currency).
    ui_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    session_idle_seconds: int = Field(default=8 * 3600, ge=1)
    session_absolute_seconds: int = Field(default=7 * 86400, ge=1)
    # Behind Caddy (prod): the sign-in throttle keys on the last X-Forwarded-For entry,
    # which Caddy sets and a client cannot choose. Off: the peer address only.
    app_trust_forwarded: bool = False
    # Network provisioning (docs/decisions/network.md). Only the stub exists; an unknown
    # value fails startup. Temporary errors: attempts before an operator must retry, and the
    # waits between them (minutes: 1, 5, 15, 60 ...).
    network_adapter: Literal["stub"] = Field(
        default="stub", validation_alias=AliasChoices("BSS_NETWORK_ADAPTER", "network_adapter")
    )
    network_max_attempts: int = Field(default=5, ge=1)
    # Number Transfer PIN validity in days (docs/decisions/numbers.md); optional.
    number_transfer_pin_days: int = Field(
        default=7,
        ge=1,
        validation_alias=AliasChoices("BSS_NUMBER_TRANSFER_PIN_DAYS", "number_transfer_pin_days"),
    )
    # Tax engine (docs/decisions/taxes.md). Only the stub exists; an unknown value fails
    # startup. A prepaid line needs a service address (the tax is on its place of primary
    # use); off, the order is accepted without one and the flat price tax applies.
    tax_adapter: Literal["stub"] = Field(
        default="stub", validation_alias=AliasChoices("BSS_TAX_ADAPTER", "tax_adapter")
    )
    tax_require_service_address: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "BSS_TAX_REQUIRE_SERVICE_ADDRESS", "tax_require_service_address"
        ),
    )
    # Payment gateway (docs/decisions/payments.md). Only the stub exists; an unknown value
    # fails startup. Temporary errors: attempts before an operator must retry.
    payment_adapter: Literal["stub"] = Field(
        default="stub", validation_alias=AliasChoices("BSS_PAYMENT_ADAPTER", "payment_adapter")
    )
    payment_max_attempts: int = Field(default=5, ge=1)
    # Separate credentials: smoke can only read; MCP cannot access user/session APIs.
    bss_service_secret: str = ""
    bss_smoke_secret: str = ""


settings = Settings()
