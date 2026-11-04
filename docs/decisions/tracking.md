# Tracking: progress tracker and UI/MCP parity

Index: [README.md](README.md).

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
`const`s, turning `${...}` into `{}` (a text scan, not a parser). `tests/test_ui_mcp_parity.py`
fails for (a) an MCP operation (API operations minus scope exclusions and auth operations)
with no matching (method, path) UI call and no line in `tests/ui-mcp-allowlist.txt`, (b) a
UI call whose path cannot be resolved, and (c) a stale, unknown or reasonless allowlist
entry. A path taken from a variable at run time is declared next to the call as
`// parity: METHOD <path expression>`. The allowlist holds `create/patch/delete_product`
(orders alone), detail reads that list rows already carry, and probes without a screen
(`healthz`, `billing_status`, `extension_schema`). The scan matches method and path
template, not query parameters or bodies.
