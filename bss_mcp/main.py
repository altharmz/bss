"""uvicorn entry point: `uvicorn bss_mcp.main:app` (the `mcp` service)."""

import logging

from bss_mcp.config import Settings
from bss_mcp.server import build_app

settings = Settings()
logging.basicConfig(level=settings.log_level.upper())
app = build_app(settings)
