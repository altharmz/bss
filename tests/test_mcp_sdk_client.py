"""Client compatibility: the official MCP Python SDK's own client - OAuthClientProvider
(discovery from the 401, RFC 9728 + RFC 8414 metadata, dynamic registration, PKCE S256,
token exchange) over its Streamable HTTP transport - against the connector, in process.
The "browser" step logs in on our /login page like a person would.

Everything runs in one event loop: the connector's lifespan (identity check, session
manager) is entered by hand, and httpx2's ASGI transport carries the client's requests.
"""

from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
from mcp.client.auth import OAuthClientProvider
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from app.main import app as bss_app
from bss_mcp.clock import FixedClock
from bss_mcp.operations import OPERATIONS
from bss_mcp.server import build_app
from bss_mcp.store import Store
from tests.test_mcp_oauth import PASSWORD, USERS, add_user

pytestmark = pytest.mark.smoke

REDIRECT = "http://localhost:33418/callback"


class Memory:
    """The SDK's TokenStorage protocol, in memory."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


def test_the_sdk_client_signs_in_lists_and_calls(api, db_conn, mcp_settings):
    for name, password in USERS.items():
        add_user(db_conn, name, password)
    clock = FixedClock()
    connector = build_app(
        mcp_settings,
        clock=clock,
        store=Store(mcp_settings.mcp_store, clock),
        transport=httpx2.ASGITransport(app=bss_app),
    )
    transport = httpx2.ASGITransport(app=connector)
    storage = Memory()
    seen: dict[str, str] = {}

    async def browser(authorization_url: str) -> None:
        """What the person does: open the URL, sign in, approve read and write."""
        async with httpx2.AsyncClient(transport=transport, base_url="https://mcp.test") as web:
            to_login = await web.get(authorization_url)
            assert to_login.status_code == 302, to_login.text
            login_url = to_login.headers["location"]
            request_id = parse_qs(urlsplit(login_url).query)["request"][0]
            assert "bss SDK test" in (await web.get(login_url)).text
            back = await web.post(
                "/login",
                data={
                    "request": request_id,
                    "user": "demo",
                    "password": PASSWORD,
                    "scope": "write",
                    "action": "approve",
                },
            )
            assert back.status_code == 302, back.text
            seen["callback"] = back.headers["location"]

    async def callback() -> AuthorizationCodeResult:
        query = parse_qs(urlsplit(seen["callback"]).query)
        return AuthorizationCodeResult(code=query["code"][0], state=query.get("state", [None])[0])

    auth = OAuthClientProvider(
        server_url="https://mcp.test/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="bss SDK test",
            redirect_uris=[REDIRECT],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=storage,
        redirect_handler=browser,
        callback_handler=callback,
    )

    async def main() -> None:
        async with connector.router.lifespan_context(connector):
            async with (
                httpx2.AsyncClient(transport=transport, auth=auth) as http,
                streamable_http_client("https://mcp.test/mcp", http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert len(tools) == len(OPERATIONS)  # every operation, write included
                assert {"list_customers", "create_individual"} < tools
                created = await session.call_tool(
                    "create_individual", {"body": {"givenName": "Ada", "familyName": "L"}}
                )
                assert not created.is_error, created
                listed = await session.call_tool("list_customers", {"limit": 1})
                assert not listed.is_error and '"status":200' in listed.content[0].text

    anyio.run(main)
    assert storage.client is not None and storage.client.client_name == "bss SDK test"
    assert storage.tokens is not None and storage.tokens.scope == "read write"
    assert storage.tokens.refresh_token
