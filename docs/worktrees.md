# Local task worktrees

Use **1 task = 1 branch = 1 git worktree = 1 agent context**. The primary checkout
is administration-only and stays clean on `master`. Fetch `origin`, then manually create
an `issue-<number>-<lowercase-kebab-slug>` branch/worktree from fresh `origin/master`,
normally under `/home/petrg/projects/bss-worktrees/<branch>`. Start the agent there.
Do not switch branches in that worktree or mutate sibling worktrees.

## Local configuration

Create `.task/runtime.json` in the task worktree (ignored, excluded from builds):

```json
{
  "version": 1,
  "task_id": "issue-77-example-task",
  "app_port": 21000,
  "mcp_port": 21001,
  "web_port": 21002
}
```

These are the only fields. The task id must match the branch, follow the issue naming
convention, and be at most 80 characters. Ports must be distinct integers in 1024–65535,
excluding the repository's reserved VPN ports. Choose free ports explicitly; there is
no allocator or reservation registry. Metadata survives terminal/agent restarts and reboot.

Provision development secrets manually **outside** the worktree:
`~/.config/bss/tasks/<task_id>/.env`, or, when explicitly set,
`$XDG_CONFIG_HOME/bss/tasks/<task_id>/.env` (XDG_CONFIG_HOME must be absolute and external).
Use restrictive permissions (directory 700, file 600). No helper creates or copies this
file. Never copy `.env`, `.env.prod` or `.env.smoke` from the primary checkout.
Task worktrees must not contain these files. Existing primary secret files stay untouched.

The external file needs a fresh development-only `POSTGRES_PASSWORD`. Optional keys are
`POSTGRES_USER` and `POSTGRES_DB` (both default to `app`), `BSS_SERVICE_SECRET` (a fresh
development-only credential shared by app and MCP), and `MCP_USERS` (local OAuth hashes;
empty disables OAuth sign-in). No production or smoke credentials are needed.

## Runtime

Use the existing Make targets. For direct local Compose operations use
`./scripts/task-compose <command>`, never raw `docker compose`. The wrapper explicitly
selects the worktree's local Compose files and external secret file. It derives:

| Value | Derivation |
|---|---|
| Compose project | `bss-<task_id>` |
| Backend image | `bss-<task_id>-app:dev` |
| Frontend image | `bss-<task_id>-web:dev` |
| MCP public URL | `http://localhost:<mcp_port>` |

`./scripts/task-compose --check` validates context without starting Docker.
`./scripts/task-compose --url app` (or `mcp`, `web`) prints the task URL.
Only `config --quiet` is supported: resolved configuration contains secrets. Unset
inherited Compose settings, credentials and conflicting runtime overrides rather than
using a shell export to select a task. Missing identity or secret files fail closed.
The wrapper uses Docker's `default` context and rejects inherited `DOCKER_HOST` and
`DOCKER_CONTEXT` selectors. Development dotenv inputs use single-line assignments;
only the keys listed above are accepted, with no duplicate settings.

Each project owns its containers, network, PostgreSQL data, MCP SQLite/token data,
frontend dependencies and npm cache. PostgreSQL is unpublished; host services bind to
loopback. Build layers/base images may be shared, writable task state is separate.
Startup checks for occupied ports, permitting existing ports published by this project.
Docker's bind error handles a race after that check. No listener is killed and no port
is silently changed. Do not edit identity/ports while its stack is active; stop it first.

`make down` stops only this task and keeps its volumes. Existing `make clean` deletes
only this project's volumes; it is destructive. No lifecycle or cleanup automation is
provided. Browser cookies are not isolated by port; use separate browser contexts when
working with multiple task UIs.

## Hooks and checks

Run `make hooks` once from either checkout. Shared stable dispatchers resolve the invoking
worktree and execute its `scripts/pre-commit.sh` / `scripts/pre-push.sh`, preserving stdin
and exit status. Installation recognizes the old repository symlinks, is idempotent,
and refuses custom hooks or configured `core.hooksPath`. Hook logs remain per-worktree;
the heavy-check lock remains in the common Git directory. Concurrent coding/runtime is
supported; existing pre-commit heavy checks still queue. Cloud setup is unchanged and
is not a way to initialize concurrent local worktrees.
