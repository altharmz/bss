"""uvicorn entry point: `uvicorn bss_mcp.main:app` (the `mcp` service)."""

from app import logsetup
from bss_mcp.config import Settings
from bss_mcp.server import build_app

settings = Settings()
logsetup.configure("mcp")
app = build_app(settings)
