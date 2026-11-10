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
`make progress`) are optional. Run `make test` (the full suite, about 7 minutes) before a PR
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
  timing-sensitive worker tests); a waiter checks the tree again, 30 minutes at most;
- `make lint`, then `make test-smoke` + `make test-changed` ([below](#tests-in-the-hook-smoke-plus-affected)),
  or the full `make test` when a data file the tests read changes (`spec/`, `frontend/src`,
  `docs/progress/scope.yaml`, `scripts/ui_mcp_parity.py`, dependency pins, `Dockerfile`,
  compose files), `app/worker/` or `alembic/`;
- `make docs-check` (about 7 s) when `app/`, `alembic/`, `spec/`, `scripts/docs/` or
  `tests/docs_collect*` change: a stale `docs/api` or `docs/db` is refused;
- `make docs-links` when any Markdown file is staged: every relative link and `#anchor`
  must resolve, and the `make` targets and `scripts/` files named in code in the
  agent-facing docs must exist (`scripts/docs_links.py`; it runs before the Markdown-only
  exit, so documentation-only commits are checked too);
- `make runtime-check` (the harness script tests: every `scripts/test_*.py` except
  `test_telemetry_database.py`, which needs the running telemetry database; run in parallel
  by `scripts/run_suites.py`) and the guard cases, only when `scripts/`, `Makefile`,
  `.claude/` or `.codex/` change (the guard cases only for `.claude/` and `.codex/`). This
  is the one place that states the duration: 151 s on 2026-11-10, set by the longest suite
  (`test_deploy_rollback.py`, 135 s), so a commit that touches the harness adds about
  2.5 minutes to the hook. The suites are found by name because a hand-kept list in the
  Makefile once left `test_smoke_retry.py` out of every run;
- `make web-lint` and `make web-test` when `frontend/` or `spec/` change;
- the full output of each run is in `.git/pre-commit-logs/` (last 10).

**Pre-push** (`scripts/pre-push.sh`) scans only the commits being pushed: it refuses an added
bcrypt hash (unless the line carries `pre-push: allow-dummy-hash`), an added or changed
`.env*` path (except `.env.example`), a branch deletion and a non-fast-forward. It names the
commit and file, never the value.

`make test` takes about 7 minutes. Timing-based tests (`test_worker_heartbeat.py`, the thread
tests in `test_order_concurrency.py` and `test_billing_db.py`) can fail under full load: rerun
once before suspecting a regression.

## The guard

`.claude/hooks/guard.sh` (PreToolUse on Bash; Codex runs it through `.codex/guard.sh`) blocks
only slips that cannot be undone: reading `.env*` files, `docker compose config` without
`--quiet`, destructive docker cleanup (system, volume, network, builder), force, delete,
mirror and prune pushes, and skipping the git hooks (`--no-verify`, `commit -n`, overriding
`core.hooksPath`). Cases: `.claude/hooks/guard-cases.txt`, run by `.claude/hooks/test-guard.sh`.

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
`.codex/test-guard.sh` with the Codex shell cases).

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

## Stale primary scripts

The primary checkout runs old scripts until `master` is fast-forwarded (a fix in #140 once
broke the finish of the very task that merged it). `start-task` (after its fast-forward,
before any claim) and `finish-task` (after its first synchronization) call
`reexec_if_scripts_changed`: if `scripts/` or the `Makefile` changed they raise `Reexec`, the
locks unwind, and the updated primary script runs once more with `BSS_SCRIPTS_REEXEC=1`.
The aborted run records no telemetry result of its own; the rerun does. Safety checks are
not skipped: the rerun repeats them from the checkpoint.

## Tests in the hook: smoke plus affected

The full suite is too long for every commit (about 7 minutes locally, 30 on a CI runner), so
the hook runs `make test-smoke` (the `smoke`-marked tests, about 1.5 minutes, and the shell
scripts' own cases) and `make test-changed` (`pytest -n auto --testmon`:
[pytest-testmon](https://github.com/tarpas/pytest-testmon), pinned in `requirements-dev.txt`),
which runs tests whose executed code changed since their last recorded run, plus new and
previously failed ones (about 20 s when nothing is affected).

testmon limits: selection is off under `-m` and `-k` (so smoke and affected are separate runs);
the first run on a checkout, or after deleting `.testmon/`, is the whole suite; it follows
Python code only, hence the full-suite triggers above; comment-only edits select nothing. Its
data is `.testmon/.testmondata`, per worktree and gitignored. If the selection looks stale,
delete `.testmon/`.

The smoke marker (`pyproject.toml`; `tests/test_smoke_marker.py` keeps every area marked)
covers billing core, the TMF contract (validator, enum lock, operation ids, spec provenance,
health, order states, catalog, conventions), migrations on an empty database, and the MCP
connector. A new test is not smoke by default: mark one only where it guards what the gate
exists for, and keep the set near two minutes of summed time.

## CI

`.github/workflows/ci.yml`, GitHub Actions on `ubuntu-latest` (private repository, Free plan,
so cost shapes what runs). Pull requests to `master` and pushes to `master`: `make lint`,
`make docs-check`, `make test-smoke`. Nightly (01:30 UTC) and `workflow_dispatch`: `make lint`,
`make docs-check` and the whole `make test` (including `make runtime-check`). A PR run is
cancelled by a newer push; runs on `master` never are. The workflow uses a disposable
database password, reads no repository secret (`contents: read`), never touches the VPS, and
pins actions to commit SHAs. The dev image is built with the Actions layer cache. A second job, `harness` (no
Docker, 15 minutes at most), runs `make docs-links` on every pull request and push and
`make runtime-check` on pull requests that change `scripts/`, `.claude/`,
`.codex/` or the `Makefile` (a changed-files step); it is the backstop for the local commit
hook, and the nightly full run covers the harness tests through `make test`. The web
checks (`web-lint`, `web-test`, `web-e2e`) are not in CI. Branch protection is the human's.

### Supply-chain scanning (2026-11-08)

Issue #241. Known vulnerabilities in what we ship, with free tooling only: CodeQL, dependency
review and secret scanning are paid on a private repository.

- **A separate workflow**, `.github/workflows/security.yml`. It runs on pull requests that
  change the dependency files, the `Dockerfile`, the ignore files, the gate or the workflow
  itself. It also runs nightly at 01:45 UTC, after CI, and on `workflow_dispatch`, with
  `contents: read`. It is not a required check, because a required check with a path filter
  would hang on every other PR. On a PR it blocks through `make check-pr`. A red nightly run is
  the signal for a new vulnerability ([operations.md](../operations.md#security-scan)).
- **Three jobs, each fails on a finding.** `python`: pip-audit (pinned in
  `requirements-security.txt`, CI only) checks `constraints.txt` and `constraints-dev.txt`,
  and every finding blocks, since pip-audit reports no severity. `ui`: `npm audit --json` in
  the pinned Node image (lockfile only, no install) blocks at `high` for production packages
  and `critical` for dev-only ones. `image`: Trivy scans the `runtime` image for fixable HIGH
  and CRITICAL findings. Trivy runs from its official image pinned by digest, not from a
  third-party action, which keeps the supply-chain surface small. The image build reads CI's
  layer cache and writes its own cache scope, so it does not evict the cache of CI's dev image.
- **The gate**, `scripts/audit_gate.py`, is stdlib only and tested in `make runtime-check`.
  It reads the tool's JSON on stdin. Exit 0: nothing blocks. Exit 1: a finding or an
  ignore-file problem blocks. Exit 2: the input is JSON the gate doesn't understand (it never
  passes silently). npm audit's report has no dev-only flag, so the gate reads `dev` from
  `frontend/package-lock.json`. A finding counts as dev-only when every installed copy of the
  package has the flag. A copy missing from the lockfile counts as production (decided on the
  issue).
- **Accepting a finding is explicit and expires.** For pip and npm, an entry in
  `ops/security/audit-ignore.txt` has the form `<id> <review-by> <reason>`. The gate fails on
  an entry whose date has passed, on a date more than 60 days ahead, and on a malformed line.
  For the image, `.trivyignore` uses Trivy's own `exp:` date, with a reason comment above each
  entry.
- **Dependabot version updates cover the GitHub Actions only** (`.github/dependabot.yml`):
  one grouped PR a month that keeps the SHA pins. Python and UI pins change only
  deliberately ([infra.md](infra.md), "Dependencies are pinned"), so there are no routine
  pip, npm or docker version PRs. A fixable OS finding is handled by bumping the base image
  through its documented procedure.
- **Dependabot alerts and security updates are on** (repository settings, enabled by the
  human; checked 2026-11-08, #268). When an advisory hits a dependency GitHub knows,
  Dependabot raises an alert and opens a fix PR. GitHub's dependency graph reads
  `requirements*.txt`, `pyproject.toml` and `frontend/package*.json`, **not
  `constraints*.txt`**: a vulnerable transitive Python package (like `opentelemetry-api` and
  `certifi` in #263) gets no alert and no PR. The `Security` workflow's pip-audit, which
  reads the constraints files, is the check for those. Trivy covers the image.
- **A Dependabot security PR is a signal, never merged as it is**, like a red nightly
  `Security` run. It touches only the manifest, so a Python bump leaves `constraints*.txt`
  at the old version and the `-c` install cannot resolve. Handle it as a task: an issue that
  links the PR, the fix in a task branch (Python: the documented pin change with
  constraints regenerated from `pip freeze`; npm: Dependabot's `frontend/package*.json`
  change may be taken over as is, checked with `make web-install` and the UI checks), and
  the Dependabot PR closed with a link to the task PR.
- **The first run's findings** were not upgraded in #241. They were accepted with review-by
  2026-12-08, and #263 fixed them: Python pins bumped, pip removed from the runtime venv
  ([infra.md](infra.md), "No pip in the runtime venv"), base image digest bumped. Only
  the two zlib1g CVEs remain accepted, renewed, because no base image ships the fix yet.

## Codex

`AGENTS.md` points Codex at CLAUDE.md. `.codex/hooks.json` runs the shared guard for Bash
and `.codex/patch-guard.sh` for `apply_patch`; both fail closed outside the repository.
`~/.codex/rules/default.rules` (outside the repository) allows `make` targets, and the
tracked `.codex/rules/commit.rules` records the permission for `make commit`. Codex matches
a rule only against a plain command, so a `make` call with a redirect or pipe prompts.
Commits also write the primary repository's `.git`, so the executor's writable roots must
include the primary checkout and the worker root.

Decisions from the #59 session review (#144, #157):

- **Bare `git add/commit/push` is blocked for Codex** by `.codex/guard.sh` (cases in
  `.codex/guard-cases.txt`, run by `.codex/test-guard.sh` from the pre-commit hook when
  `.codex/` or `.claude/hooks/` change). Claude's shared guard still allows them; the Make
  targets are the supported path for both.
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
