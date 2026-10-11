# The MCP connector

bss's API as MCP tools, for any MCP client (claude.ai, ChatGPT, Claude Code, Cursor, ...).
**Demo only - no real data.** Decisions: [decisions/mcp.md](decisions/mcp.md).

> Built in slices. Done: **slice 1** - the server, static bearer tokens, scopes, rate
> limit, body limit, audit, with four hand-written tools; **slice 2** - OAuth 2.1 with
> dynamic client registration, discovery and the login page. Local only so far. Still to
> come: tools generated from every API operation (3), the production service and the
> vps-infra routing (4), client setup and token tooling (5). Sections for those land
> with them.

## Architecture

```
MCP client ──HTTPS──> Caddy (:8445, vps-infra) ──> mcp (bss_mcp, :8080) ──HTTP──> app (bss:8080)
                                                     │                    X-Remote-User: mcp:<name>
                                                     └── SQLite /data/mcp.db (mcp_data volume)
```

- **A separate service `mcp`**, the same image as the app (`uvicorn bss_mcp.main:app`).
  It imports the app's code only for `app.openapi()` and the request models; it reaches
  bss **only over HTTP** (`BSS_API_URL`: `http://bss:8080` in prod, `http://app:8080`
  locally) and has no `DATABASE_URL`.
- **Startup identity check**: before serving, `GET {BSS_API_URL}/` must answer exactly
  `{"service": "bss", "status": "ok"}`. No answer is retried for `MCP_IDENTITY_WAIT`
  seconds (default 60; the app may come up later after the nightly reboot); any other
  answer stops the service at once, naming what answered. On the `shared` network every
  project's service is called `app`, so a wrong URL would otherwise reach someone else's.
- **Transport**: Streamable HTTP at `/mcp`, stateless, JSON responses (no SSE), from the
  official MCP Python SDK (`mcp==2.3.0`, pinned with every dependency).
- **Its own store**: SQLite on the `mcp_data` volume - never the bss database. Tokens are
  kept as SHA-256 hashes only. Losing the file only means re-authorizing.

## Authentication and scopes

Every request to `/mcp` needs `Authorization: Bearer <token>`; without one, or with an
unknown or revoked one, the answer is **401** with
`WWW-Authenticate: Bearer ..., resource_metadata="<public url>/.well-known/oauth-protected-resource/mcp"`.

Two scopes, enforced **in the server** - nothing on the client side is trusted:

| Scope | Sees in `tools/list` | May call |
|---|---|---|
| `read` | the GET tools only | the GET tools |
| `write` | every tool | every tool (write implies read) |

A `read` token calling a write tool directly gets **HTTP 403** with
`WWW-Authenticate: Bearer error="insufficient_scope", scope="read write", ...` (the MCP
authorization spec's step-up answer). The request never reaches the app.

There are two ways to get a token: OAuth (any MCP client that supports connectors: only
the URL is configured) and static API tokens (clients that can send a header).

### OAuth 2.1 (the MCP authorization flow)

What a client does, from nothing but the URL `<public url>/mcp`:

1. `POST /mcp` without a token -> **401**, `WWW-Authenticate: Bearer ...,
   resource_metadata="<public url>/.well-known/oauth-protected-resource/mcp"`.
2. **Protected-resource metadata** (RFC 9728) at that URL (also at
   `/.well-known/oauth-protected-resource`): the resource `<public url>/mcp`, the
   authorization server `<public url>`, scopes `read`, `write`.
3. **Authorization-server metadata** (RFC 8414) at
   `/.well-known/oauth-authorization-server`: issuer `<public url>` (exactly, no
   trailing slash), the endpoints below, `S256` only, auth methods `none` (public
   clients), `client_secret_post`, `client_secret_basic`.
   `/.well-known/openid-configuration` is a plain **404** (no OpenID Connect; some
   clients probe it).
4. **Dynamic client registration** (RFC 7591) at `POST /register`, open to anyone.
   Redirect URIs must be **https**, or **http on localhost / 127.0.0.1 / [::1]** (local
   tools); 1-10 of them, no fragment; custom schemes are refused. At most
   `MCP_MAX_CLIENTS` (1000) clients.
5. **`GET /authorize`** (code + PKCE S256, the redirect URI exactly as registered, an
   optional `resource` that must be `<public url>/mcp`) -> 302 to the **login page**.
6. **`/login`**: one page for login and consent. It shows the client's self-declared name
   and - more trustworthy - the host the browser goes back to, and offers **Read only**
   or **Read and write** (write only if the client asked for it). User and password from
   `MCP_USERS`. Approve -> 302 to the redirect URI with `code` and `state`; Deny -> with
   `error=access_denied`. The link is single use, expires after 10 minutes and dies after
   5 wrong passwords; the page cannot be framed and escapes everything it shows.
7. **`POST /token`**: code + `code_verifier` -> an access token (`bss_at_...`, **1 hour**)
   and a refresh token (`bss_rt_...`, **30 days**). A code works once, for its client,
   within 5 minutes. Refreshing **rotates**: the old refresh token is spent; a refresh can
   narrow the scope, never widen it.
8. **`POST /revoke`** (RFC 7009): an access token, or a refresh token - which revokes its
   whole grant (every access token from that login).

The principal of an OAuth token is `mcp:<user>`. Users: `MCP_USERS` in `.env` /
`.env.prod` (format in `.env.example`; hashes are scrypt, made with
`docker compose run --rm --no-deps app python -m bss_mcp.passwords`).

Compatibility is tested with the official MCP Python SDK's own OAuth client
(`tests/test_mcp_sdk_client.py`): discovery, registration, PKCE, login, tools, as a real
client runs them.

### Static API tokens

`bss_st_...`, for clients that can send a custom header. A name
(`^[a-z0-9][a-z0-9._-]{0,47}$`) and a scope; shown once at creation, stored as a hash,
revocable. The make target that creates, lists and revokes them comes in slice 5.

### Principal and operator

**The principal** is `mcp:<user>` (OAuth) or `mcp:<token name>` (static). It goes to
the app as `X-Remote-User`, so writes are attributed - the app shows it as the operator
and records it in a bill's state history. A basic-auth user cannot contain `:` (it
separates user and password), so **an operator with `:` always comes from MCP**
([internal-api.md](decisions/internal-api.md)).

