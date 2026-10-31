# Local task worktrees

This is the shared local lifecycle for every coding client. Use **1 active task =
1 issue = 1 branch = 1 linked Git worktree = 1 worker slot**. A task can have many
sessions and be intentionally handed between clients/providers; a slot does not
identify a provider, client, model or session. The primary checkout is administration-only
and stays clean on `master`. Run `make start-task SLOT=worker1` there
(`JSON=1` for one JSON result on stdout). It fetches/prunes `origin`, fast-forwards
`master`, and prepares an `issue-<number>-<lowercase-kebab-slug>` branch from that
fetched `origin/master` at `<checkout-parent>/<checkout-name>-agents/worker1/repo`.
Start the agent in the returned directory. Existing task-specific worktrees remain
supported; omitting SLOT retains the legacy `<checkout-name>-worktrees/<branch>` path.
Do not switch branches in that worktree or mutate sibling worktrees.

## Persistent worker slots

`SLOT=worker1` and `BSS_SLOT=worker1` select the same slot; the Make argument takes
precedence. Names are 1–32 lowercase ASCII letters, digits or hyphens, beginning with
a letter (`worker1`, `qa1`, `frontend2`). Paths, dots, whitespace and shell syntax fail.
`BSS_WORKER_ROOT` can select an absolute root instead of the default sibling
`<checkout-name>-agents`; symlink components and overlap with registered worktrees fail.
Values enter Python through the environment, never as shell command fragments.

The slot directory persists. `repo/` is the disposable task worktree, `state/` may hold
operator-managed persistent state, `slot.lock` is the persistent advisory-lock inode,
and `active.json` is the authoritative local task reservation, not telemetry. Startup
requires both repo and reservation to be absent and no registered worktree to overlap.
It never resets, switches or adopts an occupied checkout. The common Git startup lock
and per-slot lock serialize startup; different tasks retain distinct branches, ports,
Compose projects, images, data volumes and external credentials. The slot name never
enters runtime identity. Slot information is included in human/JSON startup output and
the startup telemetry event's bounded metadata. Telemetry is historical, fail-open,
and does not declare occupancy; local reservations remain authoritative when it is down.

On failed startup, the existing safe Git rollback runs first. Only this invocation's
unchanged reservation is removed, and only when repo is absent. Changed worktrees or
reservations are preserved and reported. Slot directories, locks and state are retained.
A crash can leave a reservation requiring manual review; do not delete it to bypass
an occupied or uncertain checkout.

After completing a task, stop its runtime with `make down` in its repo. Worktree removal
remains manual: review and safely remove the task Git worktree without force. From clean
primary master, run `make release-slot SLOT=worker1` to acknowledge that removal. It
refuses an existing path (including dangling symlinks) or registered worktree, locks the
slot, validates the reservation and removes only `active.json`. It preserves all state,
credentials, runtime volumes and telemetry. The next startup recreates repo on a fresh
task branch. There is no automatic task completion or runtime/volume cleanup target.

## Task preparation

Git, GitHub CLI with repository/project write access, and host Python 3 are required.
Agent identity is detected from `CODEX_THREAD_ID` or `CLAUDECODE`; otherwise set
`BSS_TASK_OWNER=codex` or `BSS_TASK_OWNER=claude` to select an existing board ownership
identity. This explicit policy identity takes precedence over detection and the legacy
`BSS_TASK_AGENT` alias. Another client can use the same lifecycle under an explicitly
chosen existing owner; this does not claim it is Codex/Claude in telemetry. There are
no per-slot labels and the existing per-owner WIP limit still applies. Unknown owners
fail closed; this change does not introduce additional board ownership labels.
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

The helper checks the agent's Doing WIP limit, rechecks the selected item's eligibility,
adds its existing ownership label, verifies the claim, moves Ready to Doing, then verifies
again before reporting success. It creates no issue, label definition, PR or remote branch.
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
and credential existence/creation status, agent and Doing status, plus a `cd` command in
human output. Missing credentials are created exclusively with a fresh random 256-bit
development-only `POSTGRES_PASSWORD`, directory mode 0700 and file mode 0600.
Existing regular task credentials are preserved and validated by task-compose's context
check. Symlink paths and multiply linked secret files are refused. No primary secret is
opened or used as a source. Generated values never appear in output.

