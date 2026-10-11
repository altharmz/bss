from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The mcp service's environment. No DATABASE_URL: it never touches the database."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    log_level: str = "info"
    # The bss app. In prod `http://bss:8080` (the alias on `shared`, where every project's
    # service is called `app`); locally `http://app:8080`.
    bss_api_url: str = "http://app:8080"
    # How clients reach this service (no trailing slash); the OAuth issuer and the
    # protected resource (`<url>/mcp`) are built from it.
    mcp_public_url: str = "http://localhost:8090"
    mcp_store: str = "/data/mcp.db"
    # name:<scrypt hash>[,name:<hash>] - the OAuth login users (docs/mcp.md)
    mcp_users: str = ""
    # per principal, on /mcp
    mcp_rate_per_min: int = Field(default=60, ge=1)
    # bytes, any request
    mcp_max_body: int = Field(default=65536, ge=1024)
    # seconds, one call to the app
    mcp_api_timeout: float = Field(default=10, gt=0)
    # seconds the startup identity check waits for an app that does not answer yet
    mcp_identity_wait: float = Field(default=60, ge=0)
