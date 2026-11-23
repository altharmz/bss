"""Shared builders of tests/test_mcp_tools.py, used by other test modules (#423)."""

import anyio

from app.main import app as bss_app
from bss_mcp import tools
from bss_mcp.tools import generate


def generated() -> dict[str, tools.Tool]:
    return anyio.run(generate, bss_app)


def api_operations() -> dict[str, tuple[str, str]]:
    return {
        op["operationId"]: (method.upper(), path)
        for path, ops in bss_app.openapi()["paths"].items()
        for method, op in ops.items()
    }
