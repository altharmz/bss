# Harness: hooks, progress tracker, agent guardrails

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md).

## Codex compatibility

Codex discovers `AGENTS.md`, which directs it to read and follow the shared
`CLAUDE.md` and the relevant decision records. Project instructions are kept in one
place rather than copied into a separate Codex policy.

`.codex/hooks.json` registers the Bash `PreToolUse` hook. Its adapter,
`.codex/guard.sh`, sets `CLAUDE_PROJECT_DIR` from its own location and passes stdin
and the exit status through to the existing `.claude/hooks/guard.sh`. It preserves
`CLAUDE_CODE_REMOTE` when supplied. Codex cloud detection, startup setup and
formatting are outside this integration.

Hardening after review (2026-10-14):

- **Fail closed.** Both hook commands find the repository with `git rev-parse`; if
  that or the script is missing (Codex started outside the repo), they exit 2 instead
  of exit 127, which Codex would treat as a failed hook and run the tool anyway.
- **`apply_patch` hook** (`.codex/patch-guard.sh`): Codex never reads
  `.claude/settings.json`, so its Edit rules are re-stated for the patch text (every
  `Add/Update/Delete File` and `Move to` path, absolute or relative to the payload's
  `cwd`). Claude's `deny` paths (`.env`, `.env.prod`, `.env.smoke`, `design-ref/`) and
  its `ask` paths (`.claude/`, `compose*.yaml`, `Makefile`, `scripts/`,
  `docs/progress/scope.yaml`), plus `.codex/`, are blocked: Codex hooks have no "ask",
  so the human applies or approves a harness edit. An unreadable payload is blocked.
- **`make` approval.** The human's `~/.codex/rules/default.rules` allows the local make
  targets and prompts for `deploy`, `verify`, `remote`, `ssh`, `tunnel`, `clean`,
  `spec-verify-upstream` and `hooks` (proposed in the review; that file is the human's
  and lives outside the repo). A prefix rule sees only the first target, so
  `.codex/guard.sh` blocks those targets anywhere after the first in one `make` call.
- **Regression cases:** `.codex/test-hooks.sh` (27 cases: payloads in Codex's
  documented shape - `tool_name`, `tool_input.command`, `cwd` - fed to the commands
  exactly as `hooks.json` writes them, from a nested and an outside directory). Not yet
  wired into `scripts/pre-commit.sh`; run it after changing `.codex/`.

Automatic hooks require a Codex client with hook support, a trusted project and
review/trust of the hook definition. Verify activation in a fresh session; running
the adapter directly proves its policy behavior but not client activation. The
adapter uses the existing guard cases unchanged, including cloud cases, and should
be checked from a nested working directory without `CLAUDE_PROJECT_DIR` set.

## Pre-commit hook

`scripts/pre-commit.sh` runs `make lint` then `make test` and blocks the commit on
failure. `make test` takes 50-70 s wall-clock (pytest itself 46-60 s, warm or cold db),
~105 s with every core busy (measured 2026-10-03).

- Not installed by default — `.git/hooks/` is never tracked by git, so every clone (this
  one included) needs one `make hooks` run. It installs a symlink, not a copy, so edits to
  `scripts/pre-commit.sh` take effect immediately without reinstalling.
- **Keeps its full log**: every run writes everything it prints (a header with time,
  HEAD and the staged files, then each check's output) to
  `.git/pre-commit-logs/pre-commit-<UTC time>-<pid>.log` - inside the git directory, so
  never tracked; a worktree uses its own git dir. The last 10 are kept. On failure the
  hook prints `Full log: <path>`. Each check runs through `tee`; the exit code is the
  check's own (`PIPESTATUS[0]`). Proven: a deliberately failing test was refused (commit
  exit 1, HEAD unchanged) and the log held the complete pytest failure and traceback.
- Runs non-interactively: both `make` calls are given `</dev/null` so the hook never
  blocks waiting on stdin, whether it fires from a terminal, an editor's git integration,
  or any other non-interactive caller.
- To skip it deliberately (e.g. a WIP commit): `git commit --no-verify`. The hook's own
  failure output reminds you of this.
