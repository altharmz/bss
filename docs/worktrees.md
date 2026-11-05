# Task workflow: worktrees, slots and Make targets

The shared local workflow for every coding client (Claude, Codex, Kimi). Use **1 active
task = 1 issue = 1 branch = 1 linked Git worktree = 1 worker slot**. A task can have many
sessions and be handed between clients; a slot identifies no client, model or session. The
primary checkout stays clean on `master` and is for administration only (start, finish,
board work). Never switch branches in a task worktree or touch sibling worktrees.

## Normal agent workflow

Every regular operation has a Make target; use it instead of the equivalent raw Git, GitHub
or Docker commands (`make help` lists them). Targets validate task identity, run the hooks
and record telemetry; raw commands skip that. Nothing blocks you from using a plain command
where no target exists (source reading, `git diff`, `git log`, `rg`). Run each target as a
standalone command, with its required variables.

| Step | Command | Where |
|---|---|---|
| Start and claim a task | `make start-task BSS_TASK_OWNER=<client>` | primary |
| Create an approved P0 issue in Ready | `make new-priority-task TITLE="..." BODY="..." [LABELS="a,b"]` (`BODY_FILE=/abs/path` for long text; `ISSUE=<n>` finishes a preserved one) | primary |
| Resume / hand off a session | `make telemetry-collect` | task |
| Install UI dependencies (first time) | `make web-install` | task |
| Format backend and UI | `make format` | task |
| Review task state | `make review-task` | task |
| Show the claimed issue (body, comments) | `make show-task` | task |
| Commit selected files | `make commit MESSAGE="..." FILES='["path/a", "path/b"]'` (or `MESSAGE_FILE=` / `FILES_FILE=`) | task |
| Update from master | `make sync-task` | task |
| Publish the branch | `make push` | task |
| Create or reuse the PR | `make create-pr` | task |
| Check PR and CI readiness | `make check-pr` (`WAIT=600` waits while pending) | task |
| Merge when policy permits | `make merge-task` | task |
| Finish and release the slot | `make finish-task SLOT=<slot>` | primary |
| List / remove leftovers of finished tasks | `make clean-tasks` (list), `make clean-tasks CONFIRM=1` | primary |
| Optional broader validation | `make check` | task |
| Lint / format harness Python | `make scripts-lint` / `make scripts-fmt` | task |

1. **Start.** From clean primary `master`, `make start-task BSS_TASK_OWNER=<client>`. It
   selects the next eligible Ready issue and a free slot, claims `agent:<client>`, moves the
   issue to Doing, creates the branch and worktree and records the session. Use exactly the
   returned issue, branch, slot and worktree; do not inspect the board or claim items first.
   If it fails or finds nothing, stop and report.
2. **Work** in the returned worktree. `make format` skips the UI formatter when the branch has
   no changes under `frontend/` or `spec/`; run `make web-install` once before it formats UI
   files. After another task merged, `make sync-task` merges `master` into the branch.
3. **Commit** with `make commit`. It stages only the listed files and runs the normal
   pre-commit hook, which is the one local verification run (see Hooks and checks). Run
   `make test` (the full suite) before a PR touching billing, migrations or the API.
4. **PR.** `make create-pr` publishes the branch and opens (or reuses) the PR with
   `Fixes #N`. `make check-pr WAIT=600` reports readiness and waits for pending checks.
5. **Merge.** `make merge-task` when ready. A human-gated PR stays blocked until the human
   approves that PR at its exact head in the conversation; then pass
   `HUMAN_APPROVAL=<PR-number>:<40-char-head-SHA>`. New commits need renewed approval.
6. **Finish.** After the merge, issue closure and board Done, immediately run
   `make finish-task SLOT=<slot>` from clean primary `master`. No human gate. It removes the
   ignored output of Make targets (`frontend/dist`, `.shots`, `.e2e`, `src/api/generated`) with the
   worktree and refuses any other ignored file.

Every lifecycle target ends with its next step: a `Next: <command>` line, or a `next_command`
field in the JSON result (commit -> `make create-pr`, create-pr -> `make check-pr WAIT=600`,
check-pr ready -> `make merge-task`, merge-task -> `make finish-task SLOT=<slot>`, finish-task
-> `make start-task`). When a human gate is the only blocker, `check-pr` gives the
ready-to-copy form: `Human approval needed for PR <n> at <sha>; after approval: make
merge-task HUMAN_APPROVAL=<n>:<sha>`.

