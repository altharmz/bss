# Harness: workflow, hooks, guard, CI, progress tracker

Principle: every regular operation is a `make` target; agents are told to use them
([worktrees.md](../worktrees.md)) and little is blocked. The agent's work ends at the merge
to `master`; deploying is the human's (`make deploy`, [infra.md](infra.md)). Dated per-issue
history is in [../archive/harness-history.md](../archive/harness-history.md).

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
- `make runtime-check` (the harness script tests: 11 suites run in parallel by
  `scripts/run_suites.py`, about 70-100 s; they took 3 min 2 s when run one after another)
  and the guard cases, only when `scripts/`, `Makefile` or `.claude/` change (the guard
  cases only for `.claude/`);
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
editing `.env*` and editing `design-ref/` (the read-only design handoff). The PostToolUse
hook `format.sh` runs ruff (Python) or prettier (frontend) on every edited file inside the
task's containers; it strips imports that are not used yet. `.codex/patch-guard.sh` applies
the same file denies to Codex's `apply_patch`.

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
githubstatus.com. Manual `gh project` commands are recovery only.

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
pins actions to commit SHAs. The dev image is built with the Actions layer cache. The web
checks (`web-lint`, `web-test`, `web-e2e`) are not in CI. Branch protection is the human's.

## Codex

`AGENTS.md` points Codex at CLAUDE.md. `.codex/hooks.json` runs the shared guard for Bash
and `.codex/patch-guard.sh` for `apply_patch`; both fail closed outside the repository.
`~/.codex/rules/default.rules` (outside the repository) allows `make` targets, and the
tracked `.codex/rules/commit.rules` records the permission for `make commit`. Codex matches
a rule only against a plain command, so a `make` call with a redirect or pipe prompts.
Commits also write the primary repository's `.git`, so the executor's writable roots must
include the primary checkout and the worker root.

## UI/MCP parity check

`scripts/ui_mcp_parity.py` scans `frontend/src` (no tests, no `generated/`) for every
`getJson`/`getAll`/`getPage`/`postJson`/`patchJson`/`request('METHOD', ...)` call and
resolves its path through constants, maps, single-return helpers, ternaries and local
`const`s, turning `${...}` into `{}` (a text scan, not a parser). `tests/test_ui_mcp_parity.py`
fails for (a) an MCP operation (API operations minus scope exclusions and auth operations)
with no matching (method, path) UI call and no line in `tests/ui-mcp-allowlist.txt`, (b) a
UI call whose path cannot be resolved, and (c) a stale, unknown or reasonless allowlist
entry. A path taken from a variable at run time is declared next to the call as
`// parity: METHOD <path expression>`. The allowlist holds `create/patch/delete_product`
(orders alone), detail reads that list rows already carry, and probes without a screen
(`healthz`, `billing_status`, `extension_schema`). The scan matches method and path
template, not query parameters or bodies. Design:
[next-slices-design.md](../next-slices-design.md#24-parity-check).

## Progress tracker

`make progress` writes `docs/PROGRESS.md` and `docs/progress/status.json`: per area (db, api,
ui, worker) the share of items implemented and tested. It reruns the whole suite with
recording (over 10 minutes), so it runs only on request.

- **Scope:** `docs/progress/scope.yaml`, hand-maintained and editable within feature work.
  Each item has id, area, stage, title and the key the code is matched on: `table` (db),
  `method` + `path` exactly as `app.openapi()` reports it (api), the id as the `data-ui`
  marker (ui). `excluded` entries are ignored by the unscoped check.
- **Implemented** is derived: db - the table exists in a throwaway database at migration
  head; api - `app.openapi()` has the operation; ui - a literal `data-ui="<id>"` (or
  `<Modal ui="<id>">`) exists in `frontend/src` (TypeScript AST). Screens carry the marker
  on a `.bss-screen` wrapper, dialogs via `Modal`'s `ui` prop, actions on their button.
- **Worker** items are proven by behaviour: implemented and tested when a test marked
  `@pytest.mark.capability("<id>")` passes and none fails; an unknown id is an error.
- **Tested, ui:** a passing vitest test saw the marker on screen (`expectUi(id)`); the reporter
  writes the passed ids to `.progress/ui-tested.json`.
- **Tested, db and api:** what passing tests exercised, from one in-process run of the whole
  suite: every request sent to `app.main.app` through any starlette `TestClient`, matched to
  an `app.openapi()` operation, and the tables with inserted rows in the test's database.
  A test that failed in any phase counts for nothing.
- **Unscoped:** a table, operation or marker in the code that is neither an item nor excluded
  makes `make progress` exit 1 after writing the files.
- **How it runs:** `tests/progress_collect.py` in the app container (scope.yaml on stdin),
  `frontend/scripts/ui-markers.mjs` and vitest in `web-offline`, then
  `scripts/progress/generate.py` on the host from the JSON in `.progress/` (gitignored).
  The output is deterministic.
