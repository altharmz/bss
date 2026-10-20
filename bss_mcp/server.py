"""The connector's ASGI app: the MCP endpoint (Streamable HTTP, stateless, JSON
responses - no SSE), bearer authentication, scopes, rate limit, body limit and audit.

Routes: /mcp; the OAuth endpoints /authorize, /token, /register, /revoke (the SDK's, on
bss_mcp.oauth.Provider) and /login (bss_mcp.login); discovery at
/.well-known/oauth-authorization-server (RFC 8414) and
/.well-known/oauth-protected-resource[/mcp] (RFC 9728); /.well-known/openid-configuration
answers 404 (no OpenID Connect - some clients probe it); /healthz.

Request path for /mcp, outermost first:
  BodyLimit          413 above MCP_MAX_BODY, before anything else reads the body
  AuthIpLimit        (POST /login, /token, /register only) 429 per client IP
  Authentication     the SDK's BearerAuthBackend: token -> AccessToken (bss_mcp.auth)
  RequireAuth        the SDK's: 401 + WWW-Authenticate (resource_metadata) without one
  Guard              429 per principal; 403 for a write tool called with a read token
  the SDK's session manager -> list_tools / call_tool below
"""

import json
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import mcp_types as types
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bss_mcp import schema
from bss_mcp.audit import audit
from bss_mcp.auth import WRITE, TokenVerifier, caller
from bss_mcp.bss import Bss
from bss_mcp.clock import Clock
from bss_mcp.config import Settings
from bss_mcp.login import Login
from bss_mcp.oauth import Provider
from bss_mcp.passwords import parse_users
from bss_mcp.ratelimit import RateLimit
from bss_mcp.routes import AuthIpLimit, oauth_routes
from bss_mcp.store import Store
from bss_mcp.tools import Tool, generate, result_text

log = logging.getLogger("bss_mcp")

INSTRUCTIONS = (
    "bss: a demo billing and customer system on the TMF Open APIs (no real data). Each tool"
    ' is one API operation. Amounts are decimal strings ("19.99"), never JSON numbers.'
    " Tools that change data need the write scope."
    " There are 153 tools with write scope (69 with read scope): TMF620 catalog,"
    " TMF632 party, TMF629 customer, TMF666 accounts, TMF676 payments, TMF637 inventory,"
    " TMF639 subscriber numbers,"
    " TMF678 bills, TMF622 orders, internal operations and service health."
    " If your client lists fewer, search by family."
)


