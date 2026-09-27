# vps-lab

FastAPI + PostgreSQL service, developed in WSL2 and verified on a small VPS.
The point of this skeleton is a tight, honest feedback loop for agent-assisted work:
one command interface, real health checks, and a deployment that fails loudly.

## Architecture of the workflow

```
WSL2 (Ubuntu)                                  VPS "vps-test" (Debian 12, 1 vCPU / 2 GB)
------------                                   ----------------------------------------
edit -> make test -> make up -> make smoke     docker load  ->  compose up -d
                 |                                    ^                |
                 +--------- make deploy --------------+          healthcheck + smoke
                                                                        |
                            failure -> remote logs printed locally <----+
```

Images are always built in WSL. The VPS has one core and runs a production VPN server,
so it only ever loads and runs a finished image.

## First run

```bash
cp .env.example .env          # local
cp .env.example .env.prod     # VPS (use a different password)
make up
make smoke
```

Keep the repository inside the Linux filesystem (`~/projects/vps-lab`), not under
`/mnt/c/...`. Bind mounts across the Windows boundary are slow and inotify events do not
propagate, which silently breaks `--reload`.

## Deploying

```bash
make deploy    # build -> ship -> restart -> wait for healthy -> remote smoke test
make verify    # lint + test + deploy, the full gate
make tunnel    # then open http://localhost:18080
```

Prerequisites on the VPS: a `deploy` user in the `docker` group, key-only SSH, and an
`~/.ssh/config` entry named `vps-test` on the WSL side.

## Why things are the way they are

- **`/healthz` checks the database.** Docker's HEALTHCHECK and `deploy.sh` both gate on
  it, so a green health status means the service can actually serve, not merely that a
  process is alive.
- **Resource limits in `compose.prod.yaml`.** On a 2 GB box the OOM killer picks its own
  victim, and that victim could be the VPN. Limits make our stack the one that dies.
- **Log rotation.** 30 GB of disk disappears fast under unrotated json-file logs.
- **`127.0.0.1` binds only.** Nothing of ours is exposed on the public interface.
- **A `Makefile` instead of ad-hoc commands.** Stable names and honest exit codes are
  what an agent can act on; twenty variations of a `docker compose exec` line are not.

## Claude Code setup

- `CLAUDE.md` — environment rules, allowed commands, hard constraints.
- `.claude/settings.json` — allow/deny permissions.
- `.claude/hooks/guard.sh` — PreToolUse guard: blocks VPN-adjacent commands, destructive
  docker cleanup, privileged remote commands, and reads of `.env`.
- `.claude/hooks/format.sh` — PostToolUse: formats and lints edited Python immediately.
- `.claude/commands/ship.md`, `.claude/commands/diagnose.md` — `/ship` and `/diagnose`.
