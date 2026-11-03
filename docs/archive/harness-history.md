# Harness history

Dated per-issue records moved out of [decisions/harness.md](../decisions/harness.md) on
2026-11-03 (#137). They describe how the harness was built; where they conflict with
harness.md or [worktrees.md](../worktrees.md), those win. Mentions of the Make-only
workflow guard (`workflow_guard.py`), cloud sessions and the issue statistics comments
describe code removed in #137.

## Per-issue records (2026-10-30 to 2026-11-03)

### Slot-only agent concurrency (2026-11-03, #129)

`make start-task` no longer rejects startup when the same `agent:<client>` already owns a
Doing item. Available worker slots are the only local concurrency limit; each task still
requires its own issue, branch, worktree, slot reservation, runtime and credentials.
Issue eligibility, ownership labels, claim verification and slot locking are unchanged.
This supersedes the earlier per-client WIP policy below.

### Python harness lint and formatting (2026-11-02, #124)

Use `make scripts-lint` and `make scripts-fmt` for Python sources below `scripts/`.
The default selection includes changed committed, staged, unstaged and new Python
harness files; deleted paths and unrelated application files are excluded. If the base
cannot be determined, selection conservatively includes all Python harness sources.
`SCRIPT_FILES='["scripts/example.py"]'` selects explicit files. Paths are validated before
container invocation; traversal, redirected sources and shell fragments are refused.
The selection is literal JSON data: Make expressions in `SCRIPT_FILES` are not evaluated
while constructing the child environment.

The pinned development image runs Ruff in a dedicated service with no network, Git,
credentials or whole-checkout mount. Lint uses a read-only scripts mount; formatting
selects its writable twin, which can write only scripts. Both read the repository's Ruff
configuration. Nonzero lint/format statuses propagate through Make. The targets supply
the previously missing Make-owned operation; the workflow guard remains enabled and
direct task-compose lifecycle invocations remain blocked. These checks are explicit for
harness changes, separate from the existing application `make lint` / `make format`.

### Make-owned lifecycle dogfood and enforcement (2026-11-01, #93)

[worktrees.md](../worktrees.md#normal-agent-workflow) is the canonical cross-client
workflow. The commit hook is the normal verification gate; `make check` remains
optional/on-demand. Provider instructions link to the same lifecycle rather than
maintaining separate mutation sequences. The existing full-test requirement for
billing, migrations and API PRs is unchanged.

`scripts/test_workflow_lifecycle.py` runs the public Make targets from startup through
finish against real disposable Git repositories, worktrees, locks and hooks. GitHub,
Docker, language formatters and the telemetry database are simulated. The fixture
uses Codex/worker1 and Claude/worker2 simultaneously, then reuses worker1 for Kimi.
These are client-identity simulations, not claims of live Claude or Kimi participation.
It verifies per-task credentials/ports, sibling preservation, safe squash-head retention,
persistent slot state and SQL event continuity through cleanup. It does not measure
provider performance or fabricate usage/model/session identity.

Dogfood exposed two gaps: finish's Makefile path calculation failed for checkout paths
with spaces, and a task behind a newly merged master had no supported synchronization
command. Finish now passes the source Makefile path through the environment intact,
including the existing bootstrap invocation. `make sync-task` reuses existing safety
checks and remote validation, merging current master through the normal commit hook
without rewriting history. Dirty state, forbidden incoming paths, conflicts and hook
failures are refused or preserved for inspection. Push names sync-task on a stale base.

After lifecycle coverage, the shared local shell guard invokes `workflow_guard.py` to
recognize direct workflow mutations and name the Make target to use. Read-only commands,
source editing, documented board/manual recovery and the existing cloud procedure retain
their policy; a blocked authorized recovery is reported, never disguised. No flag or
environment token marks arbitrary caller commands trusted. Make's validated helpers run
as children, outside shell tool-hook invocation. The guard handles ordinary command
chains, global Git options and shell wrappers, but remains a slip guard rather than an
arbitrary-shell security boundary. Guard and end-to-end regressions are in runtime-check;
existing cloud/hard-constraint cases still run through guard-check.

Live evidence for this slice is Codex startup in worker1 and its resulting PR. Its
instruction/guard edits require human approval before merge under board policy; no
live cross-provider completion or final cleanup is claimed before that gate passes.
The PR readiness gate also protects the workflow guard's helper and Python cases by
exact path, so moving enforcement code outside `.claude/` does not remove human review.

### Automatic task-slot selection (2026-11-01, #109)

`make start-task` is the normal entry point. Startup discovers the default persistent
worker pool and additional named slot directories, selects in stable name order, and
skips unavailable candidates while preserving unknown state. The common lifecycle lock
serializes concurrent callers before both issue and slot selection; per-slot locks
remain held through reservation/worktree creation. Exhaustion reports every candidate.
Recognizable reservations use the existing completion checks and checkpointed cleanup
under those same locks before reuse. The optional `SLOT` override remains administrative;
legacy issue-specific worktrees remain supported but are no longer created by default.
Client markers provide automatic identity; explicit `BSS_TASK_OWNER` takes precedence
and never follows slot identity. Strongly prefer `make start-task BSS_TASK_OWNER=codex`
for Codex. Startup owns board inspection, selection and claim; agents do not enumerate
board statuses or candidate PRs beforehand. Repository edits are authorized without
repeated edit confirmation; secret/design-reference restrictions and human merge gates
remain. See [worktrees.md](../worktrees.md).

### Explicit prioritized task creation (2026-11-01, #112)

`make new-priority-task TITLE=... BODY=... [LABELS=...]` handles a concrete new request
only after explicit human approval for P0 and first board position. Under the existing
repository/GitHub validation, it creates one issue, adds P0, moves it to Ready and
first position, verifies the result and stops (#124). It does not check slot
availability or allocate task resources; selection, claim and setup are exclusively
the independent `make start-task` operation.
Reserved priority, ownership and gate labels are rejected. A created issue is preserved for
inspection after partial failure; callers must not retry by creating a duplicate. Ordinary
backlog selection remains `make start-task`. See [worktrees.md](../worktrees.md).

### Approved board priority administration (2026-11-01, #107)

Priority-label edits and BSS manual board ordering are board administration rather than
Make-owned task transitions. They require explicit human approval for the intended
change; see [board.md](../board.md#changing-priority-with-human-approval). The workflow
guard recognizes only the documented inline `updateProjectV2ItemPosition` mutation for
the BSS project, with one item and optional predecessor. Extra request arguments,
other projects, opaque bodies and combined mutations retain the existing rejection.
Conversation approval is an agent obligation, not a caller-controlled trust token.

### Client-general task ownership (2026-10-31, #100)

`BSS_TASK_OWNER` accepts a validated lowercase client slug rather than only Codex or Claude.
The `agent:<client>` GitHub label supplies board ownership; `agent:kimi` joins the
existing labels. The former per-client WIP limit is superseded by #129 above. Worker
slots remain provider-neutral and may
be reused by different clients over time. Successful startup telemetry records validated
`client` and `slot` metadata independently, with no schema migration or inferred session,
model or usage identity. Codex/Claude detection and the legacy `BSS_TASK_AGENT` alias remain
compatible.

### Task merge and completion (2026-10-31, #91)

`make merge-task` composes existing readiness with the repository's squash-only policy
and GitHub's normal merge command, pinned to the expected head. Human gates require explicit
conversation approval, represented by `HUMAN_APPROVAL=<PR-number>:<full-head-SHA>` (#118).
This input satisfies only the local human gate; all other checks and GitHub protections
remain enforced. New heads require renewed approval. Confirmed merged heads are idempotent; queued/unconfirmed
merges retain state for inspection. No protection bypass or remote branch deletion is used.

`make finish-task SLOT=...` runs from clean primary master for persistent slots, under the
startup and slot locks. A task-specific persistent checkpoint supports retries after repo
removal. Confirmed merged PR/head, closed issue/Done board state and synchronized master
precede destructive steps. Compose ownership fields and network membership must match
before task-only down. Worktrees are removed without force; normal safe local branch
deletion may retain squash heads. Task credentials are explicitly disposable and removed
only with unchanged file identity. Slot state, volumes, images and shared telemetry survive.
See [full contracts and recovery](../worktrees.md#merge-and-finish).

Completion telemetry sets the first task end/outcome and session end times before removing
disposable resources; merge and cleanup attempts/results record bounded status/duration.
Telemetry remains fail-open with no local queue or schema change. Cleanup failure does
not undo confirmed PR completion, and keeps the slot occupied until final checks succeed.

### Verification process optimization (2026-10-31, #95)

Keep all commit-hook checks running: the human declined tree-hash reuse. No check
stamps or hook bypasses are introduced. Batch fixes and keep the tree unchanged while
checking; use focused checks during iteration, optional `make check` when useful, and only rerun
affected stages after subsequent edits. The hook is the final gate.

`make check` now runs migration/schema preflight first (migrating the local task
database before schema tests, so a fresh task can run browser fixtures) and collects stage failures
instead of stopping at the first failure. UI and browser stages are omitted for changes
confined to harness scripts, documentation and agent instructions; unknown paths or
missing base information retain all stages. Change selection includes the task branch
and uncommitted files. Local UI tests run their second timezone only for `frontend/src/`
changes or an unknown base; CI retains both. The default package test command also
retains both. Public stage targets support focused reruns.

Preflight and backend test targets prepare `.testmon/` before starting `oneoff`, so
Docker cannot create its cache directory with unsuitable ownership on a fresh task.
An empty unwritable directory can be recreated; existing cache data is never removed.

`make commit DRY_RUN=1` performs normal context, hook-installation and selection
validation without index/HEAD mutation or hook execution. Telemetry distinguishes
`commit-dry-run` attempts/results from actual commits. Normal commit and push gates
are unchanged. Runtime helper tests run under an outer Make with `GIT_PAGER=cat` to
exercise the nested-Make environment that caused late failures in #89.
See [command details](../worktrees.md#hooks-and-checks).

### Task pull requests and readiness (2026-10-31, #90)

`make create-pr` validates task context, publishes through the existing safe push helper,
and creates or confirms the task's PR against master with `Fixes #N`. `make check-pr`
reports structured readiness across checks, mergeability, review policy, human gates and
base synchronization. Both use explicit repository selectors and refuse conflicting or
incomplete state. Protected paths and `needs-human` labels remain policy blocked without
explicit conversation approval bound to the PR/head (#118); neither
command merges. The optional wait mode is not implemented. See the
[command contracts](../worktrees.md#task-pull-requests-and-readiness).

Creation/readiness attempts and results use fail-open telemetry. Only validated PR number,
allowlisted readiness/CI states, duration and slot enter metadata; descriptions, reviews,
paths and API payloads do not. No telemetry schema change is needed.

### Task commit and push (2026-10-31, #89)

`make commit MESSAGE=... FILES=...` requires a JSON list of exact intended filenames;
`make push` publishes only the validated task branch. Both reuse task quality safety
checks and require the normal shared hooks. Index/worktree state survives failures,
and publication refuses incompatible remote ancestry or unexpected destinations.
See [worktrees.md](../worktrees.md#commit-and-publish-a-task) for the interface and
transitional PR workflow boundary. Attempts and results use existing telemetry events;
successful commit hashes are bounded operational metadata, with no messages or diffs.

The guard permits the exact read-only hook-path query, including its literal Python
argv form and the original hook-rejection diagnostic in lifecycle source. It continues
to reject other hook-path occurrences, configuration mutations/overrides, and hook
bypass flags. Whole interpreter bodies
are never exempted. Regression cases cover both read-only source and actual bypasses.

### Persistent worker slots (2026-10-30, #85)

The canonical cross-client local workflow is [worktrees.md](../worktrees.md).
The original explicit `make start-task SLOT=worker1` interface prepares the issue branch at a stable
`<checkout-name>-agents/worker1/repo` path, with optional absolute `BSS_WORKER_ROOT`.
Keep runtime identity issue-specific; path reuse must not reuse Compose state or credentials.
Slot occupancy is a locked local reservation, separate from observational telemetry;
unknown state fails closed. Explicit `release-slot` only acknowledges safe prior worktree
removal and preserves the persistent directory/state. Automatic selection (#109 above)
supersedes the original omitted-SLOT legacy startup path. Board ownership labels remain
intact, while #129 above supersedes the former WIP policy. Explicit BSS_TASK_OWNER
separates policy ownership from client detection. Slots have no provider labels and
task/session cardinality remains many-to-one.
Successful startup adds the validated slot to workflow event metadata using the existing
schema, with no telemetry migration or inferred model/usage/provider values.

### Persistent task telemetry (2026-10-30, #82)

Usage ingestion and reports were added in #92; see the
[export contract, pricing provenance and report limitations](../telemetry-usage.md).
Migration 002 adds nullable provenance and a unique import identity without changing
existing rows. `telemetry-ingest` accepts only explicit, nonoverlapping machine usage
deltas; `telemetry-report` reports known data and leaves unavailable metrics null.
The #124 collector projects only allowlisted machine usage metadata from explicitly bound
client sources; conversation content is never retained or reported. No price scraping
or model/token estimation is performed.

Telemetry is observational and **fail-open**: a failed write produces a bounded warning
on stderr without changing the workflow's exit status. Writes have a three-second timeout;
they never start infrastructure, queue raw JSON files, or persist usage in a worktree.
Collection retries replay immutable machine events; final-tail capture uses a bounded
two-minute host process from surviving primary sources after task completion. Coverage
stays partial when its final counter does not arrive. Bindings, counter snapshots and
the two issue-comment checkpoints live in PostgreSQL migration 003, separately from
sample/pricing completeness. See [telemetry-usage.md](../telemetry-usage.md).
Use `make telemetry-up` once to start the shared local database and migrate it;
`telemetry-health`, `telemetry-migrate`, `telemetry-down` and `telemetry-test` manage/check it.
Management failures fail the management target. Normal task targets remain usable offline.

`compose.telemetry.yaml` is a separate fixed `bss-telemetry` project with named volume
`bss-telemetry-data`. It has no network or published ports; the repository writer reaches
PostgreSQL over its container-local socket through Docker exec. The fixed bootstrap
password is not a credential boundary: Docker access already grants database access.
No task dotenv file or inherited Docker/Compose selectors enter the telemetry commands.
Task `down`, `clean`, worktree removal and branch deletion cannot select this project or
volume. `telemetry-down` and `finish-task` preserve the volume.
There is deliberately no volume deletion target.
This is local harness infrastructure, never application or production infrastructure.

The schema lives in numbered SQL migrations under `scripts/telemetry/`, independently
of application Alembic. Migrations are transactional and serialized by an advisory lock;
the initial migration is idempotent and records version 1 in `schema_versions`. Keep
applied migrations immutable; add numbered, repeat-safe migrations for future changes.
`telemetry-test` checks the real schema, including null usage, multiple sessions per task
and bounds, in a transaction that is rolled back.

`tasks` identifies the issue branch (including start/end/outcome); `agent_sessions` has
many sessions per task, provider, nullable model/external identity and start/end;
`workflow_events` records task, optional session, operation, timestamp, numeric result,
nullable duration and bounded JSONB metadata; `usage_samples` stores nullable token
counts and exact numeric estimated cost, currency and bounded pricing metadata.
Automatic collection upserts task/session identity and appends an event after validated
task Compose operations, including those used by normal Make targets. `start-task`
records successful preparation. The `--url` and `--check` helpers do not create events.
No operation arguments, subprocess output, prompt/response, code, diffs, secret values
or arbitrary environment data are collected. Only allowlisted operation names and
validated branch/session identities enter SQL. Startup metadata contains independently
validated client and worker-slot identities; other task lifecycle metadata may contain a
validated worker slot. Successful commit/push results may also contain the commit hash.
PR lifecycle results may contain a positive PR number and allowlisted readiness/CI states.
Other event metadata remains empty.

Codex's UUID thread identifier, Claude's process registry and Kimi's session-lock registry
are reliable session sources. Kimi's registry match validates the lock version, ancestor
PID/token and hostname, then verifies its main wire source; ambiguous locks fail closed
rather than selecting the newest session. Runtimes may provide an explicit UUID in
`BSS_TELEMETRY_SESSION_ID`; without a reliable identity events have no session. A session
key includes task and provider, so one thread cannot accidentally associate events with a
different task. Unknown model, usage, pricing and cost stay NULL; there is no inference
from conversation content or fabricated zero usage. End timestamps/outcome remain NULL
until `finish-task` confirms task completion;
`down` only stops runtime and does not imply task completion.
Startup duration is NULL because startup currently does not measure it. Historical
rows are retained indefinitely; no task cleanup cascades or automatic deletion exist.

