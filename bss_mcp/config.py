from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The mcp service's environment. No DATABASE_URL: it never touches the database."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    log_level: str = "info"
    # The bss app. In prod `http://bss:8080` (the alias on `shared`, where every project's
    # service is called `app`); locally `http://app:8080`.
    bss_api_url: str = "http://app:8080"
    bss_service_secret: str = ""
    # How clients reach this service (no trailing slash); the OAuth issuer and the
    # protected resource (`<url>/mcp`) are built from it.
    mcp_public_url: str = "http://localhost:8090"
    mcp_store: str = "/data/mcp.db"
    # per principal, on /mcp
    mcp_rate_per_min: int = Field(default=60, ge=1)
    # bytes, any request
    mcp_max_body: int = Field(default=65536, ge=1024)
    # seconds, one call to the app
    mcp_api_timeout: float = Field(default=10, gt=0)
    # seconds the startup identity check waits for an app that does not answer yet
    mcp_identity_wait: float = Field(default=60, ge=0)

    # --- OAuth (bss_mcp/oauth.py, bss_mcp/login.py); lifetimes in seconds ---
    mcp_access_ttl: float = Field(default=3600, gt=0)
    mcp_refresh_ttl: float = Field(default=30 * 86400, gt=0)
    mcp_code_ttl: float = Field(default=300, gt=0)
    # the login page's link (one /authorize request) stays valid this long ...
    mcp_login_ttl: float = Field(default=600, gt=0)
    # ... and survives this many wrong passwords
    mcp_login_attempts: int = Field(default=5, ge=1)
    # per client IP, on POST /login, /token, /register; per user name on failed logins
    mcp_auth_rate_per_min: int = Field(default=20, ge=1)
    # open registration, capped
    mcp_max_clients: int = Field(default=1000, ge=1)
    # Behind the shared Caddy (prod): the client IP is the last X-Forwarded-For entry,
    # which Caddy sets. Locally there is no proxy: the peer address.
    mcp_trust_forwarded: bool = False
