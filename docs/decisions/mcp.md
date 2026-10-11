# MCP connector

User-facing description: [docs/mcp.md](../mcp.md).

## Slice 1: server, static tokens, scopes (2026-10-11)

Decisions as taken (plan D1-D6 agreed with the human):

- **Same image, own service, HTTP only (D5).** `bss_mcp/` sits next to `app/`, not in it
  (`app/`'s wall-clock rule stays as it is; bss_mcp has its own `bss_mcp/clock.py`). It
  imports the app for `app.openapi()` and the request models only, and calls the app over
  HTTP - never the database. One image means one ship and one tag, and the tools always
  match the API they call.
- **SDK, not hand-rolled.** The official `mcp` package, pinned exactly (`mcp==2.3.0`; its
  dependencies frozen in both constraints files - resolved in the pinned base image,
  `rpds-py` kept at the dev image's `2026.6.3` so both images run the same version). The
  low-level `Server`, because `tools/list` depends on the caller's scope; the SDK's
  `BearerAuthBackend` and `RequireAuthMiddleware` for 401 + `WWW-Authenticate`.
- **Stateless Streamable HTTP with JSON responses**: no SSE, no sessions to keep in a
  1-vCPU container.
- **operationId = the handler's function name (D1)**: `generate_unique_id_function` in
  `app/main.py`; `tests/test_operation_ids.py` keeps them unique, `^[a-z0-9_]+$` and at
  most 64 characters (clients' tool-name limit). `/openapi.json` changes with it; it was
  never the contract (the vendored specs are).
- **Scope enforcement before the SDK**: `Guard` reads a `tools/call` and answers a write
  tool with a read token by HTTP 403 `insufficient_scope` - the request never reaches the
  app. `call_tool` checks again (defence in depth); `tools/list` hides write tools from a
  read token.
- **Money as decimal strings, floats refused outright**: the app reads exact JSON numbers
  only; an MCP client's parser makes them floats. A float anywhere in the arguments is
  refused first, then the arguments are checked against the tool's schema (proof: with
  the float check removed a float is still refused, by the schema, only the message
  differs).
- **`X-Remote-User: mcp:<name>` (D3)**: the app's operator pattern gained `:`; a
  basic-auth user cannot contain one, so `:` marks MCP.
- **API URL per environment (D4)**: `http://bss:8080` in prod (the alias on `shared`,
  where `app` is ambiguous), `http://app:8080` locally; plus the **startup identity check**
  (`GET /` must be exactly bss's answer, else the service does not start).
- **Static tokens**: `bss_st_` + 32 random bytes, SHA-256 in SQLite, names
  `^[a-z0-9][a-z0-9._-]{0,47}$` (so `mcp:<name>` fits the operator pattern), unique.
- **Rate limit** in memory, token bucket per principal (one process; a restart refills).
  **Body limit** read in full before anything else on every route.
- **Audit** names its fields explicitly - there is no way to pass a body or token to it.

Negative proofs (slice 1, each made with Edit and reverted): Guard's scope check off ->
the 403 test fails (200); `tools/list` unfiltered -> the read-token listing test fails;
rate limit off -> 200 instead of 429; no `X-Remote-User` -> operator None; the app's
operator pattern without `:` -> both operator tests fail; identity check accepting any
200 -> "DID NOT RAISE NotBss"; float check off -> the float tests fail on the message
(still refused by the schema).

## Slice 2: OAuth 2.1 (2026-10-11)

- **The SDK's authorization-server routes** (`create_auth_routes`) on our provider
  (`bss_mcp/oauth.py`), not a hand-rolled OAuth: the SDK validates the requests, checks
  PKCE S256 and the redirect URI at /token, refuses `plain`. Three adjustments, each
  in `bss_mcp/routes.py` with its reason:
  - the RFC 8414 metadata is served by us with `none` added to the auth methods (MCP
    clients register as public clients; the SDK accepts them but does not list it);
  - the issuer is passed as a string so it stays exactly `https://host` (an `AnyHttpUrl`
    becomes `https://host/`);
  - `/revoke`: mcp 2.3.0 types `client_secret` as `str | None` *without a default*, so a
    public client could not revoke (400) - an absent one is sent on as empty
    (`PublicClientRevoke`).
- **Expiry with the injected clock only.** The SDK compares code and refresh expiry with
  `time.time()`; the provider hands it models without an expiry (codes: `inf`) and the
  store decides with `bss_mcp/clock.py`. Expiry is therefore testable, and a frozen test
  clock never meets the wall clock.
- **Login and consent are one page** (`/login`): the carve-out list stays at the paths the
  human named (no `/consent`). The `request` id is the only handle on the parked
  /authorize request - unguessable, single use, 10 minutes, 5 wrong passwords - and so
  the form's CSRF token. No `form-action` in the CSP: Chrome applies it to the redirect
  after the POST, which goes to the client.
- **Users**: `MCP_USERS=name:hash,...`, stdlib scrypt (n=2^14, r=8, p=1), `.`-separated -
  no `$`, which compose would interpolate in `.env`. An unknown user costs a scrypt too
  (no timing difference).
- **Scopes**: registration defaults to `read write`; consent narrows to read or keeps
  write (only if the client asked for it); a token's scopes are `[read]` or `[read,
  write]`; a refresh can narrow, never widen (the SDK's check).
- **Tokens**: opaque random strings, SHA-256 in SQLite; refresh rotates (the old one is
  spent); revoking a refresh token revokes its grant. Redirect URIs: https, or http on
  localhost/127.0.0.1/[::1] - custom schemes refused (the human's policy; a client
  needing one, e.g. some desktop apps, will not connect).
- **Rate limits on the auth endpoints** per client IP (and failed logins per user name);
  behind Caddy the IP is the last `X-Forwarded-For` entry (`MCP_TRUST_FORWARDED`, set in
  prod in slice 4).

Negative proofs (slice 2, each made with Edit and reverted): redirect-URI policy off ->
the 5 refusal cases fail; password check passing any known user -> wrong-password,
other-user's-password and the password unit test fail; client name unescaped -> the
escaping test fails; per-IP limit off -> no 429; consent offering write to a read-only
client -> its test fails; refresh not rotated -> the old refresh token works again;
grant not revoked with its refresh token -> the access token still works; token expiry
ignored -> 200 after 3601 s. `use_code` always succeeding did **not** fail the "code
works once" test - the spent code is already gone when loaded; its rowcount guards the
race of two exchanges that both loaded the code first, so a test for exactly that was
added, and it fails without the guard ("DID NOT RAISE TokenError").
