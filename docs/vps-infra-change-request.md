# vps-infra change requests from bss

For the vps-infra session. bss does not change anything on the VPS itself except through
its own `scripts/deploy.sh`; everything below is vps-infra's Caddy and compose config.

- **Open: [the MCP connector](#2026-10-11-route-the-mcp-connector)** (below).
- Applied: [the admin UI and its APIs](#stage-5-serve-the-bss-admin-ui-and-route-its-apis)
  (stage 5, deployed 2026-10-02 with `9424b9b`) - kept for reference.

## 2026-10-11: route the MCP connector

bss gains a third service, `mcp` (docs/mcp.md): a public MCP server with its own OAuth
2.1 authorization server, for AI clients (claude.ai, ChatGPT, Claude Code, Cursor, ...).
It has to be reachable **without basic auth** on a fixed, explicit list of paths of the
existing site `https://vpn2811.duckdns.org:8445`; everything else stays exactly as it is.
**Port 443 and anything VPN-related are not touched; no new port, no new site, no new
certificate.** Nothing changes in vps-infra's compose file: bss's `mcp` service joins
`shared` itself, with the network alias `bss-mcp`, and listens on 8080 there.

### The carve-out (add to the bss site block, before the basic-auth `route`)

These ten paths, exactly as written, go to `bss-mcp:8080` without basic auth:

| Path | What |
|---|---|
| `/mcp` | the MCP endpoint (Streamable HTTP; bearer tokens checked by the service) |
| `/authorize` | OAuth authorization endpoint (redirects to `/login`) |
| `/token` | OAuth token endpoint |
| `/register` | OAuth dynamic client registration (RFC 7591) |
| `/revoke` | OAuth token revocation (RFC 7009) |
| `/login` | the login and consent page (GET shows it, POST submits it) |
| `/.well-known/oauth-protected-resource` | RFC 9728 metadata (root form) |
| `/.well-known/oauth-protected-resource/mcp` | RFC 9728 metadata for `/mcp` - the URL the 401 names |
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata |
| `/.well-known/openid-configuration` | answered 404 by the service (no OpenID Connect); carved out so clients that probe it get a plain 404, not a Basic challenge |

```caddyfile
	# --- unauthenticated: the MCP connector (bss-mcp). Exact, case-sensitive paths:
	# an expression, NOT a path matcher - Caddy's path matcher is case-insensitive ---
	@mcp expression `{path} in ["/mcp", "/authorize", "/token", "/register", "/revoke", "/login", "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"]`
	handle @mcp {
		reverse_proxy bss-mcp:8080 {
			header_up -X-Remote-User
		}
	}
```

Place it next to the existing `handle /healthz` and `handle /billing-status`, above the
`route { basic_auth ... }` block - the same structure as those two carve-outs.

Notes:

- **Why an expression matcher.** Caddy's `path` matcher compares case-insensitively; with
  `@mcp path /mcp ...` the rehearsal answered `GET /MCP` from the connector, without
  basic auth. The expression compares exact strings, so `/MCP`, `/Mcp`, `/mcp/`,
  `/mcp/x` and an upper-case `/.well-known/...` all stay behind basic auth. (The same
  applies to the existing `handle /healthz` and `handle /billing-status`: they also match
  `/HEALTHZ` etc. Harmless there - the app answers 404 to those spellings - and not part
  of this request; mentioned so nobody copies the pattern for something that matters.)
- **Exact paths only.** Query strings (`/authorize?...`, `/login?request=...`) are not
  part of `{path}` and pass. Nothing under these paths (`/mcp/x`) and no other spelling is
  carved out.
- **`header_up -X-Remote-User`**: like the other open routes. The service sets its own
  `X-Remote-User: mcp:<user or token name>` on its calls to the app; a client-sent one
  must never reach it (it ignores it anyway).
- **`Authorization` passes through unchanged** - it carries the MCP clients' bearer
  tokens. No basic-auth credentials are involved on these paths.
- **X-Forwarded-For**: Caddy's default (it replaces a client-sent value with the real
  client IP, since no `trusted_proxies` is configured) is exactly what the service
  expects: with `MCP_TRUST_FORWARDED=true` it takes the last entry as the client IP for
  its per-IP rate limits and audit log. Do not add `trusted_proxies` for this site.
- **No CORS, header or timeout changes**: the service sets CORS on its OAuth endpoints
  itself; requests are short (no SSE, no long-lived streams).
- **Load**: the service is limited to 0.25 CPU and 192 MB, and rate-limits per client
  (60 MCP requests a minute per token; 20 a minute per IP on login, token and
  registration).

### Rehearsal (2026-10-11, on the workstation)

Caddy **2.11.4** (`caddy validate`: valid), the block above in the stage-5 structure
(`handle /healthz`, the carve-out, `route { basic_auth ... }`), plain HTTP on
`127.0.0.1:18445`, in front of a prod-like `mcp` container built from the runtime image
(read-only root, tmpfs, 192 MB, 0.25 CPU, `MCP_TRUST_FORWARDED=true`, alias `bss-mcp`) and
the local app. Results:

- `scripts/mcp-smoke.sh http://localhost:18445 caddy`: all checks pass - both metadata
  documents (issuer exact), `POST /mcp` -> 401 Bearer with `resource_metadata`,
  `openid-configuration` -> 404, `/login`, `/authorize`, `/token`, `/revoke`,
  `/register` answered by the connector; `/`, `/MCP`, `/Mcp`, `/mcp/x`, `/authorize/x`
  and an upper-case well-known path -> 401 Basic.
- A login POST with `X-Forwarded-For: 6.6.6.6` was audited with the real client IP.
- Negative proofs: the same paths with a `path` matcher -> the smoke test fails (`GET
  /MCP answered 404 without basic auth`); `/token` dropped from the list -> it fails
  (`POST /token answered 401 Basic`).
- Memory of the prod-like container after startup: 73 MB of 192 MB.

### Order of deploys

1. **vps-infra**: add the block, reload Caddy. Until bss's `mcp` runs, these ten paths
   answer 502 - nothing else changes. Check: `GET /` and `GET /tmf-api/...` still ask for
   basic auth; `/healthz` is still open.
2. **bss**: `MCP_USERS` in `.env.prod` (at least one user; `deploy.sh` and compose refuse
   to start without it), then `make verify` - which now also waits for `mcp` to be
   healthy and, after the web smoke test, runs `scripts/mcp-smoke.sh <url> caddy`
   through this Caddy (it sends no credentials).
3. If step 2's MCP smoke test fails, app, UI and mcp are already deployed and healthy;
   fix the Caddy side and re-run `make deploy`.

**Rollback**: remove the block and reload Caddy - the ten paths fall back to basic auth
and the rest of the site is unchanged. The `mcp` service can stay running unreachable, or
bss removes it from `compose.prod.yaml` in a later deploy.

---

## Stage 5: serve the bss admin UI and route its APIs

*Applied - deployed 2026-10-02 with `9424b9b`. Kept as the reference for the current
block.*

**What changes for users**: `https://vpn2811.duckdns.org:8445/` stops answering the app's
JSON status and serves the admin UI instead. `/tmf-api/*` keeps reaching the app,
`/internal/*` is new, `/healthz` stays open, `/billing-status` is opened ahead of stage 4.

### 1. The static mount

- The UI's files live in **`~/bss/web`** on the VPS (the deploy user's home), written only
  by bss's `deploy.sh` with rsync **into** the directory - never replaced or renamed.