## Tools

One tool per API operation (slice 1: `list_customers`, `create_individual`,
`create_payment`, `patch_bill`). GET tools are annotated `readOnlyHint`, the others
`destructiveHint`. Arguments: the path parameters by name; for a list `offset`, `limit`,
`fields`, `filters` (attribute -> value); for a write `body`, whose schema is the app's
own request model. Arguments are checked against the schema before the app is called.

**Money is a decimal string** (`"19.99"`, at most 2 decimals). A JSON number with a
fraction anywhere in the arguments is refused: an MCP client's JSON parser turns numbers
into binary floats, and an amount must never pass through one. The string becomes an
exact JSON number on its way to the app.

The answer is the app's: `{"status", "totalCount"/"resultCount" (lists), "body"}`, amounts
exact. A 4xx/5xx from the app is a tool error (`isError`), not an MCP error.

## Limits

| What | Default | Setting |
|---|---|---|
| Requests per principal on `/mcp` | 60 per minute (token bucket), then **429** + `Retry-After` | `MCP_RATE_PER_MIN` |
| Request body, any route | 64 KiB, then **413** | `MCP_MAX_BODY` |
| One call to the app | 10 s, then a tool error | `MCP_API_TIMEOUT` |
| `POST /login`, `/token`, `/register` per client IP | 20 per minute, then **429** | `MCP_AUTH_RATE_PER_MIN` |
| Failed logins per user name | the same 20 per minute | `MCP_AUTH_RATE_PER_MIN` |
| Wrong passwords per login link | 5, then the link is gone | `MCP_LOGIN_ATTEMPTS` |
| Registered clients | 1000, then registration is refused | `MCP_MAX_CLIENTS` |

The client IP is the peer address, or - behind the shared Caddy, with
`MCP_TRUST_FORWARDED=true` - the last `X-Forwarded-For` entry (Caddy sets it).

## Audit

One JSON line per tool call (logger `bss_mcp.audit`, the container's log): `time`,
`event`, `principal`, `tool`, `outcome` (the app's status, `403`, `invalid_arguments`,
`timeout`, ...), plus `rate_limited` events, and one per OAuth event: `register`,
`login` (ok with the scope, failed, 429 - with the client IP), `consent` (denied),
`token` (issued, code reused), `refresh`, `revoke`. **Never** bodies, arguments, codes,
tokens or passwords (a test checks the log for each).

## Known demo risks

- **The bss app has no authentication of its own.** Today it is reachable without auth
  from any container on the VPS's `shared` network (as `bss:8080`) and from the VPS
  loopback (`127.0.0.1:18081`); Caddy's basic auth only guards the public route. The mcp
  service is one more caller on `shared`, not a new path. To close: a private app<->mcp
  network and a Caddy-side alias (separate task; CLAUDE.md Open checks).
- **Registration is open**, so a client's name on the login page is whatever its
  registrant chose ("Claude" included). The page also shows the host the browser goes
  back to and says to approve only a connection you just started; the password is the
  real gate.
- Public write access and prompt injection through data: see slice 5.
