# Local task worktrees

Use **1 task = 1 branch = 1 git worktree = 1 agent context**. The primary checkout
is administration-only and stays clean on `master`. Run `make start-task` there
(`make start-task JSON=1` for one JSON result on stdout). It fetches/prunes `origin`,
fast-forwards `master`, and prepares an `issue-<number>-<lowercase-kebab-slug>` branch
from that fetched `origin/master` in the sibling `<checkout-name>-worktrees/<branch>`
directory. Start the agent in the returned directory.
Do not switch branches in that worktree or mutate sibling worktrees.

## Task preparation

Git, GitHub CLI with repository/project read access, and host Python 3 are required.
Only the primary checkout on clean `master` is accepted; staged, tracked and non-ignored
untracked changes fail. Local commits ahead of or diverging from `origin/master` fail;
there is no stash, reset, rebase or history merge. Tracked secret paths in either the
current or incoming tree are refused before checkout; ignored primary secrets are untouched.

Selection follows [board.md](board.md): open BSS issues in Ready, P0 before P1 before P2
before P3, then ascending manual board position. `blocked`, `needs-human`, any `agent:`
owner label and open GitHub blocking dependencies exclude an issue. Doing, Waiting,
Inbox and Done are not backlog candidates. Existing local/remote task branches (including
cloud task branches), registered worktrees, GitHub-linked branches, and open PRs by task
branch or closing reference exclude their issue; this command never resumes tasks.
Missing/multiple priority labels, incomplete responses, duplicate items or a filtered,
sorted or non-unique board view fail rather than inventing an order. Ready remains the
triage source of truth; the helper does not interpret dependencies from free-form prose.

The helper reads GitHub only. Claim the returned issue under the existing ownership/WIP
rules before implementation. It creates no issue, label, status, PR or remote branch.
The title is normalized to ASCII lowercase kebab form (fallback `task`) and truncated
so the whole branch is at most 80 characters. Existing branch/path/registration
collisions fail without reuse. A lock in the common Git directory serializes preparation
for this checkout; it is not a daemon or a cross-clone claim service.

Ports are allocated from **21000–21998**, in aligned blocks `(21000,21001,21002)`,
`(21003,21004,21005)`, …, `(21996,21997,21998)`, for app/MCP/web respectively. The first
block whose ports are absent from every registered worktree's runtime metadata and
can all bind to loopback TCP is selected; repository-reserved ports are excluded.
Reservations count even with a stopped stack. Invalid reservation files fail closed.
Sockets stay held until the metadata is written, then are released. Another listener can
still race later startup; task-compose's checks and Docker's bind failure handle that
without killing a listener or silently changing ports.

The result includes issue/title, branch, worktree, three ports, external secret path
and file-existence status, plus a `cd` command in human output. No secret file is opened,
created, copied or validated. Provision the external file manually before starting Compose.
Metadata uses the same schema validator as task-compose without its secret-reading step.

On failure after creation, only this invocation's unchanged worktree and branch are
removed, without force. New commits or unexpected working-tree changes are preserved
and named for manual review. Existing branches/worktrees, secrets and Docker resources
are never deleted. A successful primary fast-forward is retained on later failure.
SIGINT/SIGTERM trigger rollback; a hard kill or machine crash can require manual recovery.

## Local configuration

`start-task` writes `.task/runtime.json` in the task worktree (ignored, excluded from builds):

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
excluding the repository's reserved VPN ports. Startup allocates the explicit ports as
described above; there is no separate reservation registry. Metadata survives terminal/agent
restarts and reboot.

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
only this project's volumes; it is destructive. Finishing/removing tasks and cleaning
volumes remain manual. Browser cookies are not isolated by port; use separate browser contexts when
working with multiple task UIs.

## Hooks and checks

Run `make hooks` once from either checkout. Shared stable dispatchers resolve the invoking
worktree and execute its `scripts/pre-commit.sh` / `scripts/pre-push.sh`, preserving stdin
and exit status. Installation recognizes the old repository symlinks, is idempotent,
and refuses custom hooks or configured `core.hooksPath`. Hook logs remain per-worktree;
the heavy-check lock remains in the common Git directory. Concurrent coding/runtime is
supported; existing pre-commit heavy checks still queue. Cloud setup is unchanged and
is not a way to initialize concurrent local worktrees.
