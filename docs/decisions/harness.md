# Harness: hooks, guard, progress tracker

Simplified on 2026-10-20 (the human: keep only the really important rules; one local
test run per development cycle; commit and push need no approval). Since 2026-10-21
deployment is the human's and outside development (2026-10-28): the agent's work ends at
the merge to master, and it neither tracks nor reasons about what is deployed
(`deploy.sh`, `make deployed` and `.deployed` are the human's tools,
[infra.md](infra.md#local-deploy-record-2026-10-21)).

## Development cycle and gates

- **One local test run per cycle**: develop -> commit (the pre-commit hook runs lint and
  tests) -> push. Deploying is the human's, outside the cycle.
  `make check` (ci Hypothesis profile, docs-check, UI and browser tests) runs only when
  asked for a specific change.
- **Pre-commit hook** (`scripts/pre-commit.sh`, installed by `make hooks` as a symlink):
  `make lint` + `make test`, plus `web-lint`/`web-test` when `frontend/` or `spec/` is
  staged, `make docs-check` (~7 s) when `app/`, `alembic/`, `spec/`, `scripts/docs/` or
  `tests/docs_collect.py` is, and the guard cases when `.claude/` is. A commit of
  Markdown files only (outside `spec/` and `scripts/docs/`, which feed tests and the
  generated docs) skips every check (2026-10-20). It refuses a commit unless the working
  tree is exactly what is committed (no unstaged tracked changes, no untracked files under
  `app/`, `tests/`, `frontend/`, `spec/`) - a commit once passed while missing two
  functions that were only on disk. Full output of every run: `.git/pre-commit-logs/`
  (last 10). One run at a time per repository (2026-10-23): the checks take an `flock`
  on `pre-commit.lock` in the common git directory (all worktrees; not another clone or
  project), and a second commit waits for it (30 minutes at most), then checks the tree
  again - two sessions committing at once had run two suites side by side, 12.5
  minutes instead of 5, with the worker heartbeat tests failing on timing. Cases:
  `scripts/test-pre-commit-lock.sh` (in `make test`). The guard blocks skipping it locally; cloud sessions, which have no Docker,
  commit without the hook and run the checks of [docs/cloud.md](../cloud.md) instead.
- **Pre-push hook** (`scripts/pre-push.sh`): scans only the commits being pushed and
  their added lines; refuses a bcrypt hash without the marker `pre-push: allow-dummy-hash`,
  an added or changed `.env*` path (other than `.env.example`), a branch deletion and a
  non-fast-forward. Names the commit and file, never the value.
- **No extra gates** (the human, 2026-10-21): the hook's run is the only required test
  run, and commit and push need no approval - also where an older plan or
  hand-off still asks for "three backend passes", release proofs or "separate approval"
  (CLAUDE.md says it overrides them; the forward-looking ones were reworded). Extra runs
  (`make check`, `web-e2e`, `progress`, negative proofs) are the agent's call.
- **Tests with every change** (the human, 2026-10-21): every new feature or bug fix
  comes with a test that fails without it; showing that is optional and uses a targeted
  pytest run of that test, never a full suite. A CLAUDE.md rule, not enforced by a hook.
- `make progress` runs only on request (it reruns the whole suite with recording, over
  10 minutes); scope.yaml entries are still added with the feature.
- `make test` takes about 8 minutes (1,433 tests, 2026-10-20). Timing-based tests (`test_worker_heartbeat.py`, the thread
  tests in `test_order_concurrency.py`/`test_billing_db.py`) can fail under full load;
  rerun once before suspecting a regression.
- To check an *older* commit on its own, use a worktree: `make down`, `git worktree add
  ../bss-check <commit>`, there `COMPOSE_PROJECT_NAME=bss-check IMAGE_NAME=bss-check-app
  POSTGRES_PASSWORD=throwaway make lint test` (compose.yaml pins `name: bss`), then
  `docker compose down -v` with the same variables and `git worktree remove`.

## The guard

`.claude/hooks/guard.sh` (PreToolUse, Bash) blocks the slips against CLAUDE.md's hard
constraints: the VPN (its names, and its ports in a network context - ssh/VPS, docker
publish, firewall and socket tools, `host:port`; numbers inside filesystem paths are not
ports), destructive docker cleanup and mass stops, privileged or build commands over
ssh, reading `.env*` or printing secrets, skipping the git hooks, `reset --hard` /
`clean -f`, force/delete/mirror/prune/refspec pushes, and Node on the host. Cloud mode
(`CLAUDE_CODE_REMOTE=true`): any command naming the VPS is blocked, npm/npx/node are
allowed on the host, and a push must be exactly `git push -u origin cloud/<topic>`.
Cases: `.claude/hooks/guard-cases.txt`, run by `.claude/hooks/test-guard.sh`.

It is a pattern matcher over the raw command text, not a security boundary: Docker
access is root-equivalent, and it cannot see through variables, scripts or interpreters.
Because it reads raw text, a command that only *mentions* a blocked pattern (in a commit
message, a heredoc) is blocked too: put such text in a file (`git commit -F <file>`) or
use the Write tool. `settings.json` denies Read/Edit of `.env*` and Edit of `design-ref/`
(the design handoff, a read-only reference). The PostToolUse hook `format.sh` runs ruff
(Python) or prettier (frontend) on every edited file - it strips imports not used yet.

Removed on 2026-10-20: commit approval, the `"ask"` rules and shell-write blocks for
harness files, the quote-aware `guard.py` (485 lines, 360 cases), `decisions-check` and
`retired.md`, the Edit-only / no-heredoc / no-output-filtering rules, and the per-change
checklist (negative proofs, three test runs, a second local run before deploying).

## Codex compatibility

`AGENTS.md` points Codex at CLAUDE.md. `.codex/hooks.json` runs `.codex/guard.sh` (sets
`CLAUDE_PROJECT_DIR`, then the shared guard) for Bash and `.codex/patch-guard.sh` for
`apply_patch` (never `.env*` or `design-ref/`). Both fail closed outside the repository.

The user-level `~/.codex/rules/default.rules` allows every `make` target without approval
(owner decision 2026-10-21). Codex matches a rule only against a plain command, so a
`make test > log 2>&1` call misses it and prompts; AGENTS.md asks for bare `make` calls.

## UI/MCP parity check (2026-10-26)

Design: [next-slices-design.md](../next-slices-design.md#24-parity-check). Built:
`scripts/ui_mcp_parity.py` scans `frontend/src` (no tests, no `generated/`) for every
`getJson`/`getAll`/`getPage`/`postJson`/`patchJson`/`request('METHOD', ...)` call and
resolves the path through string constants, `X[kind]` maps, single-return helper
functions, ternaries, `+` and nearest local `const`s, turning `${...}` into `{}`
(a text scan, not a parser; `client.ts`, the helpers themselves, is skipped).
`tests/test_ui_mcp_parity.py` fails (a) for an MCP operation (API operations minus
scope exclusions and auth operations) with no matching (method, path) call and no line in
`tests/ui-mcp-allowlist.txt`, (b) for a UI call whose path the scan cannot resolve, so
an unusual indirection cannot hide a call, (c) for an allowlist entry that is stale
(the UI calls it now), unknown or has no reason. Five unit tests cover the extractor on
inline samples. Negative proof: with the `get_refund` line removed the test failed
naming `get_refund`.
- **Markers.** A path taken from a variable at run time is declared next to the call as
  `// parity: METHOD <path expression>` (balance action posts, transfer post, hub
  delete, the sidebar counts). The marker adds the call and acknowledges the
  unresolvable call within six lines below it.
- **Allowlist (23).** `create/patch/delete_product` (orders alone); detail reads the
  list rows already carry (`get_order`, `get_cancellation`, `get_refund`, `get_bill`,
  `get_bill_demand`, `get_catalog`, `get_category`, `get_cycle_specification`,
  `get_bill_format`, `get_bill_media`, `get_import_job`, `get_export_job`, the three
  account details, `get_hub_customer`, `get_hub_resource`); `healthz`, `billing_status`
  and `extension_schema` (probes and a linked document, no screen).
- **Deviation.** The design lists `get_bill` as "the internal bill detail"; the UI does
  not call the internal bill read, so its reason says the Bills screens use the list rows.
  The scan matches method and path template, not query parameters or bodies.
- **Test container.** `compose.override.yaml` mounts `frontend/src` (replacing the single
  `hubs.ts` mount) and `scripts/ui_mcp_parity.py` read-only.

## Progress tracker

`make progress` writes `docs/PROGRESS.md` (committed, plain Markdown) and
`docs/progress/status.json` (the same data, machine-readable): per area (db, api, ui,
worker) the share of items implemented, and tested, then the item table, the
unscoped and the excluded items.

- **Scope:** `docs/progress/scope.yaml`, hand-maintained and editable by agents within
  authorized feature work (policy revised 2026-10-14). Preserve existing items and
  exclusions unless the task changes them. Reports remain generated by `make progress`.
  Each item has id, area, stage, title and the
  key the code is matched on: `table` (db), `method` + `path` exactly as `app.openapi()`
  reports it, parameter names included (api), the id itself as the `data-ui` marker (ui).
  `excluded` entries (id, area, reason) are ignored by the unscoped check.
- **Implemented** (derived, never written by hand): db - the table exists in a throwaway
  database migrated to head; api - `app.openapi()` (FastAPI's public interface, not its
  router internals) has the operation; ui - a literal `data-ui="<id>"` (or `<Modal
  ui="<id>">`) exists in `frontend/src`, found with the TypeScript AST. Screens carry the
  marker on a `.bss-screen` wrapper (`display: contents`, so the layout is unchanged),
  dialogs via `Modal`'s `ui` prop, actions on their button.
- **Worker** items are behaviour, proven rather than detected: implemented and tested
  together when at least one test marked `@pytest.mark.capability("<id>")` passes and
  none fails. The collector runs those tests (`pytest -m capability`, in process, output
  on stderr); a test marked with an id that is not a worker item in `scope.yaml` is an
  error (exit 1). Proven: a passing marked test turns its item green, one failing test
  next to a passing one keeps it red, an unknown id fails the run.
- **Tested, ui** (since `cloud/billing-runs-ui`): a passing vitest test saw the marker on
  screen - `expectUi(id)` (`frontend/src/test/expectUi.ts`) asserts it and records the id
  on the test; `make progress` runs vitest with `frontend/scripts/ui-tested-reporter.mjs`
  (only when `UI_TESTED_OUT` is set), which writes the ids of the **passed** tests to
  `.progress/ui-tested.json`. An id that is not a ui item is an error (exit 1). Proven: a
  test that calls expectUi and then fails does not count (with the passed-filter removed
  it did), an unknown id fails the run. 13/13 today.
- **Tested, db and api** (since `cloud/quality-1`): what the **passing** tests
  exercised, from one in-process run of the whole suite by the collector (which also
  yields the worker capabilities; serial, ~3.5 min in the cloud). `tests/conftest.py`
  records per test, only when the collector sets `BSS_PROGRESS_RECORD=1` (`make test` is
  unchanged): api - every request sent to `app.main.app` through any starlette
  `TestClient` (class-level wrapper of `TestClient.request`: `TmfClient` and the raw
  clients of `test_health.py`, which a `TmfClient`-only recorder would miss), matched by
  the collector to the `app.openapi()` operation (method + path template, a literal
  segment before a parameter; a request no operation serves, e.g. a 404 probe, counts
  for nothing); db - the tables of the test's throwaway database with
  `pg_stat_user_tables.n_tup_ins > 0`, read in `migrated_db_url`'s teardown before the
  drop, after waiting for the database's other sessions to end (a backend flushes its
  counts at exit, before it leaves `pg_stat_activity`). An autouse fixture hands both to
  the collector in the test's `user_properties`; a test that failed in any phase counts
  for nothing. Migration tests' empty databases (`temp_db_url`) are not counted. Proven:
  with `test_healthz_ok_when_db_reachable` and
  `test_balance_is_payments_minus_charges_positive_means_credit` failing after their
  requests and writes, a run of those two counted no operation and no table; with the
  passed-filter removed it counted `GET /healthz`, `POST /payment` and `payment` (and
  the rest they touch). Not proven: the wait for the sessions to end - five runs
  without it lost nothing. 18/18 and 57/57 today.
- **Unscoped:** a table, operation or marker in the code that is neither an item nor
  excluded makes `make progress` exit 1 (after writing the files).
- **How it runs:** `tests/progress_collect.py` in the app container (scope.yaml arrives
  on stdin, parsed with the image's pinned PyYAML; tables via a throwaway
  `vpslab_progress_*` database, dropped again; then the whole test suite, in process -
  its output on stderr; extra arguments narrow that run, for negative proofs),
  `frontend/scripts/ui-markers.mjs` and
  vitest with the ui-tested reporter in `web-offline`, then `scripts/progress/generate.py`
  on the host (standard library only)
  from the collectors' JSON in `.progress/` (gitignored). The output is deterministic (no
  timestamps, no line numbers), ready for the drift check of step 4.