A commit that touches the harness runs the hook for 1.5 to 3 minutes, longer than a typical
tool call limit. Run `make commit` in the background and read its exit status from the task
itself, never through a pipe (a pipe hides the status, which hid a failed commit once);
`make commit` prints its final status line even when the hook output is long.

Commands for issue comments (`gh issue comment`) are always allowed: add a short note when
something meaningful happens (a decision, a blocker, waiting on the human). Do not log every
command.

Lifecycle targets print one result line (or their JSON result) by default; `VERBOSE=1` adds
the task, modified, staged and untracked context lines (`make review-task` always prints
them). A generic failure names its cause: the exception class, the failing command's first
two arguments and exit status, and for `gh` its sanitized first error line; never other
stderr text, which can carry tokens or URLs.

**When a target fails:** read the error and fix the underlying state; prefer another
documented target; only if no target can represent the correction, take the smallest
non-destructive plain command and report it in your summary. Never bypass human approval or
the hooks, rewrite published history, force-push, hard-reset, force-remove a modified
worktree, prune Docker globally, or modify credentials you did not create.

**At a human gate** report the task, branch, PR, readiness, the policy requiring the human
and the exact next command, then stop. **On completion** report what changed, decisions,
tests and docs, validation results (including whether `make check` ran), the PR and its
status, and whether the slot was released. Deployment is outside this workflow.

Keep the tree unchanged while checks run; batch fixes; do not kill and restart a long check
merely because it is slow. Long text (issue bodies, commit messages, file lists) can be passed
as a file: `BODY_FILE=`, `MESSAGE_FILE=`, `FILES_FILE=`, `PR_BODY_FILE=` (absolute paths).
Editor tools require reading a file before rewriting it.

## Persistent worker slots

Slots live in `<checkout-parent>/<checkout-name>-agents/<slot>/` (`BSS_WORKER_ROOT` selects
another absolute root). The default pool is `worker1`..`worker3`; an empty directory with a
valid name (1-32 lowercase letters, digits, hyphens) adds a slot. `make start-task` inspects
candidates in name order and uses the first safe free one. `SLOT=<name>` restricts selection
(administrative override).

A slot holds `repo/` (the disposable worktree), `active.json` (the authoritative local
reservation, separate from telemetry), `slot.lock`, optional operator `state/`, and
`finish-*.json` checkpoints. Unknown entries, a registered worktree, a symlink or any
uncertain state make a slot unavailable and are preserved. A slot with a reservation whose
task is provably merged, closed and clean is cleaned up by the same checkpointed logic as
`finish-task` and reused; anything else is skipped. Exhaustion lists every candidate's reason.
A common lock in the Git directory serializes startup across callers; each selected slot
stays locked until startup ends.

Failed startup rolls back only what this invocation created, without force; changed
worktrees, new commits, credentials and unacknowledged remote writes are preserved and
reported. After a crash a reservation may need manual review: never delete it to evade an
occupied slot. `make release-slot SLOT=<slot>` (primary) removes only `active.json` after the
worktree was removed manually.

## Task startup details

Requires Git, the GitHub CLI with project write access, and Python 3. The client comes from
`BSS_TASK_OWNER` (a lowercase slug of 1-32 characters; strongly preferred), else from
`CODEX_THREAD_ID` or `CLAUDECODE`. The `agent:<client>` label must already exist. Only a clean
primary `master` is accepted: no staged, tracked or untracked changes, no local commits ahead
of or diverging from `origin/master` (no stash, reset or rebase). Tracked secret paths are
refused.

Selection follows [board.md](board.md): open Ready issues, P0 before P3, then board order.
`blocked`, `needs-human`, any `agent:` label and open GitHub blockers exclude an issue, as do
existing task branches, worktrees, linked branches and open PRs. The helper claims by adding
the owner label, verifies, moves Ready to Doing and verifies again. GitHub has no atomic
claim; rechecks detect races and the common lock serializes this checkout.

