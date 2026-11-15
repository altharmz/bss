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
`make progress`) are optional. Run `make test` (the full suite, about 11 minutes) before a PR
that touches billing, migrations or the API. Every feature or fix comes with a test that
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
- `make lint`, then the test runs `scripts/hook_selection.py` plans for the staged files
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
  scripts, the compose files, the suite, its runner or the `Makefile` change. It tests the
  human's deploy, not the agents' workflow, and was the longest suite of every harness commit;
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

## Stale primary scripts

The primary checkout runs old scripts until `master` is fast-forwarded (a fix in #140 once
broke the finish of the very task that merged it). `start-task` (after its fast-forward,
before any claim) and `finish-task` (after its first synchronization) call
`reexec_if_scripts_changed`: if `scripts/` or the `Makefile` changed they raise `Reexec`, the
locks unwind, and the updated primary script runs once more with `BSS_SCRIPTS_REEXEC=1`.
The aborted run records no telemetry result of its own; the rerun does. Safety checks are
not skipped: the rerun repeats them from the checkpoint.

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
so cost shapes what runs). Pull requests to `master` and pushes to `master`: `make lint`,
`make docs-check`, `make test-smoke`. Nightly (01:30 UTC) and `workflow_dispatch`: `make lint`,
`make docs-check` and the whole `make test` (including `make runtime-check` and
`make deploy-check`). A PR run is
cancelled by a newer push; runs on `master` never are. The workflow uses a disposable
database password, reads no repository secret (`contents: read`), never touches the VPS, and
pins actions to commit SHAs. The dev image is built with the Actions layer cache. A second job, `harness` (no
Docker, 15 minutes at most), runs `make docs-links` on every pull request and push and
`make runtime-check` and `make deploy-check` on pull requests that change `scripts/`, `.claude/`,
`.codex/` or the `Makefile` (a changed-files step); it is the backstop for the local commit
hook, and the nightly full run covers the harness tests through `make test`. The web
checks (`web-lint`, `web-test`, `web-e2e`) are not in CI. Branch protection is the human's.

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
