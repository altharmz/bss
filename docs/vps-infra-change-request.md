# vps-infra change request: serve the bss admin UI (stage 5) and route its APIs

For the vps-infra session. bss does not change anything on the VPS itself except through
its own `scripts/deploy.sh`; everything below is vps-infra's Caddy and compose config.

**What changes for users**: `https://vpn2811.duckdns.org:8445/` stops answering the app's
JSON status and serves the admin UI instead. `/tmf-api/*` keeps reaching the app,
`/internal/*` is new, `/healthz` stays open, `/billing-status` is opened ahead of stage 4.

## 1. The static mount

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

## 2. The site block (replaces the current bss block on :8445)

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

## 3. The smoke user

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

## 4. Order of deploys

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
