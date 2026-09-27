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
- Automated + manual database backups, and a `make restore` that requires a human to
  confirm before it overwrites anything.
- Claude Code guardrails (`CLAUDE.md`, `.claude/settings.json`, `.claude/hooks/guard.sh`)
  that keep an agent off the VPN ports and off destructive commands.

## Creating a project from this template

```bash
git clone <this-template> my-service && cd my-service
make init NAME=bss APP_PORT=18081 HTTPS_PORT=8443 DOMAIN=example.org
```

This fills in the project name, ports and domain everywhere they appear in the repo
(compose files, `Makefile`, scripts, `CLAUDE.md`, `Caddyfile`), creates `.env` and
`.env.prod` from `.env.example` with a freshly generated database password in each, and
then deletes the `init` target from the `Makefile` so it can't run a second time and
re-mangle an already-substituted tree. Run it exactly once, right after cloning, before
you touch anything else.

```bash
make up      # start the local stack (app + postgres, hot reload)
make smoke   # confirm it responds
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
- **The reverse-proxy block**, if this VPS already runs Caddy for another service. Set
  `WITH_CADDY=false` in `.env.prod` (the default), and add this block by hand to the
  Caddyfile that's already running there:

  ```
  __DOMAIN__:__HTTPS_PORT__ {
      handle /healthz {
          reverse_proxy 127.0.0.1:__APP_PORT__
      }
      handle {
          basic_auth {
              <user> <bcrypt-hash>
          }
          reverse_proxy 127.0.0.1:__APP_PORT__
      }
  }
  ```

  (Reached via the host's published port because that Caddy lives in a different compose
  project's network.) Then reload that Caddy's config — deploying this service does not do
  it for you.

  If this is instead the *first* service on the VPS, set `WITH_CADDY=true` and
  `scripts/deploy.sh` will start and manage this repo's own `Caddyfile` and `caddy`
  container for you.
- **The DNS record** for `__DOMAIN__`, pointing at the VPS's public IP.

## Deploying

```bash
make deploy    # build -> ship -> restart -> wait for healthy -> remote smoke test
make verify    # lint + test + deploy, the full gate
make tunnel    # then open http://localhost:__APP_PORT__
```

## Built-in checks

- **`/healthz` checks the database**, not just that a process is alive. Docker's
  HEALTHCHECK and `deploy.sh` both gate on it, and it's the one route Caddy leaves
  unauthenticated for external monitoring.
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
  directly; only the reverse proxy (this project's own, or another service's shared one)
  faces the internet.

## Claude Code setup

- `CLAUDE.md` — environment rules, allowed commands, hard constraints.
- `.claude/settings.json` — allow/deny permissions.
- `.claude/hooks/guard.sh` — PreToolUse guard: blocks VPN-adjacent commands, destructive
  docker cleanup, privileged remote commands, and reads of `.env`.
- `.claude/hooks/format.sh` — PostToolUse: formats and lints edited Python immediately.
- `.claude/commands/ship.md`, `.claude/commands/diagnose.md` — `/ship` and `/diagnose`.
