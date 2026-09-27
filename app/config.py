from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment - no config files."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: str = "dev"
    log_level: str = "info"
    database_url: str = "postgresql://app:app@db:5432/app"
    db_connect_timeout: int = 3


settings = Settings()
