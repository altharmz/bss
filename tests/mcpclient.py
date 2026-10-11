"""The MCP connector under test: bss_mcp's app in process, calling the real app (the `api`
fixture's: FixedClock, throwaway database) through an ASGI transport - no network, no
container. Requests are plain JSON-RPC over HTTP, the way any MCP client sends them."""

import itertools
import json
from typing import Any

from starlette.testclient import TestClient

from bss_mcp.clock import FixedClock
from bss_mcp.store import Store

ACCEPT = "application/json, text/event-stream"
_ids = itertools.count(1)


class Mcp:
    def __init__(self, client: TestClient, store: Store, clock: FixedClock) -> None:
        self.client = client
        self.store = store
        self.clock = clock

    def token(self, name: str, scope: str) -> str:
        return self.store.create_static_token(name, scope)[1]

    def post(self, message: Any, token: str | None = None, **headers: str):
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.post(
            "/mcp",
            content=json.dumps(message),
            headers={"Accept": ACCEPT, "Content-Type": "application/json", **headers},
        )

    def rpc(self, method: str, params: dict[str, Any] | None, token: str | None):
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_ids), "method": method}
        if params is not None:
            message["params"] = params
        return self.post(message, token)

    def tools(self, token: str) -> dict[str, dict[str, Any]]:
        response = self.rpc("tools/list", None, token)
        assert response.status_code == 200, response.text
        return {t["name"]: t for t in response.json()["result"]["tools"]}

    def call(self, token: str, name: str, arguments: dict[str, Any] | None = None):
        """The HTTP response of a tools/call (for a 4xx before the tool runs)."""
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, token)

    def result(self, token: str, name: str, arguments: dict[str, Any] | None = None):
        """(isError, the tool's answer: the app's {status, body, ...}, or the error text)."""
        response = self.call(token, name, arguments)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        text = result["content"][0]["text"]
        try:
            return result["isError"], json.loads(text)
        except ValueError:
            return result["isError"], text
