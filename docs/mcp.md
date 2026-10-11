# The MCP connector

bss's API as MCP tools, for any MCP client (claude.ai, ChatGPT, Claude Code, Cursor, ...).
**Demo only - no real data.** Decisions: [decisions/mcp.md](decisions/mcp.md).

> Built in slices. Done: **slice 1** - the server, static bearer tokens, scopes, rate
> limit, body limit, audit, local only, with four hand-written tools. Still to come:
> OAuth 2.1 with DCR and the login page (2), tools generated from every API operation
> (3), the production service and the vps-infra routing (4), client setup and token
> tooling (5). Sections for those land with them.

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

**Static API tokens** (`bss_st_...`): for clients that can send a custom header. A name
(`^[a-z0-9][a-z0-9._-]{0,47}$`) and a scope; shown once at creation, stored as a hash,
revocable. The make target that creates, lists and revokes them comes in slice 5.

**The principal** is `mcp:<token name>` (OAuth, from slice 2: `mcp:<user>`). It goes to
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

## Audit

One JSON line per tool call (logger `bss_mcp.audit`, the container's log): `time`,
`event`, `principal`, `tool`, `outcome` (the app's status, `403`, `invalid_arguments`,
`timeout`, ...), plus `rate_limited` events. **Never** bodies, arguments, tokens or
passwords. From slice 2: one line per OAuth event as well.

## Known demo risks

- **The bss app has no authentication of its own.** Today it is reachable without auth
  from any container on the VPS's `shared` network (as `bss:8080`) and from the VPS
  loopback (`127.0.0.1:18081`); Caddy's basic auth only guards the public route. The mcp
  service is one more caller on `shared`, not a new path. To close: a private app<->mcp
  network and a Caddy-side alias (separate task; CLAUDE.md Open checks).
- Public write access and prompt injection through data: see slice 5.