async def _send_json(
    send: Send, status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> None:
    payload = json.dumps(body).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": payload})


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class BodyLimit:
    """Reads the whole request body (at most `limit` bytes) before the app does; 413
    above it. Every route gets it, the OAuth endpoints too."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                await _send_json(send, 413, {"error": f"request body above {self.limit} bytes"})
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        await self.app(scope, _replay(b"".join(chunks), receive), send)


class Guard:
    """After authentication: the per-principal rate limit, and the scope of a tools/call
    decided before the SDK sees it - a write tool with a read token is an HTTP 403
    (insufficient_scope, the MCP authorization spec's step-up answer) and never reaches
    the app."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: RateLimit,
        clock: Clock,
        tools: dict[str, Tool],
        resource_metadata_url: str,
    ) -> None:
        self.app = app
        self.limit = limit
        self.clock = clock
        self.tools = tools
        self.resource_metadata_url = resource_metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = caller(scope)
        assert token is not None  # RequireAuthMiddleware runs first
        principal = token.subject
        wait = self.limit.take(f"principal:{principal}")
        if wait is not None:
            audit(self.clock, event="rate_limited", principal=principal, outcome=429)
            await _send_json(
                send,
                429,
                {"error": "rate_limited", "error_description": "too many requests"},
                {"Retry-After": str(math.ceil(wait))},
            )
            return
        if scope["method"] == "POST":
            message = await receive()  # BodyLimit replays the whole body in one message
            body = message.get("body", b"")
            tool = self._called_tool(body)
            if tool is not None and not tool.read_only and WRITE not in token.scopes:
                audit(
                    self.clock, event="tool_call", principal=principal, tool=tool.name, outcome=403
                )
                await _send_json(
                    send,
                    403,
                    {
                        "error": "insufficient_scope",
                        "error_description": f"{tool.name} changes data: it needs the write scope",
                    },
                    {
                        "WWW-Authenticate": 'Bearer error="insufficient_scope", scope="read write",'
                        f' resource_metadata="{self.resource_metadata_url}"'
                    },
                )
                return
            receive = _replay(body, receive)
        await self.app(scope, receive, send)

    def _called_tool(self, body: bytes) -> Tool | None:
        try:
            message = json.loads(body)
        except ValueError:
            return None  # the SDK answers malformed JSON itself
        if not isinstance(message, dict) or message.get("method") != "tools/call":
            return None
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        return self.tools.get(name) if isinstance(name, str) else None


def _tool_listing(tool: Tool) -> types.Tool:
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        annotations=types.ToolAnnotations(
            read_only_hint=tool.read_only,
            destructive_hint=not tool.read_only,
            open_world_hint=False,
        ),
    )


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def build_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    store: Store | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
    tools: dict[str, Tool] | None = None,
) -> Starlette:
    """The ASGI app. Tests pass a FixedClock, a store on a temporary file, an ASGI
    transport to the in-process app, and may pass their own tools."""
    clock = clock or Clock()
    store = store or Store(settings.mcp_store, clock)
    # filled at startup (lifespan) from the app's operations, unless a test passes its own;
    # Guard and the handlers hold this same dict
    given_tools = tools is not None
    tools = dict(tools or {})
    bss = Bss(
        settings.bss_api_url,
        settings.mcp_api_timeout,
        transport,
        service_secret=settings.bss_service_secret,
    )
    public = settings.mcp_public_url.rstrip("/")
    resource_metadata_url = f"{public}/.well-known/oauth-protected-resource/mcp"

    async def list_tools(
        ctx: Any, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        token = caller(ctx.request.scope)
        writer = token is not None and WRITE in token.scopes
        return types.ListToolsResult(
            tools=[_tool_listing(t) for t in tools.values() if writer or t.read_only]
        )

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        token = caller(ctx.request.scope)
        assert token is not None and token.subject is not None
        principal = token.subject

        def done(outcome: str | int) -> None:
            audit(clock, event="tool_call", principal=principal, tool=params.name, outcome=outcome)

        tool = tools.get(params.name)
        if tool is None:
            done("unknown_tool")
            return _error(f"no tool named {params.name}")
        if not tool.read_only and WRITE not in token.scopes:  # Guard refused it already
            done(403)
            return _error(f"{tool.name} changes data: it needs the write scope")
        arguments = dict(params.arguments or {})
        where = schema.find_float(arguments)
        if where is not None:
            done("invalid_arguments")
            return _error(
                f"{where}: a JSON number with a fraction is not accepted - send amounts as"
                ' decimal strings, e.g. "19.99"'
            )
        problems = schema.errors(tool.input_schema, arguments)
        if problems:
            done("invalid_arguments")
            return _error("invalid arguments:\n" + "\n".join(problems))
        request = tool.request(arguments)
        try:
            response = await bss.request(principal=principal, **request)
        except httpx2.TimeoutException:
            done("timeout")
            return _error(f"bss did not answer within {settings.mcp_api_timeout:g} s")
        except httpx2.TransportError as exc:
            done("unreachable")
            log.warning("call to bss failed: %r", exc)
            return _error("bss is not reachable")
        done(response.status_code)
        text = result_text(response.status_code, dict(response.headers), response.content)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            is_error=response.status_code >= 400,
        )

    server = Server(
        "bss",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        max_request_body_size=settings.mcp_max_body,
    )
    mcp_endpoint = RequireAuthMiddleware(
        Guard(
            StreamableHTTPASGIApp(manager),
            limit=RateLimit(clock, settings.mcp_rate_per_min),
            clock=clock,
            tools=tools,
            resource_metadata_url=resource_metadata_url,
        ),
        required_scopes=[],
        resource_metadata_url=resource_metadata_url,
    )

    async def healthz(request: Request) -> JSONResponse:
        """Cheap: the store answers. The app's health is the app's own /healthz."""
        try:
            store.ping()
        except Exception as exc:  # noqa: BLE001 - the reason belongs in the log
            log.warning("healthz: store unreachable: %s", exc)
            return JSONResponse({"status": "degraded"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if not given_tools:
            from app.main import app as bss_app  # its code, never its database

            tools.update(await generate(bss_app))
            log.info("tools: %d generated from the app's operations", len(tools))
        await bss.identify(clock, settings.mcp_identity_wait)
        log.info("identity check: %s is bss", settings.bss_api_url)
        async with manager.run():
            yield
        await bss.aclose()

    users = parse_users(settings.mcp_users)
    if not users:
        log.warning("MCP_USERS is empty: nobody can sign in through OAuth (static tokens work)")
    provider = Provider(store, clock, settings)
    auth_limit = RateLimit(clock, settings.mcp_auth_rate_per_min)
    login = Login(provider, users, clock, settings, user_limit=auth_limit)

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/mcp", mcp_endpoint),
            *oauth_routes(provider, login),
        ],
        middleware=[
            Middleware(BodyLimit, limit=settings.mcp_max_body),
            Middleware(
                AuthIpLimit,
                limit=RateLimit(clock, settings.mcp_auth_rate_per_min),
                clock=clock,
                trust=settings.mcp_trust_forwarded,
            ),
            Middleware(
                AuthenticationMiddleware,
                backend=BearerAuthBackend(TokenVerifier(store, provider)),
            ),
            Middleware(AuthContextMiddleware),
        ],
        lifespan=lifespan,
    )
