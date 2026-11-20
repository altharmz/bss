from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment - no config files."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Unset means prod: the safe reading (make seed refuses it, the worker's clock override
    # needs an explicit "test"). compose sets it everywhere: dev locally, staging on the VPS.
    app_env: str = "prod"
    log_level: str = "info"
    # Seconds between reads of the runtime log level (app/logsetup.py, LevelPoller); 0 = never
    # (the tests set it). Only OUR_LOGGERS follow it.
    log_poll_seconds: int = Field(default=15, ge=0)
    # Rollback drill only (docs/operations.md "Rollback"): "unhealthy" makes /healthz answer
    # 503, so the release scripts/deploy.sh starts under BSS_DEPLOY_DRILL=unhealthy really
    # fails the health gate. Empty everywhere else.
    bss_health_drill: Literal["", "unhealthy"] = ""
    # GET /metrics (app/metrics.py): empty = off (404), else the bearer token the scraper sends.
    # The same value for the app and mcp. BSS_BUILD is the image tag, shown as bss_build_info.
    bss_metrics_token: str = ""
    build: str = Field(default="dev", validation_alias=AliasChoices("BSS_BUILD", "build"))
    database_url: str = "postgresql://app:app@db:5432/app"
    # Seconds per connection attempt, and the longest a request waits for a pooled
    # connection. 3 s turned a short database stall into 500s, so 10 s (docs/decisions/infra.md).
    db_connect_timeout: int = 10
    # The API's connection pool (app/db.py): at most this many connections to postgres,
    # whatever the number of requests in flight (37 usable on the VPS's postgres).
    db_pool_size: int = Field(default=10, ge=1)
    # Requests one app process serves at once (app/overload.py, #341); beyond it a 503 at
    # once. Unset = DB_POOL_SIZE: more only wait for a pooled connection and hold memory
    # (measured: docs/decisions/infra.md, "Request bound").
    bss_max_in_flight: int | None = Field(default=None, ge=1)
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
    # Load tests only (#281, docs/loadtest.md): the stub network produces usage at this daily
    # average in records/s, shaped over the day. 0 = off; anything else needs APP_ENV=test.
    stub_usage_rate: float = Field(
        default=0, ge=0, validation_alias=AliasChoices("BSS_STUB_USAGE_RATE", "stub_usage_rate")
    )
    # The local hour (0-23) whose share of the daily shape the generator keeps for the whole
    # run: 20 is the peak, 4 the night. -1 = follow the clock.
    stub_usage_hour: int = Field(
        default=-1,
        ge=-1,
        le=23,
        validation_alias=AliasChoices("BSS_STUB_USAGE_HOUR", "stub_usage_hour"),
    )
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
    # Customer notifications (docs/decisions/notifications.md). Only the `log` sender exists;
    # an unknown value fails startup. Send attempts before a message is `failed`; how far back
    # the detectors look for events (so a deploy does not replay history); the low-balance and
    # plan-expiry thresholds; SMS quiet hours in BILLING_TZ (HH:MM); the sender addresses.
    notify_adapter: Literal["log"] = Field(
        default="log", validation_alias=AliasChoices("BSS_NOTIFY_ADAPTER", "notify_adapter")
    )
    notify_max_attempts: int = Field(default=5, ge=1)
    notify_lookback_days: int = Field(default=2, ge=1, le=30)
    notify_low_balance: Decimal = Field(default=Decimal("5.00"), ge=0)
    notify_expiring_days: int = Field(default=3, ge=1, le=60)
    notify_quiet_start: str = Field(default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notify_quiet_end: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notify_sender_email: str = "no-reply@bss.example.test"
    notify_sender_sms: str = "18005550100"
    # Bill PDFs and delivery (docs/decisions/bills.md, "PDF documents and email delivery"): the
    # issuer printed in every PDF's header (address lines separated by "\n" or ";"), and
    # whether bills of accounts with the `email` presentation medium are emailed (the human's
    # switch in production).
    bill_issuer_name: str = Field(
        default="BSS Mobile",
        min_length=1,
        validation_alias=AliasChoices("BSS_BILL_ISSUER_NAME", "bill_issuer_name"),
    )
    bill_issuer_address: str = Field(
        default="",
        validation_alias=AliasChoices("BSS_BILL_ISSUER_ADDRESS", "bill_issuer_address"),
    )
    bill_email_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("BSS_BILL_EMAIL_ENABLED", "bill_email_enabled"),
    )
    # Account security (docs/decisions/accounts.md): whether sensitive actions on a protected
    # customer (a live prepaid product) demand the account PIN (the rollout switch; locks and
    # the audit log always apply), consecutive wrong PINs before verification locks, and for how
    # long.
    account_pin_required: bool = Field(
        default=True,
        validation_alias=AliasChoices("BSS_ACCOUNT_PIN_REQUIRED", "account_pin_required"),
    )
    pin_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias=AliasChoices("BSS_PIN_MAX_ATTEMPTS", "pin_max_attempts"),
    )
    pin_lockout_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        validation_alias=AliasChoices("BSS_PIN_LOCKOUT_MINUTES", "pin_lockout_minutes"),
    )
    # Customer self-care (docs/decisions/self-care.md): off, every /self-care/v1 route answers
    # 404 and no login can be invited. Exposing it in production is the human's decision. A
    # customer session ends after the idle time and at the absolute lifetime (seconds).
    self_care_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("BSS_SELF_CARE_ENABLED", "self_care_enabled"),
    )
    care_session_idle_seconds: int = Field(
        default=30 * 60,
        ge=60,
        validation_alias=AliasChoices("BSS_CARE_SESSION_IDLE_SECONDS", "care_session_idle_seconds"),
    )
    care_session_absolute_seconds: int = Field(
        default=12 * 3600,
        ge=60,
        validation_alias=AliasChoices(
            "BSS_CARE_SESSION_ABSOLUTE_SECONDS", "care_session_absolute_seconds"
        ),
    )
    # Separate credentials: smoke can only read; MCP cannot access user/session APIs.
    bss_service_secret: str = ""
    bss_smoke_secret: str = ""

    @field_validator("bss_max_in_flight", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # compose passes ${VAR:-} through as an empty string
        return None if value == "" else value

    @model_validator(mode="after")
    def _derived_defaults(self) -> "Settings":
        if self.bss_max_in_flight is None:
            self.bss_max_in_flight = self.db_pool_size
        return self


settings = Settings()
