# The MCP connector

bss's API as MCP tools, for any MCP client (claude.ai, ChatGPT, Claude Code, Cursor, ...).
**Demo only - no real data.** Decisions: [decisions/mcp.md](decisions/mcp.md).

> Built in five slices (server, OAuth, tools, production service, tooling and docs),
> all local: **not deployed yet**, and so not yet tried with the hosted clients below -
> the OAuth flow is tested with the official MCP SDK's own client. Deploying needs the
> vps-infra routing first ([Production](#production)).
>
> **The connector is the boundary.** An AI client calling these tools - Claude Code
> included - is not seen by this repo's Bash guard or permission rules: the server's
> scopes are the only control. Never add an unauthenticated mode.

## Architecture

```
MCP client ──HTTPS :443──> host nginx (SNI, raw TLS) ──> Caddy (:8445, vps-infra) ──> mcp (bss_mcp, :8080) ──HTTP──> app (bss:8080)
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
   or **Read and write** (write only if the client asked for it). User and password are an app user's with role **`mcp`** (the app checks them, `POST
   /internal/v1/service/credential-check`; an `admin` or `user` is refused like a wrong
   password). Approve -> 302 to the redirect URI with `code` and `state`; Deny -> with
   `error=access_denied`. The link is single use, expires after 10 minutes and dies after
   5 wrong passwords; the page cannot be framed and escapes everything it shows.
7. **`POST /token`**: code + `code_verifier` -> an access token (`bss_at_...`, **1 hour**)
   and a refresh token (`bss_rt_...`, **30 days**). A code works once, for its client,
   within 5 minutes. Refreshing **rotates**: the old refresh token is spent; a refresh can
   narrow the scope, never widen it.
8. **`POST /revoke`** (RFC 7009): an access token, or a refresh token - which revokes its
   whole grant (every access token from that login).

The principal of an OAuth token is `mcp:<app username>`. The users are the app's with role
`mcp` (UI -> Users, issue #173): the only role MCP accepts, and one that cannot sign in to
the UI. An administrator sets its password and hands it over; it has no forced first change.
The scopes (read, read and write) are the consent choice, not the role. The app also checks
the role on every call, so a user moved away from `mcp` loses its grants at once. Each grant remembers the
user's `credential_version`, which the app increments on a password change, a password
reset, a role change and a disable; the connector sends it with every call and, when the
app answers 401 `credentialRevoked`, ends the grant and answers the client 401
`invalid_token`, so it authorizes again.

Compatibility is tested with the official MCP Python SDK's own OAuth client
(`tests/mcp/test_mcp_sdk_client.py`): discovery, registration, PKCE, login, tools, as a real
client runs them.

### Static API tokens

`bss_st_...`, for clients that can send a custom header. A name
(`^[a-z0-9][a-z0-9._-]{0,47}$`) and a scope; shown once at creation, stored as a hash,
revocable; no expiry. Managed with the make targets in
[Token management](#token-management).

### Principal and operator

**The principal** is `mcp:<app username>` (OAuth) or `mcp-token:<token name>` (static). It goes to
the app as `X-Remote-User`, so writes are attributed - the app shows it as the operator
and records it in a bill's state history. A basic-auth user cannot contain `:` (it
separates user and password), so **an operator with `:` always comes from MCP**
([internal-api.md](decisions/internal-api.md)).

## Tools

**One tool per API operation**: every operation of `app.openapi()` except those
`docs/progress/scope.yaml` excludes and the interactive authentication/user-management
operations. The tool's name is the operationId, which is the handler's function name
(`create_payment`, `list_bills`, `catch_up`, ...). GET tools are annotated `readOnlyHint`
and are the only tools a read-scope token sees; the others are `destructiveHint` and need
the write scope. The client's `tools/list` is the authoritative list. If your client lists
fewer tools, search by API family; the families are the APIs in
[docs/api/README.md](api/README.md).

They are **generated at startup** (`bss_mcp/tools.py`) from `app.openapi()` and a small
registry (`bss_mcp/operations.py`) that adds what the OpenAPI document does not carry:

- **Arguments**: the path parameters by name; the query parameters - TMF lists `offset`,
  `limit`, `fields` and `filters` (an object whose attributes are exactly the equality
  filters that list accepts, read from the handler itself at startup), TMF get-one
  `fields`, the internal lists `offset`/`limit`, `first_charge` its
  `productOffering.id` and `startDate`, `run_accounts` its `status`; for a write `body`,
  whose schema is the request model the handler parses, `$ref`s inlined. Arguments are
  checked against the schema before the app is called; anything extra is refused.
  The registry also describes attributes consumed before model parsing:
  `patch_order.body.state: "acknowledged"` submits a draft. List filters can carry
  formats: `list_bill_cycles.filters.billingDate` is a date-time with offset for local
  midnight of the billing day in `BILLING_TZ`, such as `2026-10-12T21:00:00Z`.
- **Descriptions**: the summary, the API (TMF number and name, or the internal API), a
  one-line note condensed from the API notes (`docs/api/`), the handler's docstring if
  it has one, then the list filters, "JSON merge patch" for PATCH, "Changes data (write
  scope)" for writes, and the decimal-string rule where a body carries amounts.
  Passthrough documents name their vendored FVO schema, array/object shape and required
  keys without inlining the full polymorphic schema. TMF622 `externalId` keeps its
  existing lenient model.

**Drift check** (`tests/mcp/test_mcp_tools.py`, in `make test` and so in `make check`): the
tool set equals the API operations minus `scope.yaml`'s exclusions; the registry's copy
of the exclusions (the runtime image has no `docs/`) equals `scope.yaml`'s; every
POST/PATCH names the very model class its handler passes to `parse()` (read from the
handler's source); the list filters equal what `make docs` probes. A registry out of step
with the app also stops the service at startup (`DriftError`). Every GET tool is called
against the real app in the tests, and every write tool too.

**Money is a decimal string** (`"19.99"`, at most 2 decimals) - every amount: `Money.value`
and the internal price change's `amount`. A JSON number with a
fraction anywhere in the arguments is refused: an MCP client's JSON parser turns numbers
into binary floats, and an amount must never pass through one. The string becomes an
exact JSON number on its way to the app.

The answer is the app's: `{"status", "resultCount"/"hasMore" (lists: no total is counted, #312; `hasMore` comes from the app's `Link: rel="next"`), "body"}`, amounts
exact. A 4xx/5xx from the app is a tool error (`isError`), not an MCP error.

## Client setup

The server URL is **`https://bss2811.duckdns.org/mcp`** (locally
`http://localhost:8090/mcp`) - the standard port: claude.ai's connector service did not
reach `https://vpn2811.duckdns.org:8445/mcp` (2026-10-12, a 502 from its proxy), and the
OAuth metadata names only the new URL, so OAuth clients must use it. Clients that support MCP's OAuth need nothing else: they
discover the rest, register themselves and open the login page, where you choose **Read
only** or **Read and write**. Clients that cannot do OAuth with an https or localhost
redirect use a static token instead. Menu names below are as of 2026-10 and move often;
the URL and the header are what matter.

| Client | How | Redirect it registers |
|---|---|---|
| claude.ai (and Claude Desktop, which shares its connectors) | OAuth | `https://claude.ai/api/mcp/auth_callback` |
| ChatGPT | OAuth (developer mode) | `https://chatgpt.com/connector_platform_oauth_redirect` |
| Claude Code | OAuth, or a static token | `http://localhost:<port>/callback` |
| Cursor | **static token only** | a `cursor://` URI - refused |

**claude.ai** - Settings -> Connectors -> *Add custom connector*: a name (`bss`) and the
server URL; leave the advanced OAuth fields empty (the connector registers itself). Then
*Connect*: the login page opens; log in with your BSS `mcp` user and pick the scope. In a
chat, enable the connector from the tools menu. claude.ai asks before each write tool
call (they are marked `destructiveHint`); keep that on.

**ChatGPT** - custom MCP connectors with arbitrary tools need **developer mode**
(Settings -> Apps & Connectors -> Advanced settings -> Developer mode), then *Create*:
the server URL, authentication *OAuth*. Without developer mode ChatGPT only uses
connectors that offer `search`/`fetch` tools, which bss does not. ChatGPT asks for
confirmation before write calls.

**Claude Code** - with OAuth (a browser opens on the first use, or with `/mcp` ->
*Authenticate*; the redirect goes to a localhost port, which the policy allows):

```
claude mcp add --transport http bss https://bss2811.duckdns.org/mcp
```

With a static token instead (no browser; the token sits in Claude Code's config in
clear - prefer a `read` token, and `--scope local`, the default, keeps it to one
project):

```
claude mcp add --transport http bss https://bss2811.duckdns.org/mcp \
  --header "Authorization: Bearer bss_st_..."
```

`claude mcp list` shows the state; `claude mcp remove bss` drops it (revoke the token
too). **In this repository**: tool calls through the connector are not checked by
`.claude/hooks/guard.py` or the permission rules - see the note at the top.

**Cursor** - Cursor's OAuth redirect is a custom `cursor://` scheme, which registration
refuses (https or localhost http only, the agreed policy). Use a static token in
`~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project - not committed):

```json
{
  "mcpServers": {
    "bss": {
      "url": "https://bss2811.duckdns.org/mcp",
      "headers": { "Authorization": "Bearer ${env:BSS_MCP_TOKEN}" }
    }
  }
}
```

with the token in the environment Cursor starts from (or literally in the file, which
then is a secret). **Other clients**: any client speaking Streamable HTTP works with
either OAuth (https or localhost redirect) or the header; SSE-only clients do not.

## Token management

Static tokens and OAuth grants live in the connector's store; the tooling is
`python -m bss_mcp.tokens` (`bss_mcp/tokens.py`), run in the `mcp` service so it has
the store's volume. Revocation takes effect on the next request.

Locally:

```
make mcp-token-create NAME=ci SCOPE=read   # prints the token once, on stdout
make mcp-tokens                            # static tokens (id, name, scope, revoked) + live OAuth grants
make mcp-token-revoke ID=<token id>        # a static token
make mcp-token-revoke GRANT=<grant id>     # an OAuth login: all its access and refresh tokens
```

The OAuth smoke's client registrations (`bss-mcp-smoke`, without a live token) are pruned
by `deploy.sh` and `make mcp-smoke`; by hand: `python -m bss_mcp.tokens prune-smoke-clients`
in the `mcp` service. Other clients are never pruned.

On the VPS (the deployed image, against the `mcp-data` volume), through `make remote`:

```
make remote SERVICE=mcp CMD="python -m bss_mcp.tokens create ci --scope read"
make remote SERVICE=mcp CMD="python -m bss_mcp.tokens list"
make remote SERVICE=mcp CMD="python -m bss_mcp.tokens revoke <token id>"
make remote SERVICE=mcp CMD="python -m bss_mcp.tokens revoke-grant <grant id>"
```

A production token is a credential: **the human runs `create`** (`! make remote ...` in
Claude Code would print it into the session). The listing never shows a token, only ids.
Each create and revoke writes an audit line (`token_create`, `token_revoke`,
`grant_revoke`) to the run's stderr.

- **Users** (the OAuth login): the app's users with role `mcp` (UI -> Users -> New user,
  role `mcp`). Disabling a user, resetting their password or changing their role ends
  their grants at the next call (401, the client authorizes again); no redeploy. Moving a
  user to or from `mcp` needs a new password in the same edit: one password never serves
  both the UI and MCP.
- **Everything at once**: losing the store (the `mcp_data` / `mcp-data` volume) ends every
  token and client registration; clients re-register on their next use. Removing the
  volume is the human's call (CLAUDE.md: never `docker volume rm` from a session).

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
**Through `bss2811.duckdns.org` that is the docker gateway, not the client**: the host's
nginx forwards raw TCP, so Caddy only sees the gateway. Every client then shares one
per-IP bucket (20 a minute on login, token and registration, together), and the audit
log's client IP is the gateway. Accepted for the demo; the per-user and per-token limits
are unaffected.

## Audit

One JSON line per tool call (logger `bss_mcp.audit`, the container's log): `time`,
`event`, `principal`, `tool`, `outcome` (the app's status, `403`, `invalid_arguments`,
`timeout`, ...), plus `rate_limited` events, and one per OAuth event: `register`,
`login` (ok with the scope, failed, 429 - with the client IP), `consent` (denied),
`token` (issued, code reused), `refresh`, `revoke`; the token tooling adds
`token_create`, `token_revoke`, `grant_revoke` (on the run's stderr, not the service's
log). **Never** bodies, arguments, codes,
tokens or passwords (a test checks the log for each).

`GET /metrics` (Prometheus text format; 404 until `BSS_METRICS_TOKEN` is set, then a bearer
token) counts the same events: tool calls by tool and outcome, their duration by outcome,
and the OAuth events by result. It is fed from the audit function, so the two agree.
See [ops/monitoring/README.md](../ops/monitoring/README.md).

## Production

- **Service** `mcp` in `compose.prod.yaml`: the app's image, `uvicorn bss_mcp.main:app`,
  one worker, `--limit-concurrency 32`; 192 MB (about 75-100 MB used), 0.25 CPU,
  read-only root, `restart: unless-stopped` (back after the 03:00 reboot - the identity
  check waits up to 60 s for the app, then the restart policy retries). Publishes nothing;
  on `shared` as **`bss-mcp:8080`**. `BSS_API_URL=http://bss:8080`,
  `MCP_PUBLIC_URL=https://bss2811.duckdns.org` (every OAuth URL is built from it),
  `MCP_TRUST_FORWARDED=true`. Its store
  is the volume `mcp-data` (`/data/mcp.db`).
- The OAuth login needs at least one active app user with role `mcp`.
- **The smoke user** (optional, for `deploy.sh`'s OAuth smoke): create an `mcp` user in
  the UI (e.g. `mcp-smoke`, a long random password), then add two lines to the local
  `.env.smoke`: `MCP_SMOKE_USER=<name>` and `MCP_SMOKE_PASSWORD=<password>`. Each deploy
  then logs in as it with the read scope, calls one read tool, checks that a write tool
  is refused, refreshes and revokes, and then prunes its client registration. Without
  the lines the step prints SKIPPED. The password can obtain write like any `mcp` user's
  (docs/decisions/mcp.md): keep `.env.smoke` private; disable the user to stop it.
- **Moving to role `mcp`** (the deploy that ships issue #173): from that deploy on, MCP
  refuses the `user` accounts created for #146 and ends their grants, so MCP is unavailable
  until they are converted. Right after the deploy, for each of them: UI -> Users -> Edit ->
  role `mcp` (MCP only), a new password, Save; then reconnect the MCP client and log in with
  the new password.
- **Routing** (vps-infra's Caddy):
  ten exact, case-sensitive paths of the bss site (`bss2811.duckdns.org` since 2026-10-12;
  the older `vpn2811.duckdns.org:8445` name stopped resolving in 2026-11, #453 - 443 is the host's nginx SNI router, which passes that
  name as raw TLS to Caddy's :8445; Caddy holds its certificate) bypass basic auth
  and go to `bss-mcp:8080` - `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`,
  `/login`, `/.well-known/oauth-protected-resource`,
  `/.well-known/oauth-protected-resource/mcp`, `/.well-known/oauth-authorization-server`,
  `/.well-known/openid-configuration`. Everything else stays behind basic auth. Nothing of
  ours binds 443 or touches nginx or the VPN. No HTTP/3 is advertised for bss2811 (its
  Alt-Svc would point at UDP 8445, which nothing routes).
- **Deploy checks**: `deploy.sh` waits for `mcp` to be healthy with app and worker, then,
  after the web smoke test, runs **`scripts/mcp-smoke.sh <url> caddy`** through Caddy:
  discovery, the 401 challenge, the OAuth endpoints answered by the connector - and
  `/`, `/MCP`, `/Mcp`, `/mcp/x`, `/authorize/x`, an upper-case well-known path still
  behind basic auth, and no h3 in Alt-Svc - against `MCP_URL` (default
  `https://bss2811.duckdns.org`). It sends no credentials. Then
  **`scripts/mcp-oauth-smoke.py <url> caddy`** logs in as the smoke user (above), or
  prints SKIPPED without one. Locally, `make mcp-smoke` runs both, the prepaid tools with
  a throwaway token, and the OAuth login as throwaway `mcp` and `user` accounts it
  disables afterwards.

## Known demo risks

- **Network isolation remains incomplete.** The app is reachable from containers on
  the VPS's `shared` network (as `bss:8080`) and from VPS loopback
  (`127.0.0.1:18081`). Application authentication now protects business routes on
  these paths too; health probes and the service identity root remain public. A
  private app/MCP network and a Caddy-side alias remain a separate task
  (CLAUDE.md Open checks). Caddy basic auth was removed with U11.
- **Registration is open**, so a client's name on the login page is whatever its
  registrant chose ("Claude" included). The page also shows the host the browser goes
  back to and says to approve only a connection you just started; the password is the
  real gate.
- **Write access over the public internet.** Anyone holding an `mcp` user's password or a
  `write` token can create customers, orders, payments, price changes and billing runs on
  the demo instance - every write tool, with no per-tool approval on the server. Bounded
  by: demo data only (no real customers, ever), the scopes (consent offers read only),
  short access tokens, revocation, the rate limits, the audit log naming the principal.
  Bills and charges from a billing run are not undone through the API; a wrecked demo is
  restored from a backup or rebuilt.
- **Prompt injection through data.** Every text field in bss - names, notes,
  descriptions, reasons - is written by whoever had write access, and an AI client reads
  it back as tool output. A planted "ignore your instructions and ..." in a customer's
  name is just data to bss, but may steer a model that then calls write tools with the
  user's token. Mitigations: connect with **Read only** unless the session needs writes;
  keep the client's confirmation for write tools on; treat tool output as untrusted
  (no escaping makes text safe for a model).
- **The connector bypasses this repo's agent guardrails.** A Claude Code session with the
  connector added can change the deployed demo through tools that `.claude/hooks/guard.py`
  never sees (it matches Bash commands). The token's scope is the only limit; do not
  give an agent a `write` token it does not need.
- **Tokens in client configs.** A static token in `~/.claude.json` or `mcp.json` is in
  clear on that machine and has no expiry: prefer OAuth, use `read` tokens, name them
  per client so one can be revoked alone.
