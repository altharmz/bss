"""The login and consent page that /authorize sends the browser to (one page, /login).

GET shows who is asking (the client's self-declared name and, more trustworthy, the host
its redirect goes to) and the scope choice; POST checks the password (MCP_USERS) and
either issues the authorization code or answers access_denied - always by redirecting to
the redirect URI registered for the client.

The `request` parameter is the only handle on the parked /authorize request: unguessable,
bound to it, single use and short-lived - which also makes it the form's CSRF token. The
page cannot be framed. Everything shown is escaped (the client name is attacker-chosen:
registration is open).
"""

import html
from urllib.parse import urlsplit

from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from bss_mcp.audit import audit
from bss_mcp.auth import WRITE, principal_of
from bss_mcp.clock import Clock
from bss_mcp.config import Settings
from bss_mcp.oauth import Provider, granted_scopes
from bss_mcp.passwords import check_password
from bss_mcp.ratelimit import RateLimit
from bss_mcp.store import NAME

HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # no form-action: after the POST the browser is redirected to the client
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline';"
    " frame-ancestors 'none'; base-uri 'none'",
}

STYLE = """
body{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;
color:#1d1d1f;background:#fff}
@media (prefers-color-scheme:dark){body{color:#eee;background:#161618}
input{background:#222;color:#eee;border-color:#555}}
h1{font-size:1.3rem}.who{padding:.75rem 1rem;border:1px solid #8884;border-radius:8px}
label{display:block;margin:.75rem 0 .25rem}input[type=text],input[type=password]{width:100%;
box-sizing:border-box;padding:.5rem;border:1px solid #8888;border-radius:6px;font:inherit}
fieldset{border:1px solid #8884;border-radius:8px;margin:1rem 0}
.err{color:#c0392b}.small{font-size:.85rem;opacity:.8}
button{font:inherit;padding:.5rem 1rem;margin-right:.5rem;border-radius:6px;cursor:pointer}
"""


def page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>bss sign-in</title><style>{STYLE}</style></head><body>{body}</body></html>",
        status_code=status,
        headers=HEADERS,
    )


def gone() -> HTMLResponse:
    return page(
        "<h1>This sign-in link is no longer valid</h1><p>It expired, was already used, or"
        " had too many wrong passwords. Start the connection again from your MCP client.</p>",
        400,
    )


def client_ip(request: Request, trust_forwarded: bool) -> str:
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "?"


class Login:
    def __init__(
        self,
        provider: Provider,
        users: dict[str, str],
        clock: Clock,
        settings: Settings,
        user_limit: RateLimit,
    ) -> None:
        self.provider = provider
        self.store = provider.store
        self.users = users
        self.clock = clock
        self.settings = settings
        self.user_limit = user_limit

    async def _load(self, request_id: str):
        row = self.store.pending(request_id) if request_id else None
        if row is None:
            return None
        client = await self.provider.get_client(row["client_id"])
        if client is None:
            return None
        return client, AuthorizationParams.model_validate_json(row["params"])

    def _form(self, request_id: str, client, params, error: str = "", status: int = 200):
        name = html.escape(client.client_name or "an unnamed client")
        host = html.escape(urlsplit(str(params.redirect_uri)).netloc)
        can_write = params.scopes is None or WRITE in params.scopes
        write = (
            '<label><input type="radio" name="scope" value="write"> <b>Read and write</b> -'
            " also create and change data</label>"
            if can_write
            else '<p class="small">This client asked for read access only.</p>'
        )
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        return page(
            f"<h1>Connect to bss (demo)</h1>"
            f'<div class="who"><b>{name}</b> wants access.<br>'
            f'<span class="small">After you approve, you go back to <b>{host}</b>.'
            f" Only approve if you just started this connection yourself.</span></div>"
            f'<form method="post" action="/login">{err}'
            f'<input type="hidden" name="request" value="{html.escape(request_id)}">'
            f'<label for="u">User</label><input id="u" type="text" name="user"'
            f' autocomplete="username" required>'
            f'<label for="p">Password</label><input id="p" type="password" name="password"'
            f' autocomplete="current-password" required>'
            f"<fieldset><legend>Access</legend>"
            f'<label><input type="radio" name="scope" value="read" checked>'
            f" <b>Read only</b></label>"
            f"{write}</fieldset>"
            f'<button type="submit" name="action" value="approve">Approve</button>'
            f'<button type="submit" name="action" value="deny" formnovalidate>Deny</button>'
            f"</form>",
            status,
        )

    async def get(self, request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        loaded = await self._load(request_id)
        if loaded is None:
            return gone()
        return self._form(request_id, *loaded)

    async def post(self, request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        loaded = await self._load(request_id)
        if loaded is None:
            return gone()
        client, params = loaded
        ip = client_ip(request, self.settings.mcp_trust_forwarded)

        def back(**query: str | None) -> RedirectResponse:
            url = construct_redirect_uri(str(params.redirect_uri), state=params.state, **query)
            return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})

        if form.get("action") == "deny":
            self.store.drop_pending(request_id)
            audit(self.clock, event="consent", client_id=client.client_id, ip=ip, outcome="denied")
            return back(error="access_denied", error_description="the user denied access")

        user = str(form.get("user", ""))
        password = str(form.get("password", ""))
        known = NAME.match(user) is not None
        principal = principal_of(user) if known else None
        if known and self.user_limit.take(f"user:{user}") is not None:
            audit(self.clock, event="login", principal=principal, ip=ip, outcome=429)
            return self._form(request_id, client, params, "Too many attempts. Wait a minute.", 429)
        if not check_password(password, self.users.get(user) if known else None):
            self.store.fail_pending(request_id, self.settings.mcp_login_attempts)
            audit(
                self.clock,
                event="login",
                principal=principal,
                client_id=client.client_id,
                ip=ip,
                outcome="failed",
            )
            if self.store.pending(request_id) is None:
                return gone()
            return self._form(request_id, client, params, "Wrong user or password.", 401)

        choice = str(form.get("scope", "read"))
        allowed = ["read", WRITE] if params.scopes is None or WRITE in params.scopes else ["read"]
        if choice not in allowed:
            return self._form(request_id, client, params, "Choose one of the offered scopes.", 400)
        if not self.store.drop_pending(request_id):  # used up in a parallel request
            return gone()
        assert principal is not None
        code = self.provider.issue_code(client.client_id, params, principal, granted_scopes(choice))
        audit(
            self.clock,
            event="login",
            principal=principal,
            client_id=client.client_id,
            ip=ip,
            outcome=f"ok, scope {choice}",
        )
        return back(code=code)