On failure after creation, only this invocation's unchanged worktree and branch are
removed, without force. New commits or unexpected working-tree changes are preserved
and named for manual review. Existing branches/worktrees, secrets and Docker resources
are never deleted. Newly generated credentials are also retained and their path reported
on failure, since another process may already have adopted them. An acknowledged remote
claim/status is compensated only while its expected labels, status and open state still
match. Unacknowledged writes or concurrent changes are preserved and reported for review.
GitHub has no conditional label/status mutation: rechecks detect observed races, but
cannot provide a cross-clone atomic claim. The common Git lock serializes this checkout.
A successful primary fast-forward is retained on later failure.
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

Development secrets live **outside** the worktree:
`~/.config/bss/tasks/<task_id>/.env`, or, when explicitly set,
`$XDG_CONFIG_HOME/bss/tasks/<task_id>/.env` (XDG_CONFIG_HOME must be absolute and external).
`make start-task` creates a missing file; an existing directory must have mode 700
for creation. Existing files are never overwritten. Never copy `.env`, `.env.prod`
or `.env.smoke` from the primary checkout.
Task worktrees must not contain these files. Existing primary secret files stay untouched.

The external file needs a fresh development-only `POSTGRES_PASSWORD`. Optional keys are
`POSTGRES_USER` and `POSTGRES_DB` (both default to `app`), `BSS_SERVICE_SECRET` (a fresh
development-only credential shared by app and MCP), and `MCP_USERS` (local OAuth hashes;
empty disables OAuth sign-in). No production or smoke credentials are needed.

## Runtime

Harness metrics use a separate persistent PostgreSQL service: `make telemetry-up`
starts/migrates it, `make telemetry-health` checks it, `make telemetry-test` tests its
schema and `make telemetry-down` stops it without removing data. Normal lifecycle
operations record metrics automatically and warn without failing when storage is down.
The shared `bss-telemetry-data` volume is independent of all task runtimes and worktrees;
task cleanup must never remove it. See [telemetry policy and schema](decisions/harness.md#persistent-task-telemetry-2026-10-30-82).

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

From the task worktree, use these repository-owned quality commands:

- `make format` validates local task safety and runs the existing backend `fmt` and
  frontend `web-fmt` sequentially. It stops on formatter failure; frontend dependencies
  must already be installed with `make web-install`. Whitespace errors are allowed here
  so formatting can repair them. Formatters may update files but never stage them.
- `make check` validates task safety and runs the existing comprehensive local check:
  lint, full backend tests (including runtime-helper and migration tests), generated
  docs checks, frontend lint/type checks, unit tests, and browser smoke checks, using
  the CI Hypothesis profile, plus the existing guard regression cases. It stops on
  failure. The browser check builds and seeds this task's runtime; docs checks can
  regenerate documentation. Screenshot capture
  (`web-shots`) remains an explicit visual-review tool, not a correctness assertion.
- `make review-task` reports JSON lines with issue/task identity, slot (`null` for legacy
  worktrees), runtime metadata, and modified, staged and untracked filenames. It checks
  both staged and unstaged whitespace, primary master cleanliness, slot reservations,
  forbidden paths, force-added artifacts matching Git ignore rules, and suspicious
  local secret filenames (including ignored files). It reads no secret contents and
  never stages, commits, pushes or cleans files.
  Unsafe state returns nonzero and stays available for inspection. Existing read-only
  design references may remain tracked, but changes to them are refused.

All three require a linked task checkout with matching branch/runtime identity. Slot
checkouts require a matching `active.json`; legacy task worktrees remain supported.
No command contacts GitHub or changes secrets. Validated attempts emit one fail-open
telemetry event with result, duration and task/session/slot identity; invalid contexts
fail before telemetry because they cannot safely establish an identity. Child runtime
commands retain their own existing telemetry. No filenames, diffs or output enter events.
These commands reuse existing checks; pre-commit's change-sensitive test selection and
pre-push's outgoing-commit secret checks remain the normal commit/push gates.

Run `make hooks` once from either checkout. Shared stable dispatchers resolve the invoking
worktree and execute its `scripts/pre-commit.sh` / `scripts/pre-push.sh`, preserving stdin
and exit status. Installation recognizes the old repository symlinks, is idempotent,
and refuses custom hooks or configured `core.hooksPath`. Hook logs remain per-worktree;
the heavy-check lock remains in the common Git directory. Concurrent coding/runtime is
supported; existing pre-commit heavy checks still queue. Cloud setup is unchanged and
is not a way to initialize concurrent local worktrees.