- **Create the directory before Caddy is restarted with the mount**, as the deploy user:
  `mkdir -p ~/bss/web`. Otherwise Docker creates it for the bind mount, owned by root,
  and bss's rsync can no longer write into it.
- Mount it into the Caddy container **read-only**:

      volumes:
        - /home/<deploy user>/bss/web:/srv/bss-web:ro

  A directory bind mount holds the directory's inode, which is why bss never replaces the
  directory: files inside it are swapped by rsync and Caddy sees them immediately.

### 2. The site block (replaces the current bss block on :8445)

Validated locally with Caddy 2.11.4 (`caddy validate`, then a rehearsal container serving
a real build in front of the bss app, with bss's `scripts/web-smoke.sh` passing; removing
the `/internal` route, the `X-Remote-User` header or the smoke user's 403 each made the
smoke fail). The rehearsal differed only in: plain HTTP on :80, upstream `app:8080`, and
throwaway hashes.

```caddyfile
vpn2811.duckdns.org:8445 {
	# --- unauthenticated: the external monitor ---
	handle /healthz {
		reverse_proxy bss:8080 {
			header_up -X-Remote-User
		}
	}
	# stage 4 freshness signal (date, status, age only - no amounts or names); until the
	# stage 4 worker ships, the app answers 404 here, which is fine
	handle /billing-status {
		reverse_proxy bss:8080 {
			header_up -X-Remote-User
		}
	}

	# --- everything else: basic auth, then the API or the static UI, in this literal order ---
	route {
		basic_auth {
			<operator login> <bcrypt hash>
			smoke <bcrypt hash of the smoke password>
		}

		# the smoke user is read-only: any method but GET/HEAD is refused before the app
		@smoke_write expression `{http.auth.user.id} == "smoke" && {method} != "GET" && {method} != "HEAD"`
		respond @smoke_write "read-only user" 403

		# the APIs go to the app; the operator header is SET (overwritten) from the
		# authenticated user, so a client-sent X-Remote-User never passes through
		@api path /tmf-api/* /internal/*
		reverse_proxy @api bss:8080 {
			header_up X-Remote-User {http.auth.user.id}
		}

		# the UI: an existing file, or index.html for client-side routes
		root * /srv/bss-web
		try_files {path} /index.html
		header Cache-Control "no-cache"
		header /assets/* Cache-Control "public, max-age=31536000, immutable"
		file_server
	}
}
```

