from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment - no config files."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: str = "dev"
    log_level: str = "info"
    database_url: str = "postgresql://app:app@db:5432/app"
    db_connect_timeout: int = 3
    # "Today" for billing is the calendar date in this zone (see app/clock.py).
    billing_tz: str = "Europe/Moscow"
    # Local time (BILLING_TZ, HH:MM) of the daily billing run. It must come after 00:00
    # (the run of D sees orders completed before 00:00 of D), before the 02:15 backup,
    # and well before the 03:00 reboot. Stage 4's worker reads it; the UI only shows it.
    billing_run_at: str = Field(default="00:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # The one currency the admin UI works in (the backend stays multi-currency).
    ui_currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")


settings = Settings()
