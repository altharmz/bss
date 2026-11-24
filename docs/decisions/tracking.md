# Tracking: progress tracker, UI/MCP parity and board priority

Index: [README.md](README.md).

## Progress tracker

`make progress` writes `.progress/PROGRESS.md` and `.progress/status.json` locally
(not committed): per area (db, api,
ui, worker) the share of items implemented and tested. It reruns the whole suite with
recording (over 10 minutes), so it runs only on request. A committed snapshot goes stale;
see the [documentation count rule](harness.md#documentation-no-hand-written-counts).

- **Scope:** `docs/progress/scope.yaml`, hand-maintained and editable within feature work.
  Each item has id, area, stage, title and the key the code is matched on: `table` (db),
  `method` + `path` exactly as `app.openapi()` reports it (api), the id as the `data-ui`
  marker (ui). `excluded` entries are ignored by the unscoped check.
- **Implemented** is derived: db - the table exists in a throwaway database at migration
  head; api - `app.openapi()` has the operation; ui - a literal `data-ui="<id>"` (or
  `<Dialog ui="<id>">`) exists in `frontend/src` (TypeScript AST). Screens carry the marker
  on a `.bss-screen` wrapper, dialogs via the kit `Dialog`'s `ui` prop, actions on their button.
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

## UI/MCP parity check

`scripts/ui_mcp_parity.py` scans `frontend/src` (no tests, no `generated/`) for every
`getJson`/`getAll`/`getPage`/`postJson`/`patchJson`/`request('METHOD', ...)` call and
resolves its path through constants, maps, single-return helpers, ternaries and local
`const`s, turning `${...}` into `{}` (a text scan, not a parser). `tests/internal/test_ui_mcp_parity.py`
fails for (a) an MCP operation (API operations minus scope exclusions and auth operations)
with no matching (method, path) UI call and no line in `tests/ui-mcp-allowlist.txt`, (b) a
UI call whose path cannot be resolved, and (c) a stale, unknown or reasonless allowlist
entry. A path taken from a variable at run time is declared next to the call as
`// parity: METHOD <path expression>`. The allowlist holds `create/patch/delete_product`
(orders alone), detail reads that list rows already carry, and probes without a screen
(`healthz`, `billing_status`, `extension_schema`). The scan matches method and path
template, not query parameters or bodies.

## Board priority

**The board's Priority field is the one source of priority** (2026-11-24, #468). Priority
was a `P0`-`P3` label; nothing stopped an issue from carrying two or none, labels neither
sort nor group on the board, and they crowded the label list. The project's single-select
**Priority** field (P0-P3, IDs in [board.md](../board.md)) holds exactly one value per
item and is what the board shows, so it replaced the labels: the field was re-synced from
the labels for every open item, then the labels were removed from the issues and deleted
from the repository.

- `make start-task` ranks eligible Ready items by the field (P0 first; empty counts as P3),
  then by board order. A value outside P0-P3 stops selection instead of guessing.
- `make set-priority` (still behind `HUMAN_APPROVAL=<issue>`), `make add-task PRIORITY=`
  and `make new-priority-task` (always P0) write the field and confirm it by reading the
  board again; `LABELS=` refuses `P0`-`P3` so a stale habit cannot recreate a label.
  `make new-priority-task ISSUE=<n>` finishes a preserved issue whose field is P0 or
  still empty (creation stopped before the field was set), never one with another value.
- `make board` shows each item's field value, and task facts record it (`priority` in
  `task_facts`, telemetry schema 6), so `make telemetry-summary` shows it per issue.
  Facts captured earlier keep their `P0`-`P3` labels and an empty `priority`.
- The nightly CI report cannot write the owner's project with the run's token, so its
  issue arrives with an empty field and triage sets it.