Notes:

- **`route`, not `handle` + directive order.** Inside `route` the directives run in the
  order written. With Caddy's default order, `handle`/`reverse_proxy` would run before
  `respond`, and the smoke user's POST would reach the app.
- **`/internal/*` must be in `@api`.** Without it `/internal/v1/context` falls through to
  `try_files` and silently answers `index.html` with 200 - the web smoke checks for JSON
  precisely because of this.
- **`header_up X-Remote-User {http.auth.user.id}`** sets the header (replacing a
  client-sent one); `header_up -X-Remote-User` on the open routes removes it. The app
  shows it as the operator name only; nothing is authorised by it (docs/decisions/internal-api.md,
  "Trust in X-Remote-User").
- **Caching**: `index.html` is `no-cache` (a new deploy is picked up at once); the hashed
  `/assets/*` are immutable.
- `/healthz` stays open for the external monitor, as today. `/billing-status` becomes the
  stage 4 alerting signal through the same monitor.

### 3. The smoke user

- A second basic-auth user **`smoke`** with its **own** password, hashed with
  `caddy hash-password` (run it in the Caddy container; the plaintext never goes into the
  Caddyfile or the vps-infra repo).
- GET/HEAD only (the `@smoke_write` 403 above).
- The plaintext goes into bss's **`.env.smoke`** on the workstation - a local-only file,
  gitignored and never shipped (unlike `.env.prod`, which deploy.sh copies to the VPS as
  `~/bss/.env`) - as `SMOKE_BASIC_AUTH=smoke:<password>` (see bss's `.env.example`). The
  password is therefore never on the VPS except as Caddy's hash. bss's deploy refuses to
  start without `.env.smoke`, refuses if `.env.prod` has a `SMOKE_BASIC_AUTH` line, and
  never prints the value.

### 4. Order of deploys

1. **Backup**: make sure a fresh dump of the `bss` database exists (vps-infra's backup).
   Migration 0003 only adds two nullable columns, but it is the first migration since
   production data exists.
2. **vps-infra**: `mkdir -p ~/bss/web` as the deploy user; add the smoke user's hash; add
   the read-only mount; replace the bss site block; reload/recreate Caddy. Check:
   `curl -s -o /dev/null -w '%{http_code}' https://vpn2811.duckdns.org:8445/healthz` -> 200,
   and `/` with credentials -> 404 (empty `web/`, expected until step 3).
3. **bss**: `SMOKE_BASIC_AUTH` in `.env.smoke` (not `.env.prod`), optionally
   `DRY_RUN=1 ./scripts/deploy.sh` (local checks, the exact list of shipped files, no
   network), then `make verify` from the workstation:
   backend and UI checks, the e2e smoke, then `deploy.sh` - image, migration 0003, app,
   healthcheck, the UI rsynced into `~/bss/web`, and the web smoke through Caddy (all the
   checks in section 2's notes, plus `POST` as smoke -> 403).
4. If step 3's web smoke fails, the app itself is already deployed and healthy (the UI
   ships last); fix the Caddy side and re-run `make deploy` - the image is not re-shipped
   when it is already on the server.

Rollback: the previous Caddy block restores the old behaviour (`/` = the app's JSON
status); bss's database schema stays at 0003 (adding columns is backward compatible with
the previous app image).