The branch is `issue-<n>-<kebab-title>` (at most 80 characters) from the fetched
`origin/master`. Ports come from 21000-21998 in aligned blocks of three (app, MCP, web): the
first block not reserved by any registered worktree and free to bind on loopback. The output
lists issue, branch, worktree, ports, the secret file path and the Doing/claim result.

## Local configuration and runtime

`.task/runtime.json` (ignored) holds only `version`, `task_id` (equal to the branch),
`app_port`, `mcp_port` and `web_port` (distinct, 1024-65535). Development secrets are outside
the worktree: `~/.config/bss/tasks/<task_id>/.env` (or under `$XDG_CONFIG_HOME`), created by
startup with a random `POSTGRES_PASSWORD` and `BSS_SERVICE_SECRET` (the app and the MCP
connector share it; directory mode 0700, file 0600, never overwritten, never copied from the
primary). Optional keys: `POSTGRES_USER`, `POSTGRES_DB`. A file from before issue #174 lacks
`BSS_SERVICE_SECRET`: `make mcp-smoke` then fails with the line to add. Task worktrees must not contain `.env*` files.

Local Compose goes through `./scripts/task-compose` (the Make targets use it); it selects the
worktree's files and secret file and derives the project `bss-<task_id>`, images
`bss-<task_id>-app:dev` / `-web:dev` and the MCP URL. `--check` validates context, `--url
app|mcp|web` prints a URL, `config --quiet` is the only config output (resolved config holds
secrets). Each project owns its containers, network, PostgreSQL data, MCP data and frontend
dependencies; ports bind to loopback. `make down` keeps volumes; `make clean` deletes this
project's volumes. Browser cookies are not isolated by port: use separate browser contexts
for several task UIs.

Usage telemetry is a separate persistent database, described in
[telemetry-usage.md](telemetry-usage.md); lifecycle targets record to it automatically and
only warn when it is down.

## Hooks and checks

`make hooks` (once, from either checkout) installs shared dispatchers that run the invoking
worktree's `scripts/pre-commit.sh` and `scripts/pre-push.sh`. Details of the gates:
[decisions/harness.md](decisions/harness.md). In short:

- **pre-commit** refuses a commit unless the tree is exactly what is committed, then runs
  `make lint`, the smoke tests and the tests the change affects (the full suite when a data
  file, `app/worker/` or a migration changes), `make docs-check`, `web-lint`/`web-test` for
  UI changes, and the harness script tests plus guard cases when `scripts/`, `Makefile` or
  `.claude/` change. Markdown-only commits skip everything. One run at a time per repository.
- **pre-push** refuses bcrypt hashes, `.env*` paths, branch deletions and non-fast-forwards.
- `make format` formats backend and, if `frontend/` or `spec/` changed (or the base is
  unknown), the UI (never stages). `make review-task` prints JSON about
  task identity, slot, runtime, changed files, whitespace, forbidden paths and suspicious
  secret filenames; it reads no secrets and changes nothing.
- `make check` (optional) runs migration preflight, lint, the full backend suite, generated
  docs checks, guard cases, and UI and browser checks unless every change is confined to
  `scripts/`, `docs/`, `.claude/` or the instruction files. Stage failures are collected and
  reported together. `make scripts-lint` prints a hint when a `noqa` comment for the reported
  code sits on a neighbouring line instead of the finding's line.
- `make web-test` runs one timezone, adding `America/New_York` when `frontend/src/` changed.

## Commit and publish a task

`make commit MESSAGE=... FILES=...` takes a JSON array of exact repository-relative file
names (deletions included; never a directory or glob). It validates task identity, protected
and secret paths and the installed hooks, stages only those files, refuses unrelated or
partially staged files (naming them and the `git add -- <files>` that fixes them) and runs a
normal `git commit` with hooks. If the hook fails, the files this run staged are unstaged
again (edits kept; files you had staged, and a merge in progress, are untouched). `DRY_RUN=1` validates
without staging or running hooks. Inherited `GIT_*` variables are refused (the refusal
names them) except the presentation-only `GIT_PAGER`, `GIT_EDITOR` and
`GIT_SEQUENCE_EDITOR`. The command prints where the hook keeps its full output
(`.git/pre-commit-logs/`). A failure leaves HEAD and the working tree as they were.

