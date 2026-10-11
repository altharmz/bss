"""The OAuth and discovery routes, and the per-IP limit on the auth endpoints."""

import math

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bss_mcp.audit import audit
from bss_mcp.auth import READ, WRITE
from bss_mcp.clock import Clock
from bss_mcp.login import Login, client_ip
from bss_mcp.oauth import Provider
from bss_mcp.ratelimit import RateLimit

SCOPES = [READ, WRITE]
AUTH_METADATA = "/.well-known/oauth-authorization-server"
# public clients (no secret) are what MCP clients register as; the SDK accepts them
# but does not list the method
AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
LIMITED = ("/login", "/token", "/register")


def oauth_routes(provider: Provider, login: Login) -> list[Route]:
    issuer = AnyHttpUrl(provider.issuer)
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
    )
    revocation = RevocationOptions(enabled=True)
    routes = []
    for route in create_auth_routes(
        provider,
        issuer_url=issuer,
        client_registration_options=registration,
        revocation_options=revocation,
    ):
        if route.path == AUTH_METADATA:
            continue  # replaced below
        if route.path == "/revoke":
            route = Route("/revoke", PublicClientRevoke(route.endpoint), methods=route.methods)
        routes.append(route)
    # the issuer as a string: the metadata models keep it exactly ("https://host"), while
    # an AnyHttpUrl object would already be normalised to "https://host/"
    metadata = build_metadata(provider.issuer, None, registration, revocation)  # type: ignore[arg-type]
    metadata.token_endpoint_auth_methods_supported = AUTH_METHODS
    metadata.revocation_endpoint_auth_methods_supported = AUTH_METHODS
    routes.append(
        Route(
            AUTH_METADATA,
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
    )
    # RFC 9728: <host>/.well-known/oauth-protected-resource/mcp for the resource /mcp; the
    # root form too, for clients that probe it first
    [resource] = create_protected_resource_routes(
        resource_url=AnyHttpUrl(provider.resource),
        authorization_servers=[provider.issuer],  # type: ignore[list-item] - as above
        scopes_supported=SCOPES,
        resource_name="bss (demo)",
    )
    routes += [resource, Route("/.well-known/oauth-protected-resource", resource.endpoint)]

    async def no_openid(request: Request) -> JSONResponse:
        return JSONResponse({"error": "not_found", "error_description": "no OpenID Connect"}, 404)

    routes += [
        Route("/.well-known/openid-configuration", no_openid, methods=["GET"]),
        Route("/login", login.get, methods=["GET"]),
        Route("/login", login.post, methods=["POST"]),
    ]
    return routes


class PublicClientRevoke:
    """mcp 2.3.0's RevocationRequest types client_secret as `str | None` without a
    default, so pydantic requires it: a public client (token_endpoint_auth_method none,
    no secret - what MCP clients register as) could not revoke (400). An absent
    client_secret is sent on as empty; the SDK's client authentication ignores it for
    public clients, and a confidential client still has to send its real one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        message = await receive()  # BodyLimit replays the whole body in one message
        body = message.get("body", b"")
        if b"client_secret=" not in body:
            body = body + (b"&" if body else b"") + b"client_secret="
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class AuthIpLimit:
    """POST /login, /token, /register: at most MCP_AUTH_RATE_PER_MIN per client IP."""

    def __init__(self, app: ASGIApp, *, limit: RateLimit, clock: Clock, trust: bool) -> None:
        self.app = app
        self.limit = limit
        self.clock = clock
        self.trust = trust

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] in LIMITED:
            ip = client_ip(Request(scope), self.trust)
            wait = self.limit.take(f"ip:{ip}")
            if wait is not None:
                audit(self.clock, event=scope["path"].lstrip("/"), ip=ip, outcome=429)
                response = JSONResponse(
                    {"error": "rate_limited", "error_description": "too many requests"},
                    429,
                    headers={"Retry-After": str(math.ceil(wait))},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
