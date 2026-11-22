# Harness: workflow, hooks, guard, CI

Principle: every regular operation is a `make` target; agents are told to use them
([worktrees.md](../worktrees.md)) and little is blocked. The agent's work ends at the merge
to `master`; deploying is the human's (`make deploy`, [infra.md](infra.md)). Per-issue
history is in `git log` and the closed issues.

## Human-approved task selection

`make start-task BSS_TASK_OWNER=<client>` selects by priority and board order by default.
The human can instead approve a specific issue in the current conversation; pass
`ISSUE=<n> HUMAN_APPROVAL=<n>` to select it. A missing or mismatched marker fails before
repository setup. The marker records the caller's assertion, not proof of conversation
approval, so agents must never invent approval. The override changes selection only:
the issue must pass every normal eligibility and claim-race check, with no fallback to
another issue and no priority or board-order edits.

## Documentation: no hand-written counts

Markdown must not state undated current-state counts or ratios of tools, operations,
tests, items, APIs or endpoints. Link to the generated source or name the command that
prints the current inventory instead. Allowed: generated `docs/api/` and `docs/db/`,
explicitly dated history, counts in code that a test checks (such as
`bss_mcp/server.py`'s `INSTRUCTIONS`), and `docs/archive/` (history, left untouched).
A committed snapshot goes stale as features ship. This rule is reviewed manually;
no automated regex check is added because dates, versions and money cause false positives.

## Development cycle and gates

One local test run per cycle: develop -> `make commit` (the pre-commit hook is the run) ->
push -> PR. Commit and push need no approval. Extra runs (`make check`, `make web-e2e`,
`make progress`) are optional. Run `make test` (the full suite but the tests marked
`migration_history` or `slow`, [below](#tests-left-out-of-the-default-run-2026-11-22-424);
`make test-all` runs those too) before a PR that touches billing, migrations or the API. Every feature or fix comes with a test that
fails without it (a CLAUDE.md rule, not enforced by a hook).

**Pre-commit** (`scripts/pre-commit.sh`, run by the shared dispatcher `make hooks` installs
in the invoking worktree):

- refuses the primary checkout, an unstaged change to a tracked file, and untracked files
  under `app/`, `tests/`, `frontend/`, `spec/` (a commit once passed while missing two files
  that existed only on disk);
- a Markdown-only commit (outside `spec/` and `scripts/docs/`) skips every check;
- takes a repository-wide `flock` (`pre-commit.lock` in the common git directory), so two
  sessions never run two suites at once (that took 12.5 minutes instead of 5 and broke the
  timing-sensitive worker tests); a waiter checks the tree again, 60 minutes at most
  (`PRE_COMMIT_LOCK_WAIT`). Every heavy test target takes the same lock through
  `scripts/with-test-lock` when run by hand (#348: a UI run beside another worktree's
  backend suite timed out 12 tests): the backend pytest targets, `runtime-check`,
  `deploy-check`, `web-test-once`, `web-test-timezone`, `web-e2e`, `web-shots` and
  `progress`; aggregates such as `make check` lock stage by stage, and light targets (lint,
  format, docs, builds, lifecycle) never wait. The hook exports `BSS_TEST_LOCK_HELD=1`, so
  the targets it runs pass straight through (a second open of the lock would deadlock);
  the wrapper waits up to `BSS_TEST_LOCK_WAIT` seconds (3600), then fails without running.
  `scripts/test_test_lock.py` refuses a heavy recipe line without `$(LOCKED)`;
- every choice below is made from the selected paths (`scripts/hook_selection.py paths`):
  the staged paths, or on a merge commit the task's own side only
  ([below](#tests-in-the-hook-smoke-plus-affected));
- no backend test for a commit no backend test can depend on (the harness, the docs, the
  UI, CI), only the suites that depend on the change, and no second smoke run on the same
  backend content ([proportional runs](#tests-proportional-to-the-change-2026-11-15-369));
- `make lint`, then the test runs `scripts/hook_selection.py` plans for the selected paths
  ([below](#tests-in-the-hook-smoke-plus-affected)): `make test-smoke` + `make test-changed`,
  plus `make test-files` for the tests that read a staged data file (`frontend/src`,
  `docs/progress/scope.yaml`, `scripts/ui_mcp_parity.py`); or the whole backend suite,
  recorded (`make test-recorded`), for `spec/`, dependency pins, `Dockerfile`, compose files,
  `app/worker/` or `alembic/`;
- `make docs-check` (about 7 s) when `app/`, `alembic/`, `spec/`, `scripts/docs/` or
  `tests/docs_collect*` change: a stale `docs/api` or `docs/db` is refused;
- `make docs-links` when any Markdown file is staged: every relative link and `#anchor`
  must resolve, and the `make` targets and `scripts/` files named in code in the
  agent-facing docs must exist (`scripts/docs_links.py`; it runs before the Markdown-only
  exit, so documentation-only commits are checked too);
- `make runtime-check` (the harness script tests: every `scripts/test_*.py` except
  `test_telemetry_database.py`, which needs the running telemetry database, and the deploy
  suite), only when `scripts/`, `Makefile`, `.claude/` or `.codex/` change. The guard cases
  are one of its suites (`scripts/test_guard.py`). The suites are found by name because a
  hand-kept list in the Makefile once left `test_smoke_retry.py` out of every run;
- `make deploy-check` (`scripts/test_deploy_rollback.py`), only when the deploy or smoke
  scripts, the compose files, the suite, its runner or the `deployed` recipe of the
  `Makefile` change. It tests the
  human's deploy, not the agents' workflow, and was the longest suite of every harness commit;
- `make hook-cases` (the shell cases of the hook's lock and plan and of the billing status
  check) when `scripts/` changes;
- neither of the two suites runs again on content it already passed on
  ([below](#tests-in-the-hook-smoke-plus-affected));
- `make web-lint` and `make web-test` when `frontend/` or `spec/` change;
- the full output of each run is in `.git/pre-commit-logs/` (last 10).

**Pre-push** (`scripts/pre-push.sh`) scans only the commits being pushed: it refuses an added
bcrypt hash (unless the line carries `pre-push: allow-dummy-hash`), an added or changed
`.env*` path (except `.env.example`), a branch deletion and a non-fast-forward. It names the
commit and file, never the value.

**Script test durations** (stated here only). `scripts/run_suites.py` runs the suites in
parallel and each suite in parts: a suite's tests are dealt out over up to as many
processes as there are jobs (one per CPU, 16 at most), each started as
`python test_x.py Class.test ...`, so the longest suite no longer sets the wall time. The
tests of a suite must therefore not depend on each other. Measured on 2026-11-11 (12 CPUs):
`make runtime-check` 68 s and `make deploy-check` 45 s. Before (2026-11-10): 151 s for both
in one target, set by the deploy suite (135 s), and 3 min 2 s when the suites ran one after
another. With 8 jobs the parts gave no gain (135 s against 125 s whole, single runs), so the
default follows the CPU count.

`make test` takes about 11 minutes ([measured](#test-speed-2026-11-11-319)). Timing-based tests (`test_worker_heartbeat.py`, the thread
tests in `test_order_concurrency.py` and `test_billing_db.py`) can fail under full load: rerun
once before suspecting a regression.

## The guard

`.claude/hooks/guard.py` (PreToolUse on Bash; Codex runs the same file with `--codex`) blocks
only slips that cannot be undone: reading `.env*` files, `docker compose config` without
`--quiet`, destructive docker cleanup (system, volume, network, builder), force, delete,
mirror and prune pushes, and skipping the git hooks (`--no-verify`, `commit -n`, overriding
`core.hooksPath`). Cases: `.claude/hooks/guard-cases.txt` and `.codex/guard-cases.txt`, run by
`scripts/test_guard.py`, a `make runtime-check` suite.

One implementation serves both clients (#317). Until then the guard was a shell script that
started Python to strip the command and `grep` for each rule, and `.codex/guard.sh` carried a
second, weaker copy of the stripping for the Codex-only rule: it ignored every heredoc, so
`bash <<EOF` with a bare `git push` inside passed. Now the rules and the stripping exist
once, the Codex rule sees shell heredocs and `sh -c` scripts, and a payload Codex cannot
read is blocked (Claude's passes, as before).

It is a pattern matcher, not a security boundary. It matches the executable part of the
command: quoted prose (quoted text containing whitespace), data heredocs and the like are
ignored, so an issue body, a commit message or a heredoc that merely *mentions* a blocked
pattern passes; the script given to `sh -c` / `bash -c`, a heredoc fed to a shell and quoted
single words (`".env"`, `"--force"`) are still matched. Long text can also come from a
file: `BODY_FILE=` for `make new-priority-task`, `MESSAGE_FILE=` and `FILES_FILE=` for
`make commit`, `PR_BODY_FILE=` for `make create-pr`. The workflow itself is
guidance, not enforcement: the Make-owned workflow guard and the VPS/VPN, host-Node and
deploy-record checks were removed in #137.

`settings.json` allows `make` and read-only Git/GitHub commands, and denies reading or
editing `.env*` and the task secret directory (`~/.config/bss/`, where the development
secrets are: the guard only stops shell readers such as `cat`, not the Read tool), and
editing `design-ref/` (the read-only design handoff) and `.deployed` (written only by
`scripts/deploy.sh`). The PostToolUse
hook `format.sh` runs ruff (Python) or prettier (frontend) on every edited file inside the
task's containers; it strips imports that are not used yet. Application code goes to the
`tools` service and `scripts/` to `scripts-tools-fmt`; a Python file that no service mounts
(`.claude/hooks/`) passes silently, where the hook used to report a failure on every such
edit (`scripts/test_format_hook.py`). `.codex/patch-guard.sh` applies the same file denies
to Codex's `apply_patch` (cases: `.codex/patch-guard-cases.txt`, run by
`scripts/test_guard.py` with the shell cases).

The second PreToolUse hook, `agent-telemetry.py`, gives usage telemetry the subagent
identity ([telemetry-usage.md](../telemetry-usage.md#collecting-usage)). It rewrites a
subagent's `make X` into `make BSS_TELEMETRY_AGENT=<id> X` through `updatedInput`, without
a permission decision. An environment prefix would break the `Bash(make:*)` allow rule
(only a fixed list of variables is stripped for matching); a make variable after `make`
keeps it, and make exports command-line variables to recipes. Quoted text and heredoc
bodies are never touched; the main thread (no `agent_id`) is never rewritten.

## GitHub board automation

The BSS project's own workflows (auto-add, item closed, pull request merged, ...) add
items and set Done, normally within seconds. They are best-effort: during a GitHub
incident on 2026-11-03 they did not run (a new issue stayed off the board; a closed
issue stayed in Doing), see [#138](https://github.com/altharmz/bss/issues/138). The
helpers therefore do not depend on them: `make new-priority-task` waits up to 20 s
(`BSS_BOARD_ADD_WAIT`) for the issue to appear, then adds it itself and sets Ready and
first position; `ISSUE=<n>` finishes a preserved issue without creating another. After a
confirmed merge and closed issue, `make finish-task` waits up to 60 s
(`BSS_BOARD_DONE_WAIT`) for Done, then sets Done itself. GraphQL calls are retried once
(`BSS_GH_RETRY_PAUSE`, 2 s) and a failure prints a bounded one-line error and a pointer to
githubstatus.com. Manual `gh project` commands are recovery only. After the 2026-11-03
incident the automation was observed back to about 2 seconds (#138 set to Done 2 s after
closing) and the fallbacks were not needed afterwards; they stay for incidents.

## Board targets

Board administration has Make targets (#308), so the docs name no raw `gh project` or
`gh api graphql` commands: `make board` (the open items by status in board order),
`make add-task` (a new issue in Inbox or Ready, never P0: that stays
`make new-priority-task`), `make move-task` (to Inbox, Ready or Waiting, Waiting with its
`Waiting on:` comment; Waiting to Doing only for an owned issue), `make set-priority` and
`make reorder-task`. Doing stays owned by `make start-task` and Done by closing the issue
or `make finish-task`. Priority and order changes need `HUMAN_APPROVAL=<issue>`, the same
marker as `make start-task ISSUE=`: it records the agent's assertion that the human
approved, not proof, and the script cannot verify the conversation. Every change is
confirmed by reading the board again. The targets work from any checkout and record no
telemetry: they belong to no task. The entry point is `scripts/board_admin.py`; it reuses
`task_board.py` (where `INBOX` and `WAITING` joined the other status options) and
`task_repo.py` (where `body_from_file` moved from `start_task.py`), keeping those two
libraries free of command-line code.

## Lifecycle output and waiting

Lifecycle targets print one result (a line or the JSON) with `next_command`/`Next:` as the
last step; the task, modified, staged and untracked context lines need `VERBOSE=1`
(`make review-task` always prints them). Generic failures print a bounded cause from
`task_runtime.diagnostic` (exception class, first two command arguments, exit status, the
sanitized first `gh` error line), never other stderr text. `make check-pr WAIT=<s>` is the
only waiting mode: it polls read-only, so it cannot merge or change state. A failed
pre-commit hook unstages what `make commit` staged, so an edit afterwards cannot trip the
partial-staging refusal.

## Task leftovers are disposable

Task volumes and images are development data that nothing needs after the merge (a database
volume holds demo data and the image is rebuilt in minutes), and they piled up: on
2026-11-03, 38 tasks still had volumes (95), 69 images, 40 local branches and 18 credential
directories, on a host shared with other projects. `make finish-task` therefore removes them
(`scripts/task_cleanup.py`, `remove_leftovers`): volumes by the exact Compose project label
`bss-<task id>` that no container uses, the exact tags `bss-<task id>-app:dev` / `-web:dev`,
and the local branch only when its tip equals the merged PR's head (`git update-ref -d` with
that value, so a moved branch is refused). Never touched: the `bss-telemetry` project and
volume, base images and shared layers, the build cache, other projects, anything without the
exact label or tag, the slot directory and its state. There is still no global prune; the
code runs as a script child, so the guard keeps blocking manual `docker volume`, `network`
and `system` cleanup by agents. `make clean-tasks` (list by default, `CONFIRM=1` to remove)
applies the same function to earlier leftovers of finished tasks and reports abandoned work
as kept.

## Finish-task removes the task's load-test stack (2026-11-15, #311)

A task that ran `make loadtest-*` could not be finished while any `lt-*` container existed:
the Compose ownership check accepted only the dev services created from `compose.yaml` and
`compose.override.yaml`, and the plain `down` leaves the load-test containers as orphans.
`scripts/task_finish.py` now also accepts the seven `lt-*` services of
`compose.loadtest.yaml`, but only with exactly that file added to the same worktree's
two (project, working directory and file list still match exactly; a dev service with the
load-test file list is refused too). When any is present, the removal runs
`scripts/task-compose --loadtest --profile '*' down`, which removes both stacks, one-off
containers included, and keeps volumes for `remove_leftovers` (which already deleted
`lt_pgdata`). No direct `docker rm`. The load-test services join the same `lab` network, so
the network check needed no change. Anything else, such as another project, worktree or
compose file, still stops the finish with the runtime preserved.

## Help output is grouped

Every help comment in the `Makefile` starts with a group tag (`target: ## [group] text`,
#307). `make help` shows the daily groups (task, stack, test, ui, docs, mcp);
`make help GROUP=<name>` shows one of the others (loadtest, telemetry, ops) or `all`, and
`make help-ops` shows the human-only deploy and VPS targets. The grouping only changes the
listing and restricts nothing. `scripts/test_make_help.py` fails when a target has no valid
tag, so a new target cannot be left out of the list.

## Script modules

`start_task.py` is an entry point, not a library. What other lifecycle scripts need from it
lives in two modules (#317): `task_repo.py` (running Git and `gh` without inherited `GIT_*`
selectors and with sanitized errors, the primary checkout's checks, worktrees, the external
secret path) and `task_board.py` (the board read in board order, item status, adding an
issue). `task_finish.py` and `task_cleanup.py` imported `start_task` for these, while
`start_task` imported `task_finish` back for slot recovery.

Not merged, on purpose: `task_runtime.git` (text, the caller's directory),
`task_quality.query` (bytes, for NUL-separated paths), `task_pr.gh` (JSON reads with a retry)
and `task_repo.run`. They differ in error type and return value, every caller depends on
those, and one helper with flags for each difference would be harder to read than four
short functions.

## One entry point for lifecycle targets

The lifecycle targets (start, commit, PR, merge, finish, quality and board targets, #309) all
run `scripts/task.py <target>`. Its table `TARGETS` names each target's script, the arguments
it gets and the make variables it accepts. The Makefile passes only the names of the variables
given on the make command line (`BSS_MAKE_VARS`), never their values; `task.py` refuses a name
the target does not know (`VERBOSE` and `BSS_*` are always allowed), so `make commit
MESAGE=...` fails instead of committing without the intended message. Values keep travelling
as exported environment variables, because pasting make variables into a recipe breaks on
quotes and lets text become shell. Variables from the process environment are not checked:
they are not in `BSS_MAKE_VARS`. `finish-task` runs the `task.py` next to its Makefile
(`HARNESS_DIR`), so `make -f /abs/worktree/Makefile finish-task` keeps working without the
former `python -c` one-liner. The scripts stay runnable directly, which `task_runtime.rerun`
relies on. `FILES` accepts a plain space-separated list besides a JSON array; `FILES_FILE` one
name per line or a JSON array.

## Stale primary scripts

The primary checkout runs old scripts until `master` is fast-forwarded (a fix in #140 once
broke the finish of the very task that merged it). `start-task` (after its fast-forward,
before any claim) and `finish-task` (after its first synchronization) call
`reexec_if_scripts_changed`: if `scripts/` or the `Makefile` changed they raise `Reexec`, the
locks unwind, and the updated primary script runs once more with `BSS_SCRIPTS_REEXEC=1`.
The aborted run records no telemetry result of its own; the rerun does. Safety checks are
not skipped: the rerun repeats them from the checkpoint.

## Approval last, master's gates, the full-suite record (2026-11-18, #395)

Delivering #197 took two human approvals, an extra full-suite run and an extra hook run:

- **Approval is requested last.** `check-pr` reported `human-approval-required` with
  `base-behind`; `next_command` was a generic "resolve the listed reasons", the agent asked
  for approval, the sync then changed the head and the human approved again. Now
  `next_command` names every other fix first (sync, commit, push, `make test`, waiting for CI)
  and gives the approval form only when the approval reasons are all that is left. Such a PR
  is `pending`, not `blocked`, so `WAIT` keeps polling through pending CI. A review or a
  `BLOCKED` merge state is still a block. `WAIT` no longer polls while only a local step is
  due: it never comes by itself.
- **`check-pr` and `merge-task` run `master`'s harness** (`task_runtime.master_harness`).
  The #197 branch predated #391 and blocked on a behind-but-unshared base that `master`
  accepted. Both targets fetch `master`; when its `scripts/` changed since the fork and the
  branch changed none of its own, they extract `master`'s `scripts/` (and
  `compose.telemetry.yaml`, for telemetry) into a temporary directory and run its `task.py`
  in the worktree, once (`BSS_SCRIPTS_REEXEC`). A branch that changes the harness itself
  keeps its copy: never an older one than the task's own; `make sync-task` brings master's in.
- **The full-suite record** (`scripts/full_suite.py`). A passing `make test` on a clean tree
  writes `{head, tree, passed_at}` to `.testmon/full-suite-passed.json`; a failing run, a
  dirty tree or a tree that changed during the run writes nothing. `check-pr` reports
  `full_suite`. "Required" reuses the hook's selection: a PR with a path a backend test may
  depend on (`hook_selection.backend`), so docs, harness, UI and CI PRs are `not-required`.
  That is wider than "billing, migrations or the API", but nearly every `app/` path is one of
  those, and a second list would drift. The record is `passed` at the head's tree, or when
  every commit since the recorded head is a merge whose changes share no path with the PR
  (`shared_paths`, the #390 rule); else `stale`. `stale` and `missing` are the reason
  `full-suite-required`, `next_command` `make test`.
- **The flaky worker test.** `wait_until_blocked` in `tests/test_worker_signals.py` polled a
  fixed 10 s; a hook run beside another slot's tests took longer. It now waits up to 60 s and
  returns as soon as the worker blocks.

## Tests in the hook: smoke plus affected

The full suite is too long for every commit (about 11 minutes locally, 19 in the nightly CI run), so
the hook runs `make test-smoke` (the `smoke`-marked tests, about 1.5 minutes, and the shell
scripts' own cases) and `make test-changed` (`pytest -n auto --testmon`:
[pytest-testmon](https://github.com/tarpas/pytest-testmon), pinned in `requirements-dev.txt`),
which runs tests whose executed code changed since their last recorded run, plus new and
previously failed ones (about 20 s when nothing is affected).

**A new worktree starts with a record** (#319). testmon has nothing to compare against in a
fresh worktree, so the first commit of every task ran the whole suite in `make test-changed`
(2534 tests, 10 to 15 minutes), even for a change that touches no application code.
`make finish-task` now saves the merged task's record (a consistent SQLite copy that passes
its integrity check) as `bss-testmon/testmondata` in the common Git directory, and
`make start-task` copies it into the new worktree (`scripts/task_testmon.py`; the result
shows `test_selection`). The record is keyed on the content of the code each test ran, not
on a commit, so one that is older than `master` or comes from another branch makes testmon
run more tests, never fewer. Both steps are best effort: a missing or damaged record, or a
worktree that does not ignore `.testmon/`, leaves the old behaviour (the whole suite).
Measured 2026-11-11: a fresh worktree seeded from another task's record selected no tests
in 28 s.

**What testmon cannot see** (#344, `scripts/hook_selection.py`). Until 2026-11-14 any staged
data file sent the commit to the full `make test`: a commit of UI sources, docs and
`scope.yaml` ran 2,638 backend tests for 11 min 46 s, plus 116 s of harness and deploy suites
it had not touched, and a retry after its one failure paid all of it again. Now:

- **Data files few tests read** are mapped to those test files (`DATA`): the hook adds
  `make test-files TESTS=...` to smoke and affected. The map cannot fall behind:
  `tests/conftest.py` installs an audit hook, and a test file that opens or lists a mapped
  path without being listed fails at the read with the line to add
  (`tests/test_hook_selection.py`). Reads during a module's import count for that module; a
  read in a subprocess is not seen. A change to the map itself runs every mapped test.
- **What any test may depend on** (`FULL`: the specs, which the app and the response
  validator read; the pins, image and compose files; `app/worker/`; `alembic/`) still runs
  every backend test, as `make test-recorded`: `pytest -n auto --testmon-noselect`, which
  runs everything and records it. When pytest ran every test (exit 0 or 1), the target
  writes the digest of the `FULL` files in the index to `.testmon/full-suite`. A later
  commit whose `FULL` files have exactly that content - the retry after a failure - gets the
  usual plan, and testmon selects the tests that failed and the ones the fix affects. Any
  other content of those files, or a missing stamp, is the whole suite again.
- `make test-recorded` has no `runtime-check` or `deploy-check`: the hook runs those by its
  own path rules, once. `make test` (by hand, nightly) is unchanged.

**A merge commit chooses from the task's side** (2026-11-15, #366). `make sync-task` merges
`origin/master` into the task branch, and the hook of that merge commit used to choose its
checks from `git diff --cached`, which for a merge is everything coming in. In #309 a merge
that brought in a migration and `app/worker/` code ran `make test-recorded`,
`make runtime-check` and `make deploy-check`, over 10 minutes, on a task that had touched
none of it; that code had already passed the hook and CI on `master`. Now
`scripts/hook_selection.py paths` gives the paths every choice is made from: the staged
paths, or, while `MERGE_HEAD` exists, the paths whose index content differs from
`MERGE_HEAD` (`git diff --cached --name-only MERGE_HEAD`). Those are the task's own changes
since it left `master` and the conflicts resolved by hand; a file that only came in is left
out. The plan, the Markdown-only exit, `docs-links`, `docs-check`, the two script suites and
the web checks all use these paths. `make lint`, `make test-smoke` and `make test-changed`
still run on a merge (unless the task's side is Markdown only), and testmon selects the
tests the merged code affects, so a clash between the two sides is still caught. The whole
suite runs on a merge only when the task's own side touched a `FULL` path. The log lists
the real staged files and a line `selection: merge with <commit>, N incoming paths left out`.

**Fewer whole-suite runs and syncs for a merge** (2026-11-16, #390). Delivering #198 ran
the whole suite three times, about 10 minutes each: the feature commit and two sync merges.
Its own side kept `spec/internal/bss-internal.oas.yaml` (a `FULL` path), and the stamp
check compared the digest of *every* `FULL` file, so a migration or spec coming in from
`master` meant a full run again. The second sync merged cleanly and was due only because
`master` moved during the first one's hook. Two rules now:

- **A merge does not repeat the whole suite for the task's own `FULL` files**
  (`merge_full_tested` in `scripts/hook_selection.py`). It is skipped when every `FULL` path
  the merge selects still has `HEAD`'s content and the stamp equals the digest of `HEAD`'s
  `FULL` files (`tree_digest`): the whole suite already ran on them, and the incoming ones
  passed on `master`. The plan is then the usual smoke plus affected. A `FULL` file both
  sides changed, or resolved by hand, differs from `HEAD`: that combination never ran, so the
  whole suite does. In #198 both syncs were that case (`spec/internal` changed on both
  sides), so the rule would not have saved them; it saves the common case of a task with its
  own migration or spec while `master` brings in others.
- **Behind `master` with nothing shared needs no sync** (`shared_paths` in
  `scripts/task_git.py`). `make push`/`create-pr` and `check-pr`/`merge-task` accept a task
  that lacks `master` commits when no path is changed on both sides since the fork, and no
  migration is added on each side (two migrations on one parent are two Alembic heads,
  whatever their names). Renames count on both names. Anything shared keeps `base-behind`,
  and its `next_command` is `make sync-task, make push, then make check-pr WAIT=600`.
  `check-pr` reads the incoming side from GitHub's compare (`head...master`); a list at
  GitHub's 300-file cap counts as shared. GitHub still refuses a conflict
  (`mergeable`), and a branch rule that wants an up-to-date branch shows as
  `merge-state-behind`.

The cost: the squash commit of a behind PR has a tree no local hook ran on. What came in
passed the hook and CI on `master`, the two sides share no file, and the nightly full run on
`master` is the backstop for a semantic clash between unrelated files.

**A script suite is not repeated on the same content** (2026-11-15, #366).
`make runtime-check` (about 85 s) ran four times in #309. After either script suite passes
in the hook, the hook writes a digest of the index entries (mode, blob, path) of that
suite's trigger paths to `.testmon/runtime-check` or `.testmon/deploy-check`. Before a run
it compares the digest with the stamp and, when they are equal, prints
`runtime-check: passed on this content already; skipped`. A retry after a lint failure or
a failure of the other suite then costs no second run. This skips nothing the path rules
would not skip: a suite is only considered when a trigger path is selected, and the stamp
only matches when every trigger path has exactly the content a passing run saw. A failed
run writes no stamp. The trigger paths of both suites are in one place (`SUITES` in
`scripts/hook_selection.py`); `scripts/test_ci_workflow.py` keeps the CI harness job on the
same `runtime-check` paths. The other half is a working rule
([worktrees.md](../worktrees.md#hooks-and-checks)): while developing, run only the suites
of the change by hand; the hook is the one full local run.

### Tests proportional to the change (2026-11-15, #369)

Measured in #366: a harness-only commit paid `make test-smoke` (95 s), `make test-changed`
and all 32 harness suites (80 s) on every commit, and its sync merge reran 2,737 backend
tests (12 min 6 s) because `master` had changed `tests/conftest.py`. None of that could
fail because of the task. The rules now:

- **No backend test for what no backend test depends on.** `NO_BACKEND` in
  `scripts/hook_selection.py` lists what a backend test cannot depend on: `docs/`,
  `scripts/`, `frontend/`, `design-ref/`, `.claude/`, `.codex/`, `.github/`, the `Makefile`
  and the root Markdown files. A commit whose selected paths are all there plans no
  `make test-smoke` and no `make test-changed`; a `DATA` path among them still gets its
  readers, alone (a UI commit runs three test files, not the smoke subset). Every other
  path, known or not, is backend. The rule cannot fall behind the tests: the audit hook
  of `tests/conftest.py` refuses a read of a `NO_BACKEND` path by a test unless `DATA`
  maps the test to it (`tests/test_hook_selection.py`). The map file itself counts as
  backend code (`tests/conftest.py` imports it).
- **The smoke run has a stamp** (`.testmon/test-smoke`: the content of every backend path
  in the index), like the script suites. A retry on the same backend content prints
  `test-smoke: passed on this content already; skipped`. `make test-changed` needs none:
  testmon selects nothing when nothing changed.
- **The shell cases left `make test-smoke`.** `test-billing-status-check.sh` and
  `test-pre-commit-lock.sh` are `make hook-cases`: in the hook when `scripts/` changes
  (with a stamp), in the CI harness job, and in `make test`.
- **Only the harness suites that depend on the change.** `hook_selection.py suites` reads
  the sources under `scripts/`: a suite depends on every file its source names (a file
  name, an imported module, a quoted module name such as the ones fixtures copy in a loop)
  and on what those scripts name in turn. A changed path runs the suites that reach its
  file name; the `Makefile`, `scripts/run_suites.py`, `scripts/hook_selection.py`, and any
  path no suite names run them all. The hook then runs
  `make runtime-check RUNTIME_SUITES="..."`; such a partial run writes no stamp. The reach
  is deliberately wide: the lifecycle scripts copy each other into fixtures, so a change
  to one of them still runs 22 of the 32 suites, while a change to a suite alone, the
  guard, the docs generator or the workflow runs 1 to 6. A wrong guess is caught by the CI
  harness job, which runs every suite on a pull request, and by the nightly run.
  `.github/workflows/` and `compose.prod.yaml` became trigger paths (hook and CI): a suite
  reads each (`test_ci_workflow.py`, `test_compose_prod.py`), and a change to only that
  file ran the suite nowhere before the nightly run.
- **Of the `Makefile`, the deploy suite follows one recipe.** `scripts/test_deploy_rollback.py`
  reads only the `deployed` recipe, so `make deploy-check` (41 s) runs on a `Makefile`
  change only when that recipe differs from the commit's parent (on a merge, from the
  commit being merged in); its stamp covers the recipe, not the file.
- **A sync adopts the saved test-selection record** when the merge brings backend code in
  (`scripts/task_testmon.py adopt`, called by `make sync-task`). The worktree's own record
  does not know the incoming code, so testmon reran every test it affects. The saved
  record is the one the last merged task left, so against it the merged tree differs by
  the task's own change: exactly the tests a clash could break. A worktree notes the
  record it was given (`.testmon/seeded`), and `make finish-task` keeps the stored record
  when the task's is still that one: a task that ran no backend test must not put an older
  record over a newer one.

**The slowest backend tests** (2026-11-15, one `make test` run with `--durations=60`,
12 workers, 2,743 tests in 12 min 36 s, another task's suite running beside it). Four
tests take over 100 s each and 16 take over 20 s; the next 40 take 10 to 20 s, most of
them migration round trips. Nothing was changed here: each of the four is a real probe or
a seeding run, and shortening one is its own piece of work (#373, below).

| Test | Time |
| --- | --- |
| `test_tmf_gaps.py::test_the_gap_list_is_the_recorded_one[nested]` | 141 s |
| `test_loadtest_seed.py::test_parallel_processes_and_fast_commit_give_the_rows_of_one_process` | 130 s |
| `test_seed.py::test_seed_builds_the_demo_and_is_idempotent` | 109 s |
| `test_loadtest_seed.py::test_a_run_killed_mid_day_and_rerun_equals_an_uninterrupted_run` | 107 s |
| `test_usage_load.py` (two tests) | 42 s, 41 s |
| `test_prepaid_offering_changes.py::test_0044_combined_items_block_downgrade` | 37 s |
| `test_nonsuperuser_migration.py::test_migrations_run_as_non_superuser_database_owner` | 36 s |
| `test_migrations.py::test_alembic_downgrade_then_upgrade_roundtrip` | 32 s |

**Shortened (#373, 2026-11-15).** Re-measured on the idle 12-CPU host. Most of the 100+ s
above was the other suite beside it: alone, the four took 44 to 56 s. What changed, with
no assertion dropped:

- *nested gap probe*: it seeds a 30-day history (`tests/tmf_gaps.py`, `SEED_DAYS`), not the
  demo's 120. The probe reads which attributes resources carry, not how many there are;
  from 25 days on (the first bills) the lines equal the 120-day ones, and the test
  asserts one daily run per seeded day. Splitting the probe in parts, as the issue
  proposed, would have seeded once per part: more total work, not less.
- *load-test seed*: the 20 operators get the smallest scrypt cost, as `tests/conftest.py`
  gives test users (a stored hash names its own cost); hashing and their first sign-ins at
  the production cost were half of each run. The killed-run test asserts the cost it got
  and runs 3 days instead of 4, which still leaves a whole day after the killed one.
- *`test_seed`*: unchanged. Its time is the demo itself (120 daily runs over 9 accounts
  ~16 s, prepaid passes ~5 s, API calls ~11 s, profiled), and every part is asserted
  (`START_DAYS` daily runs, events from day 100). Shortening it means a cheaper
  `bill_account` (about 13 ms per account and day) in the app, not a test change.

| Test | Alone, before | Alone, after | `make test`, after |
| --- | --- | --- | --- |
| `test_tmf_gaps[nested]` | 56 s | 31 s | 50 s |
| `test_loadtest_seed` parallel processes | 50 s | 35 s | 42 s |
| `test_loadtest_seed` killed and rerun | 48 s | 23 s | 63 s (still 4 days) |
| `test_seed` | 44 s | 37 s | 74 s |

The full backend suite (`pytest -n auto`, 12 workers, idle host) took 8 min 31 s for
2,738 tests, under the 10-minute target. `test_seed` stays over the 60 s target under
load.

testmon limits: selection is off under `-m` and `-k` (so smoke and affected are separate runs);
the first run on a checkout without a seeded record, or after deleting `.testmon/`, is the
whole suite; it follows
Python code only, hence the full-suite triggers above; comment-only edits select nothing. Its
data is `.testmon/.testmondata`, per worktree and gitignored. If the selection looks stale,
delete `.testmon/`.

The smoke marker (`pyproject.toml`; `tests/test_smoke_marker.py` keeps every area marked)
covers billing core, the TMF contract (validator, enum lock, operation ids, spec provenance,
health, order states, catalog, conventions), migrations on an empty database, and the MCP
connector. A new test is not smoke by default: mark one only where it guards what the gate
exists for, and keep the set near two minutes of summed time.

### Tests left out of the default run (2026-11-22, #424)

Two marks (`pyproject.toml`) name tests that cost much more than they protect in an
ordinary run. `make test` and `make test-recorded` leave them out (`-m "not
(migration_history or slow)"`, the Makefile's `MARKS`):

- **`migration_history`**: a test that replays migrations from an empty database
  (`temp_db_url`) to check the data handling of one deployed migration. Deployed
  migrations are immutable, so such a test fails only when `alembic/env.py`, the Postgres
  image, the pins or a shared helper changes.
- **`slow`**: the six slowest tests (`test_seed`, the two `test_loadtest_seed` runs,
  `test_tmf_gaps[nested]`, two `test_usage_load` generator tests). Their time is the
  application's own work (#373), and they depend only on the seed, load-test, gap-probe and
  usage-generator code.

They still run:

- in the nightly CI run: its job sets `FULL=true`, which the Makefile maps to `MARKS=all`
  (`scripts/test_ci_workflow.py` checks both), so the workflow needed no change;
- in the commit hook, when a path they depend on changes (`scripts/hook_selection.py`,
  `MARKED`): `alembic/`, the pins, the `Dockerfile` and the compose files bring
  `migration_history` into the whole-suite run they already call for (`make test-recorded
  MARKS=migration_history`); `tests/seed.py`, `loadtest/`, `tests/tmf_gaps.py`,
  `tests/tmf-gaps.txt` and `app/integrations/network/usage_load.py` run the `slow` tests
  (`make test-marked MARKS=slow`, before `make test-changed` and recorded for testmon, so it
  does not repeat them). `make test-changed` has no `-m`, so testmon still runs a marked test
  whose Python code changed, and a run with `-m` keeps the selection records of the tests it
  leaves out (testmon reads the names before `-m` deselects);
- by hand: `make test-all` (everything), `make test-marked MARKS=...`.

**The rule for a migration's tests.** A test that takes `temp_db_url` carries
`migration_history` unless `KEPT_REPLAYS` in `tests/conftest.py` lists it; a collection check
refuses anything else, and a smoke test that carries either mark (`tests/test_smoke_marker.py`
tests the check). Kept: the upgrade to head on an empty database, the downgrade-and-upgrade
round trip of every migration, the drill's refusal of an unknown mode (no replay), and an
app check against an older schema (`test_worker_round_trips.py`). A new migration's data
test is written into `KEPT_REPLAYS` and stays in the default run until the migration is
deployed (`make deployed` names the newest deployed one); the next task that touches
migrations after that moves it to `migration_history`. On 2026-11-22 the newest deployed
migration was 0088, and no data test of 0088 or 0089 replays migrations.

**Measured** (2026-11-22, the 12-CPU workstation with no other test run, the backend
pytest part of `make test` back to back, `-p no:randomly`; CPU is the host's busy time
over the run, summed is the sum of the per-test `--durations=0` phases):

| Run | Tests | Wall | Host CPU | Summed test time |
| --- | --- | --- | --- | --- |
| Pair 1, default first | 2,983 / 3,037 | 12 min 25 s / 12 min 25 s | - | - |
| Pair 2, everything first | 2,983 / 3,037 | 11 min 46 s / 13 min 58 s | 7,501 s / 8,922 s | 6,851 s / 8,831 s |

(default / everything). The saving is the work: about 1,400 CPU seconds (16 %) and
2,000 s of summed test time (22 %), the six slow tests alone 478 s of it. The wall time
moves less and not every time: twelve workers keep the host saturated either way, and
the tail is set by whichever long test is left last (`test_seed`'s production-gates run
and the bill PDF timing test now, 70 to 80 s under load), so pair 1 ended level.

## Test layout (2026-11-22, #423)

**No test module imports another.** Until #423 the backend tests had 380 imports from
other test files (`tests.test_prepaid_renewal_producer` alone 34 times). Editing one test
file made testmon re-run every importer, and renaming or deleting a test broke unrelated
files. A helper, constant or fixture that more than one module uses now lives in
`tests/builders/`, not in a test module. `tests/test_test_imports.py` (smoke) fails on any
`tests.test_*`, `from tests import test_*` or relative import of a test module, in
subpackages too.

- The first step moved the shared names mechanically: one `tests/builders/<module>.py` per
  former source module, holding the imported names and the definitions they depend on.
  The source module imports them back. Builder modules do not import fixtures: pytest
  resolves a fixture from the requesting test's module, so the test module imports every
  fixture it needs (with `# noqa: F401`, and `# ruff: noqa: F811` at the top where a test
  parameter shadows the import).
- Collected tests before and after: 3,037 in 185 files.
- The domain subpackages, the prepaid docstrings and one helper for the performance guards
  are #440 (one PR closes one issue, so #423 was split).

## Test speed (2026-11-11, #319)

Measured on the 12-CPU workstation. The host is shared with other agents' test runs, so
every comparison below was run back to back with the host otherwise quiet; a single number
taken under load is not comparable (the same smoke run took 84 to 219 s).

| What | Before | After | Change |
|---|---|---|---|
| First commit in a new worktree, `make test-changed` | all 2534 tests, 10 to 15 min | no tests selected, 28 s | the seeded record ([above](#tests-in-the-hook-smoke-plus-affected)) |
| Smoke subset (`make test-smoke`, the pytest part) | 89 and 95 s | 84 and 89 s | the kept template database ([infra.md](infra.md#conventions)) |
| UI unit tests (`make web-test`, one timezone) | 123 and 125 s | 103 and 104 s | the two largest test files split |
| Full backend suite (`make test`, the pytest part) | 10 min 42 s | not measured again | the docs said 7 minutes; the suite grew |

**2026-11-14 (#344).** The same workstation, under load from two other tasks, so only the
per-test phases below are comparable (summed `--durations` of `tests/test_api_parties.py`,
38 tests, serial, alternated twice):

| What | Before | After | Change |
|---|---|---|---|
| A commit of UI sources and `scope.yaml`, backend tests | all 2,638 tests, 11 min 46 s, again on a retry | smoke + affected + 4 mapped test files | the data-file map |
| Harness and deploy suites in the hook's full-suite run | 116 s every time | only when their files are staged | `make test-recorded` |
| Fixture setup per `api` test | about 307 ms | about 205 ms | the test user's stored hash names the smallest scrypt cost (the check was 110 ms); one kept connection creates and drops the databases (30 ms a connection) |
| Fixture teardown per `api` test | about 57 ms | about 37 ms | the kept connection |

Where a test's fixed cost goes after that (measured): the clone of the template about
210 ms under load (`CREATE DATABASE ... TEMPLATE`, 14 MB; `STRATEGY FILE_COPY` was slower,
368 ms), the drop 60 ms, two connections of 30 ms. Not done: cloning ahead and dropping in a
background thread. It only moves that work off the test, and the full suite already keeps
every CPU busy (707 s x 12 workers / 2,638 tests = 3.2 worker-seconds a test, of which the
fixtures are about a tenth), so the gain would be a few percent for a thread under every
test's database.

**The UI split.** vitest runs files side by side but one file's tests in order, and
`SubscriberCard.test.tsx` (168 tests, 121 s) alone set the wall time while all files
together were 518 s of work. It is now five files and `Catalog.test.tsx` (88 tests, 71 s)
three, with the fixtures they share in `frontend/src/test/subscriberCardKit.ts` and
`catalogKit.ts`. Every test moved verbatim: 920 tests before and after. The price is CPU:
each file sets up its own environment, so the summed work rose to about 800 s and the load
during the run doubles. A further split would cost more than it gains.

**A bug the per-worker migration hid.** `alembic/env.py` configures logging from
`alembic.ini`, which replaces the root handlers. While every worker migrated its own
template, every test process lost the app's redacting log handler at start. With one build
per run only the building worker would lose it, so the build now puts the logging back
(`tests/conftest.py`), and two MCP audit tests failed at once: the redaction read the
principal of a static token, `mcp-token:<name>`, as a `token:` secret pair and logged
`mcp-token:[redacted]` in the audit line and the request line's operator. That was the
behaviour in production; the tests had only passed because the handler was gone.
`app/logsetup.py` now leaves that principal alone (`tests/test_logging.py`).

**Suites never inherit a hook's Git selectors.** Inside a hook Git exports `GIT_DIR` and
`GIT_INDEX_FILE`, and a git command that inherits them acts on the hook's repository,
whatever directory it names. On 2026-11-11 a new suite ran `git init <temporary directory>`
from the commit hook: Git re-initialised the real repository instead and set `core.bare`,
so every command in the primary checkout failed until `git config core.bare false`
(nothing else changed). `scripts/run_suites.py` now removes the selectors from every
suite's environment, `scripts/task_testmon.py` from its own git call, and
`scripts/test_task_testmon.py` has the case.

**Nor the variables of the make that runs them** (2026-11-15, #366). With
`make runtime-check RUNTIME_SUITES="..."`, make passed the variable on in `MAKEFLAGS`, and
every `make` a suite started in its fixture received it; the lifecycle targets refuse
unknown variables, so `scripts/test_task_quality.py` failed whenever it was chosen that
way. `scripts/run_suites.py` removes make's control variables as well
(`scripts/test_run_suites.py`).

**Measured and left alone:**

- *Scheduler and workers.* `--dist worksteal` gave 91 and 92 s on the smoke subset against
  84 and 89 s without it. Fewer workers were slower (`-n 8` 155 s, `-n 6` 144 s, single runs
  under some load), in line with the earlier decision to keep `-n auto`.
- *How a test database is made.* Cloning the template and dropping the clone takes
  0.12 to 0.16 s; `STRATEGY file_copy` is no faster than the default (60 cycles: 9.6 s
  against 7.2 and 9.8 s). A typical API test spends about half its 0.6 s in this setup, but
  the alternative, one database per worker that is emptied between tests, gives up the
  isolation the suite relies on and is a decision of its own.
- *The slowest tests* (`test_loadtest_seed.py` twice, `test_seed.py`,
  `test_tmf_gaps.py[nested]`: 29 to 46 s each alone, about 100 s each in a full run). A
  profile shows the time is the application's own work, 35,000 to 45,000 SQL statements per
  test for the demo seed and the gap probe; fixtures, client start and stop and pool
  handling are under 0.3 s. The generator tests are already at 12 subscribers over 3 to 4
  days and each compares two complete runs, which is what they prove. Nothing was changed:
  a faster version of any of them would have to do less.
- *Bytecode.* Sources are mounted read-only, so every pytest process compiles about 2000
  modules (1.6 s). A cache volume would save that once per worker; not worth a new volume.
- *The browser e2e run* could not be timed: `make seed` fails on a default local stack
  ([#321](https://github.com/altharmz/bss/issues/321)).

## CI

`.github/workflows/ci.yml`, GitHub Actions on `ubuntu-latest` (private repository, Free plan,
so cost shapes what runs). Pull requests to `master`: `make lint`,
`make docs-check`, `make test-smoke`. Nightly (01:30 UTC) and `workflow_dispatch`: `make lint`,
`make docs-check` and the whole `make test` (including `make runtime-check` and
`make deploy-check`). A PR run is
cancelled by a newer push; a nightly or manual run never is. The workflow uses a disposable
database password, reads no repository secret (`contents: read`), never touches the VPS, and
pins actions to commit SHAs. The dev image is built with the Actions layer cache. A second job, `harness` (no
Docker, 15 minutes at most), runs `make docs-links` on every run and
`make runtime-check`, `make deploy-check` and `make hook-cases` on pull requests that change
`scripts/`, `.claude/`, `.codex/`, `.github/workflows/`, the `Makefile` or `compose.prod.yaml`
(a changed-files step); it is the backstop for the local commit
hook, and the nightly full run covers the harness tests through `make test`. The web
checks (`web-lint`, `web-test`, `web-e2e`) are not in CI. Branch protection is the human's.

**No run on a push to `master`** (2026-11-15, #366). `make merge-task` merges only a green
pull request that is not behind `master` (`base-behind`, `scripts/test_task_finish.py`), and
the repository squash-merges, so the commit that lands on `master` has exactly the tree the
PR's run tested. Since #390 a PR that is behind but shares no path with what it lacks also
merges (see *Fewer whole-suite runs and syncs for a merge*); the nightly run covers that tree. The push run tested it a second time: 84 of 185 runs in 30 days, on a Free
plan of 2,000 minutes a month of which roughly 1,000 to 1,400 were used. The Actions layer
cache for the dev image is now written on `master` by the nightly run; a PR run reads its
own branch's cache and `master`'s. Moving the test gate into CI or running the full suite
on pull requests does not fit the budget: the local hook stays the gate.

**No Docker steps for a pull request that cannot affect them** (2026-11-15, #369). The
first step of `check` asks `scripts/hook_selection.py ci-check` about the changed paths:
backend paths (the hook's rule), the `DATA` paths, `scripts/docs/` and the workflows call
for the job. For any other pull request (harness, docs, UI) every later step is skipped
and the job ends green in seconds, so `make check-pr` needs no change. It is a step and not
a job of its own, because every job is billed as a whole minute: a separate job would have
cost a minute on every run to save one or two on some. Scheduled and manual runs always
run everything.

**A failed nightly run opens an issue** (2026-11-15, #366). Without the push run, the
nightly run is the only one on `master`, and a red one was found only by looking. The job
`nightly-report` runs after a scheduled run in which `check` or `harness` failed: it opens
`Nightly CI failed: <date>` (labels `bug`, `P1`) with the run's URL, the jobs that did not
succeed and the commits since the last successful scheduled run, or comments on the open
issue with that title if there is one. It is the only job with a write permission
(`issues: write`, plus `actions: read` and `contents: read` for the run list and the compare
request), checks out nothing, runs no repository code and uses only the run's own token. It
writes to GitHub issues only; the issue reaches the board's Inbox like any new issue.
`scripts/test_ci_workflow.py` holds the triggers and the permissions.

The supply-chain scan (`.github/workflows/security.yml`, the `Security` workflow) is an
infrastructure decision: [infra.md](infra.md#supply-chain-scanning-2026-11-08).

## Codex

`AGENTS.md` points Codex at CLAUDE.md. `.codex/hooks.json` runs the shared guard for Bash
and `.codex/patch-guard.sh` for `apply_patch`; both fail closed outside the repository.
`~/.codex/rules/default.rules` (outside the repository) allows `make` targets, and the
tracked `.codex/rules/commit.rules` records the permission for `make commit`. Codex matches
a rule only against a plain command, so a `make` call with a redirect or pipe prompts.
Commits also write the primary repository's `.git`, so the executor's writable roots must
include the primary checkout and the worker root.

Decisions from the #59 session review (#144, #157):

- **Bare `git add/commit/push` is blocked for Codex** by the guard's `--codex` rule (cases
  in `.codex/guard-cases.txt`, [the guard](#the-guard)). Claude's run of the guard still
  allows them; the Make targets are the supported path for both.
- **`.codex/rules/default.rules` is tracked** and installed by copying; it allows `make`, issue
  comments and `gh pr checks` only. The earlier user-level rules allowed bare Git writes and
  carried one-off entries.
- **Long targets** (commit hook 2 to 12 minutes, sync-task, test) run in a background or
  persistent session; a killed call is checked with `make review-task` before a retry.
- **Body files live in `/tmp`.** `finish-task` allows only known ignored output (`.task/runtime.json`,
  build and test output of Make targets) and preserves the worktree on anything else.
- **Subagents** work in the task worktree and are not polled in a loop. Codex records a
  subagent thread as its own session file that also holds its parent's `session_meta`; the
  telemetry parser identifies the session by the first one.