`make push` publishes `HEAD` to the same-named branch on `origin` (upstream set if needed),
refuses unexpected destinations, remote history outside local ancestry and protected
paths, and runs the pre-push hook. No force, tags or unrelated refs.

`make sync-task` merges current `origin/master` into a clean task without rewriting history
(the merge runs the commit hook). Conflicts and hook failures preserve the merge state:
resolve as source edits and `make commit`.

## Task pull requests and readiness

`make create-pr` pushes through the safe push helper and creates the PR against `master` in
`altharmz/bss`, or reuses a matching open one. Title defaults to the latest commit subject;
`PR_TITLE=` and `PR_BODY_FILE=/absolute/path` override. `Fixes #N` is always added.

`make check-pr` prints JSON readiness: PR, CI state, mergeability, review decision, human
gate status, commits behind base, reason codes and `next_command`. Exit status: 0 ready, 1
failed or invalid, 2 pending, 3 policy blocked (read the `status` field). `WAIT=<seconds>`
(default off, capped at 1800) polls every 20 s and stops as soon as the status is ready,
failed or blocked, or when the time is up (then exit status 2 as without it); it never merges
and never changes state. Ready requires passing checks, equal
local and PR heads, a clean tree, a current base, a nondraft mergeable PR and a clean merge
state. Human gates: a `needs-human` label, or a changed path (or the old name of a
renamed one) among CLAUDE.md, AGENTS.md, `.claude/settings.json`, `.claude/hooks/`, `.codex/`,
`scripts/pre-commit.sh`, `.github/workflows/`: the files that grant permissions, run code or
define policy. A GitHub
approval does not satisfy them; only `HUMAN_APPROVAL=<PR-number>:<full-head-SHA>` does, and
only for the local gate: CI, conflicts, reviews and branch protection still apply. Never infer
approval from earlier sessions or heads.

## Merge and finish

`make merge-task` re-runs the readiness gates, checks the repository allows only squash
merges and runs GitHub's normal merge pinned to the expected head. No admin or auto-merge; an
unconfirmed merge is reported and left for inspection; an already-merged matching head
succeeds again.

`make finish-task SLOT=<slot>` runs from clean primary `master` under the startup and slot
locks. It verifies the merged PR and head, the closed issue with Done board state, a clean
worktree and matching Compose ownership; synchronizes `master`; stops the task's containers;
removes the worktree without force; removes the task's own external `.env` only if
unchanged; then removes what the task created, as disposable development data: its Compose
volumes (exact project label `bss-<task id>`, none in use), its images
`bss-<task id>-app:dev` and `-web:dev` (exact tags, none in use) and its local branch when
the tip equals the merged PR's head (that exact ref is deleted; any other tip is kept). The
telemetry project and volume, base images, shared layers, build cache and other projects are
never touched, and nothing prunes globally. The result lists `removed` and `kept` (with the
reason for each); removals are recorded in the checkpoint as they happen. It closes
telemetry windows and starts the final-tail collector. A `finish-<task>.json` checkpoint in the slot makes partial failures
retryable by rerunning the same command; changed credentials, heads or unknown paths stop a
retry.

`make clean-tasks` (primary) is the safety net for tasks whose finish failed and for leftovers
from before finish removed them. It lists, and with `CONFIRM=1` removes, the volumes, images,
local `issue-*` branches and credential directories (`~/.config/bss/tasks/<task id>`) of
tasks that are finished: the issue is closed and Done, a PR for the branch is merged, and
there is no open PR, registered worktree, slot reservation or running container. Everything
else is listed as kept with its reason; an abandoned branch without a merged PR is reported,
never deleted. It uses the same removal code as `finish-task`. The slot directory and state persist. `make start-task` and `make finish-task` run the
primary checkout's scripts, which are stale until `master` is fast-forwarded. After the
fast-forward, if `scripts/` or the `Makefile` changed, the target releases its locks and runs
again once from the updated scripts (marker `BSS_SCRIPTS_REEXEC`, so it cannot loop), so a fix
merged by a task applies to that task's own finish; all checks run again and the checkpoint
resumes a finish. If primary predates this target, bootstrap with
`make -f /absolute/task/worktree/Makefile finish-task SLOT=<slot>`.
