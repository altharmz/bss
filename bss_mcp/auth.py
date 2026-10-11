"""Who is calling, and what they may do.

Every bearer token - a static API token now, an OAuth access token from slice 2 on -
resolves to the SDK's AccessToken with:
  scopes   ["read"] or ["read", "write"] - write implies read;
  subject  the principal, "mcp:<user or token name>", sent to the app as X-Remote-User.

The scope is enforced here, in the server: a read token is not shown the write tools and
is refused (HTTP 403) if it calls one anyway. Nothing on the client side is trusted.
"""

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.types import Scope as AsgiScope

from bss_mcp.store import Scope, Store

READ = "read"
WRITE = "write"


def scopes_for(scope: Scope) -> list[str]:
    return [READ, WRITE] if scope == WRITE else [READ]


def principal_of(name: str) -> str:
    return f"mcp:{name}"


class TokenVerifier:
    """The SDK's TokenVerifier protocol: bearer token -> AccessToken, or None (401)."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        static = self.store.find_static_token(token)
        if static is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"static:{static.name}",
            scopes=scopes_for(static.scope),
            subject=principal_of(static.name),
        )


def caller(scope: AsgiScope) -> AccessToken | None:
    """The authenticated caller of an HTTP request (set by the SDK's BearerAuthBackend)."""
    user = scope.get("user")
    return user.access_token if isinstance(user, AuthenticatedUser) else None
