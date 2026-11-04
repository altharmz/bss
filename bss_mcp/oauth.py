"""OAuth 2.1 for MCP clients: the SDK's authorization-server routes (authorize, token,
register, revoke, RFC 8414 metadata) on top of this provider, which keeps everything in
the connector's SQLite store.

- Dynamic Client Registration (RFC 7591), open: redirect URIs must be https, or http on
  localhost / 127.0.0.1 / [::1] for local tools; at most MCP_MAX_CLIENTS clients.
- Authorization code + PKCE S256 (checked by the SDK's token handler). /authorize sends
  the browser to the login page (bss_mcp.login), which issues the code.
- Access tokens (bss_at_, MCP_ACCESS_TTL) and refresh tokens (bss_rt_, MCP_REFRESH_TTL,
  rotated on every use) are opaque, stored as hashes, revocable (/revoke; revoking a
  refresh token revokes its whole grant).

Expiry is decided here with the injected clock: the models handed to the SDK carry no
expiry of their own (the SDK would compare against the wall clock).
"""

import json
import logging
import secrets
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from bss_mcp.audit import audit
from bss_mcp.auth import READ, WRITE, Caller
from bss_mcp.clock import Clock
from bss_mcp.config import Settings
from bss_mcp.store import Store

log = logging.getLogger("bss_mcp")

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
MAX_REDIRECT_URIS = 10
MAX_CLIENT_NAME = 100


def granted_scopes(choice: str) -> list[str]:
    """The consent screen's choice -> the token's scopes (write implies read)."""
    return [READ, WRITE] if choice == WRITE else [READ]


def redirect_uri_allowed(uri: str) -> bool:
    parts = urlsplit(uri)
    if parts.fragment or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


class Provider:
    """The SDK's OAuthAuthorizationServerProvider protocol."""

    def __init__(self, store: Store, clock: Clock, settings: Settings) -> None:
        self.store = store
        self.clock = clock
        self.settings = settings
        self.issuer = settings.mcp_public_url.rstrip("/")
        self.resource = f"{self.issuer}/mcp"

    # --- clients ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.store.client(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]

        def refuse(code: Any, why: str) -> RegistrationError:
            audit(self.clock, event="register", outcome=f"refused: {code}")
            return RegistrationError(code, why)

        if not uris or len(uris) > MAX_REDIRECT_URIS:
            raise refuse("invalid_redirect_uri", f"1 to {MAX_REDIRECT_URIS} redirect_uris")
        for uri in uris:
            if not redirect_uri_allowed(uri):
                raise refuse(
                    "invalid_redirect_uri",
                    f"{uri}: redirect URIs must be https, or http on localhost/127.0.0.1",
                )
        if len(client_info.client_name or "") > MAX_CLIENT_NAME:
            raise refuse("invalid_client_metadata", f"client_name: at most {MAX_CLIENT_NAME}")
        if self.store.count_clients() >= self.settings.mcp_max_clients:
            raise refuse("invalid_client_metadata", "registration is closed (client limit)")
        self.store.save_client(client_info.client_id, client_info.model_dump_json())
        audit(self.clock, event="register", client_id=client_info.client_id, outcome="ok")

    # --- authorization ---

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the login page."""
        if params.resource is not None and params.resource.rstrip("/") != self.resource:
            raise AuthorizeError("invalid_request", f"resource must be {self.resource}")
        request_id = secrets.token_urlsafe(32)
        self.store.save_pending(
            request_id, client.client_id, params.model_dump_json(), self.settings.mcp_login_ttl
        )
        return f"{self.issuer}/login?request={request_id}"

    def issue_code(
        self,
        client_id: str,
        params: AuthorizationParams,
        principal: str,
        scopes: list[str],
        credential_version: int,
    ) -> str:
        """Called by the login page after a successful login and consent."""
        code = secrets.token_urlsafe(32)
        data = {
            "client_id": client_id,
            "scopes": scopes,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
            "subject": principal,
        }
        # not an AuthorizationCode field: read back from the store at the exchange
        stored = {**data, "credential_version": credential_version}
        self.store.save_code(code, client_id, json.dumps(stored), self.settings.mcp_code_ttl)
        return code

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = self.store.code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        # expiry: checked by the store with the injected clock; the SDK's own check
        # (against the wall clock) gets a value it never trips
        data = json.loads(row["data"])
        data.pop("credential_version", None)
        return AuthorizationCode(code=authorization_code, expires_at=float("inf"), **data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        row = self.store.code(authorization_code.code)
        if row is None or not self.store.use_code(authorization_code.code):
            audit(self.clock, event="token", client_id=client.client_id, outcome="code reused")
            raise TokenError("invalid_grant", "authorization code already used")
        assert authorization_code.subject is not None
        tokens = self._issue(
            client.client_id,
            authorization_code.subject,
            authorization_code.scopes,
            authorization_code.resource,
            json.loads(row["data"])["credential_version"],
            grant_id=secrets.token_hex(8),
        )
        audit(
            self.clock,
            event="token",
            principal=authorization_code.subject,
            client_id=client.client_id,
            outcome="issued",
        )
        return tokens

    # --- refresh ---

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self.store.token(refresh_token, "refresh")
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotation: the refresh token is spent, a new pair is issued in the same grant."""
        live = self.store.token(refresh_token.token, "refresh")
        row = self.store.revoke_token(refresh_token.token)
        if live is None or row is None or row["kind"] != "refresh":
            raise TokenError("invalid_grant", "refresh token already used or revoked")
        assert refresh_token.subject is not None
        scopes = granted_scopes(WRITE if WRITE in scopes else READ)
        tokens = self._issue(
            client.client_id,
            refresh_token.subject,
            scopes,
            refresh_token.resource,
            live["credential_version"],
            grant_id=row["grant_id"],
        )
        audit(
            self.clock,
            event="refresh",
            principal=refresh_token.subject,
            client_id=client.client_id,
            outcome="issued",
        )
        return tokens

    # --- access tokens and revocation ---

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.token(token, "access")
        if row is None:
            return None
        return Caller(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            resource=row["resource"],
            subject=row["subject"],
            grant_id=row["grant_id"],
            credential_version=row["credential_version"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        row = self.store.revoke_token(token.token)
        if row is not None and row["kind"] == "refresh":
            self.store.revoke_grant(row["grant_id"])
        audit(
            self.clock,
            event="revoke",
            principal=token.subject,
            client_id=token.client_id,
            outcome=f"{row['kind']} revoked" if row else "already revoked",
        )

    async def exchange_identity_assertion(self, client: Any, params: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "not supported")

    def _issue(
        self,
        client_id: str,
        principal: str,
        scopes: list[str],
        resource: str | None,
        credential_version: int,
        *,
        grant_id: str,
    ) -> OAuthToken:
        access = "bss_at_" + secrets.token_urlsafe(32)
        refresh = "bss_rt_" + secrets.token_urlsafe(32)
        common = {
            "grant_id": grant_id,
            "client_id": client_id,
            "subject": principal,
            "scopes": scopes,
            "resource": resource,
            "credential_version": credential_version,
        }
        self.store.save_token(access, kind="access", ttl=self.settings.mcp_access_ttl, **common)
        self.store.save_token(refresh, kind="refresh", ttl=self.settings.mcp_refresh_ttl, **common)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",  # noqa: S106 - the token type, not a secret
            expires_in=int(self.settings.mcp_access_ttl),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )
