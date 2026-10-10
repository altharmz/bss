# vps-lab-template

A reusable skeleton for small FastAPI + PostgreSQL services: one command interface, real
health checks, and a deployment that fails loudly. Built in WSL2, verified on a small VPS
that also runs other things — a production VPN server, and possibly other services from
this same template.

What it gives you out of the box:

- FastAPI app with a database-checking `/healthz` and a JSON status root, wired to
  PostgreSQL via psycopg 3 and Alembic migrations.
- A `Makefile` covering the whole loop: build, run, lint, test, smoke-test, deploy, verify.
- A multi-stage `Dockerfile` (build once locally, run the same image everywhere) and a
  layered compose setup (local dev vs. VPS overlay).
- A deploy script that builds locally, ships the image over SSH, migrates, restarts, waits
  for a real health check, and prints remote logs on failure — never builds on the VPS.
- On the VPS, plugs into the shared infrastructure from **vps-infra** (one Postgres and
  one Caddy for every app on the host) instead of carrying its own copies; database
  backups are vps-infra's job too.
- Claude Code guardrails (`CLAUDE.md`, `.claude/settings.json`, `.claude/hooks/guard.sh`)
  that keep an agent off the VPN ports and off destructive commands.

## Creating a project from this template

The order matters: the VPS side (vps-infra) has to exist before this project's first
deploy, and `deploy.sh` refuses to run without it.

1. **vps-infra is deployed on the VPS** — once per host, not per project. It creates the
   external `shared` docker network and runs the one Postgres and the one Caddy that all
   apps use. If it isn't there yet, deploy it first (see its own README).

2. **Create and initialise this project:**

   ```bash
   git clone <this-template> my-service && cd my-service
   make init NAME=bss APP_PORT=18081 HTTPS_PORT=8443 DOMAIN=example.org
   ```

   This fills in the project name, ports and domain everywhere they appear in the repo
   (compose files, `Makefile`, scripts, `CLAUDE.md`, this README), creates `.env` and
   `.env.prod` from `.env.example` with a freshly generated database password in each,
   and then deletes the `init` target from the `Makefile` so it can't run a second time
   and re-mangle an already-substituted tree. Run it exactly once, right after cloning,
   before you touch anything else.

   `NAME` doubles as the Postgres database and role name on the VPS and as the app's
   alias on the `shared` network, so keep it to lowercase letters, digits and `_`.
   `HTTPS_PORT` is the port vps-infra's Caddy serves HTTPS on (never 443 — that belongs
   to the VPN side of the host).

3. **Create the database in vps-infra:**

   ```bash
   # in the vps-infra project
   make add-db NAME=bss
   ```

   This creates database and role `bss` in the shared Postgres, with the password
   taken from `DB_PASSWORD_<NAME in upper case>` in vps-infra's `.env.prod`. **Replace
   `POSTGRES_PASSWORD` in this project's `.env.prod` with that same password**:
   the value `make init` put there is only a temporary placeholder the shared Postgres
   doesn't know. A mismatch doesn't fail with a clear error — the migration step hangs on
   connection retries until `deploy.sh`'s 120s timeout. (`.env` stays as it is: local dev
   runs its own Postgres and never talks to vps-infra.)

4. **Add this app's block to vps-infra's `Caddyfile`** by hand, then deploy vps-infra so
   Caddy picks it up:

   ```
   vpn2811.duckdns.org:8445 {
       encode gzip
       log {
           output stdout
           format console
       }

       # Unauthenticated on purpose: external monitoring polls it. Must stay ahead of the
       # catch-all below (handle blocks are exclusive, first match wins).
       handle /healthz {
           reverse_proxy bss:8080
       }

       handle {
           # Inline bcrypt hash - basic_auth does not expand {env.*} in the password field.
           # Generate one with: docker run --rm caddy caddy hash-password
           basic_auth {
               <user> <bcrypt-hash>
           }
           reverse_proxy bss:8080
       }
   }
   ```

   Caddy reaches the app by its alias on the `shared` network (`bss:8080`, the
   port inside the container), not by the host port — that one exists only for
   `make tunnel`.

5. **Run it locally, then deploy:**

   ```bash
   make up      # start the local stack (app + its own postgres, hot reload)
   make smoke   # confirm it responds
   make verify  # lint + test + deploy to the VPS, must go healthy
   ```

Keep the repository inside the Linux filesystem (e.g. `~/projects/my-service`), not under
`/mnt/c/...`. Bind mounts across the Windows boundary are slow and inotify events do not
propagate, which silently breaks `--reload`.

## What `make init` does NOT set up for you

- **An SSH alias for the VPS.** `Makefile`/`scripts/deploy.sh` talk to a host named
  `vps-test` by default (override with `REMOTE=...`). Add a matching entry to your
  `~/.ssh/config` on the workstation, using key-only auth.
- **A `deploy` user on the server.** Create one, add it to the `docker` group, and drop
  your public key in its `authorized_keys`. Nothing here provisions the VPS itself.
- **Anything in vps-infra**: the `shared` network, the database (`make add-db`) and the
  Caddyfile block — steps 1, 3 and 4 above.
- **The DNS record** for `vpn2811.duckdns.org`, pointing at the VPS's public IP.

## Deploying

```bash
make deploy    # build -> ship -> restart -> wait for healthy -> remote smoke test
make verify    # lint + test + deploy, the full gate
make tunnel    # then open http://localhost:18081
```

On the VPS the project lives in `~/bss` (`REMOTE_DIR`, filled in from `NAME` by
`make init`): its compose files and `.env` go there. One directory per project, so several
apps from this template can share a VPS without overwriting each other's files.

## Built-in checks

- **`/healthz` checks the database**, not just that a process is alive. Docker's
  HEALTHCHECK and `deploy.sh` both gate on it, and it's the one route vps-infra's Caddy
  leaves unauthenticated for external monitoring.
- **`make verify`** is the single source of truth for "does it actually work on the
  server": deploy, wait for healthy, remote smoke test, non-zero exit and printed remote
  logs on any failure.
- **`make lint` / `make test`**, and a pre-commit hook (`make hooks` to install) that runs
  both before every commit.
- **Resource limits in `compose.prod.yaml`.** On a small box the OOM killer picks its own
  victim, and that victim could be something else entirely. Limits make this stack the one
  that dies, not its neighbor.
- **Log rotation**, so unrotated json-file logs don't fill the disk.
- **`127.0.0.1` binds only.** Nothing of this service is exposed on the public interface
  directly; only vps-infra's shared reverse proxy faces the internet.

## Claude Code setup

- `CLAUDE.md` — environment rules, allowed commands, hard constraints.
- `docs/decisions/` — the decision records by area, moved out of `CLAUDE.md`
  (`make decisions-check` proves nothing was lost).
- `.claude/settings.json` — allow/deny permissions.
- `.claude/hooks/guard.sh` — PreToolUse guard: blocks VPN-adjacent commands, destructive
  docker cleanup, privileged remote commands, and reads of `.env`.
- `.claude/hooks/format.sh` — PostToolUse: formats and lints edited Python immediately.
- `.claude/commands/ship.md`, `.claude/commands/diagnose.md` — `/ship` and `/diagnose`.