- When the staged files touch `frontend/` or `spec/`, it also runs `make web-lint` and
  `make web-test` (offline, ~+30 s: lint 20 s, test 9 s). They never install: an out-of-date node_modules fails
  with "run make web-install". A change in `app/api` or `app/internal` alone does not
  trigger them, though it can break the UI; `make verify` (`web-e2e`) is the gate for that.
- **It only lets a commit through when the working tree is exactly what is committed.**
  The checks run on the working tree, so before running them the hook refuses the commit
  (with the list of files) if a tracked file has unstaged changes (`git diff --quiet`
  fails) or an untracked file sits under `app/`, `tests/`, `frontend/` or `spec/`. Stage
  everything that belongs to the commit, or set the rest aside with `git stash push
  --keep-index`. (This replaced the old caveat that the hook tested the working tree, not
  the index: a commit once passed while missing two repository functions that were only on
  disk.) Proven: an untracked file under `frontend/`, and a partially staged change, are
  each refused before any check runs; the fully staged change commits normally.
- To check an *older* commit on its own, use a worktree: stop the local stack (`make
  down`; port 8080 conflict), `git worktree add ../bss-check <commit>`, then in that
  directory `COMPOSE_PROJECT_NAME=bss-check IMAGE_NAME=bss-check-app
  POSTGRES_PASSWORD=throwaway make lint test` (compose.yaml pins `name: bss`, so without
  the override the worktree would share this project's containers and database volume),
  then `docker compose down -v` with the same variables and `git worktree remove`.
- **One unexplained failure, investigation closed.** 2026-10-03 05:20Z the hook's `make
  test` failed once on a CLAUDE.md-only commit; what failed is lost (the commit output was
  run through `grep ... | tail -1`). Unreproduced in 30 runs (20 warm, 5 cold after `make
  down`, 5 with all 12 cores busy; 418/418 each). The `connect_timeout=3` hypothesis
  (see [Open checks](../../CLAUDE.md#open-checks)) is consistent but unconfirmed: freezing the db for 6 s with `docker
  pause` gives 500s in API tests, but a 3 s stall was never seen under real load. The
  two thread tests (`test_order_concurrency.py`, `test_billing_db.py`: `join(timeout=10)`)
  are the other real-time dependency; the hypothesis deadline is off. Diagnostics
  improved since: the 500 handler logs the exception with its traceback, and app loggers
  are no longer silenced in tests (Alembic's `fileConfig` used to disable them).

## Pre-push hook

`scripts/pre-push.sh` (also installed by `make hooks`, as a symlink) scans only the
commits being pushed - those no remote has yet - and only their added lines. It refuses
the push if a commit adds a bcrypt hash (`$2a$`/`$2b$`/`$2y$` + cost) on a line without the
marker `pre-push: allow-dummy-hash`, adds or changes a path matching `(^|/)\.env` other
than `.env.example`, deletes a remote branch, or is not a fast-forward. It names the
commit and file, never the matching value. Never bypass it (`--no-verify`). Ported from
vps-lab `a3ada0b`. Proven 2026-10-09 in a scratch repo against a local bare remote: a
clean commit, `.env.example`, a marked dummy hash and a clean new branch pushed; a hash
(on `main` and on a new branch), `.env.prod`, `sub/.env.smoke`, a branch deletion and a
non-fast-forward were refused, and the remote held only the allowed commits.

## Progress tracker

`make progress` writes `docs/PROGRESS.md` (committed, plain Markdown) and
`docs/progress/status.json` (the same data, machine-readable): per area (db, api, ui,
worker) the share of items implemented, tested and deployed, then the item table, the
unscoped and the excluded items.

- **Scope:** `docs/progress/scope.yaml`, hand-maintained, **every change needs the human's
  approval** (`"ask"` rule in settings.json). Each item has id, area, stage, title and the
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
- **Deployed:** "unknown" until `deploy.sh` writes `docs/progress/deployed.txt`
  (approved, not built).
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

## Agent guardrails: what they are for

`.claude/settings.json` (allow/deny rules) and `.claude/hooks/guard.sh` protect against
**agent mistakes, not a hostile agent**. They stop the obvious slip - reading `.env`, a
destructive docker cleanup, touching the VPN - and explain why. They are not a security
boundary:

- **Docker access is root-equivalent.** `docker compose:*` and `make:*` are allowed, and
  anyone who can start a container can bind-mount any host path into it, read or write any
  file the docker daemon can, and reach every other container on the host.
- **`.env` values are reachable despite the deny rules.** `docker compose config` prints
  the compose file with every `${VAR}` already substituted, and `docker compose run ...
  env` (or `exec ... env`) prints a container's environment. The `Read(./.env)` deny rule
  and the guard's `cat .env` pattern only cover the direct route. Do not run those
  commands; `docker compose config --quiet` validates without printing.
- **`guard.sh` is a pattern matcher over the command text.** It cannot see through
  variables, scripts, encodings or a command it has no pattern for.
- **It is quote-aware** (`.claude/hooks/guard.py`): the command is tokenised with `shlex`
  (POSIX quoting, shell punctuation as separate tokens), so text inside quotes - a grep
  pattern, a commit message - is never treated as a command. What a shell does execute is
  still checked: `sh -c`/`bash -c` scripts, `eval`, ssh remote commands, the command after
  `docker compose run <service>`, `xargs`, `find -exec`, and `$(...)`/backtick
  substitutions (even inside quotes). `cd` is tracked for path rules. The VPN, port and
  mass-stop rules still scan the raw text. **A VPN port number inside a filesystem path
  is not a port** (`/`, `~/`, `./`, `../` paths are masked first, in guard.py and in the
  fallback; found 2026-10-10 on the cloud's `/opt/pw-browsers/chromium_headless_shell-1194/`):
  `-p 1194:1194`, `host:1194`, `nc host 1194`, `1194/udp` still block, and over ssh or
  naming the VPS the number counts anywhere, paths included. **Fail-closed:** a command that cannot be
  tokenised (unbalanced quotes, a heredoc or here-string) goes through the old raw-text
  checks, exactly as strict as before. Regression cases: `.claude/hooks/guard-cases.txt`
  (allow/block per command), run by `.claude/hooks/test-guard.sh` and by the pre-commit
  hook whenever `.claude/` changes.
- **Cloud mode: `CLAUDE_CODE_REMOTE=true`**, which Claude Code sets in every cloud
  session and never on the workstation; only the exact value `true` counts
  (`docs/cloud.md`). The SessionStart hook (`startup|resume`) runs
  `scripts/cloud-setup.sh`, which exits 0 at once unless cloud mode is on, then starts
  PostgreSQL and installs the venv and `npm ci` only when missing, and always exits 0
  (a failure is reported in the session's context, never fatal). The guard then allows `npm`/`npx`/`node` on the
  host (the sandbox is the container; `pnpm`/`yarn`/`corepack` stay blocked), blocks any
  command that mentions `vps-test` or `vpn2811.duckdns.org` (quotes included, checked
  before guard.py), and lets a push through only with explicit `cloud/*` destinations
  (a bare `git push`, `HEAD`, `master`, `main` are blocked; an untokenisable push is
  blocked outright). Cases marked `@cloud` in `guard-cases.txt` run with
  `CLAUDE_CODE_REMOTE=true`, `@remote-false` with `false`, all others with it removed;
  absolute paths there are written `$CLAUDE_PROJECT_DIR`.

- **Harness files need the human's OK.** `.claude/**`, `compose*.yaml`, `Makefile` and
  `scripts/**` are under `"ask"` for the Edit tool, and `guard.sh` blocks the shell writes
  that would skip that prompt (`>`, `>>`, `tee`, `sed -i`, `mv`, `cp` as the destination,
  `rm`). Same limit as above: a write through an interpreter, `git checkout`/`git apply`
  or a container mount is not caught. Change these files with the Edit tool only.
  The `"ask"` rules are unverified under auto mode; if they don't prompt there, these
  paths move to `"deny"`.

So: the rules in ["Hard constraints"](../../CLAUDE.md#hard-constraints) bind because they are the rules, not because
something enforces them. Anything that needs real enforcement (secrets an agent must
never see, hosts it must never reach) has to be kept out of the environment the agent
runs in, not filtered here.
