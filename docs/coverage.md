# Line coverage

`make coverage` measures executable line coverage of the application from one checkout
(#480) and writes `.coverage-report/report.md` and `report.json` (gitignored):

- totals for the backend (`app/`, `bss_mcp/`), the frontend (`frontend/src/` TS/TSX) and
  the two combined;
- every file with missing lines, as line ranges, largest gap first;
- the explicit exclusions (`# pragma: no cover` for Python, `v8 ignore` comments for the
  frontend), so none is hidden;
- each suite's exit status. The target fails after writing the report when a suite failed
  or a report is missing.

It is not part of `make test` or the commit hook, and takes the test lock like the other
heavy targets. Expect the backend run to take longer than `make test`: it runs every test,
the `migration_history` and `slow` ones too, under instrumentation.

## What it runs

1. **Backend.** The whole pytest suite (no mark filter) in the `oneoff` service with
   coverage.py. `scripts/coverage/coverage.ini` (`parallel = True`, `source = app, bss_mcp`)
   and `scripts/coverage/sitecustomize.py` (`coverage.process_startup()`) are copied to
   `.testmon/coverage/`, which the container sees as `/testmon/coverage`; with
   `COVERAGE_PROCESS_START` and `PYTHONPATH` pointing there, the xdist workers and the
   worker and MCP subprocesses the tests start record too. The data is combined and
   written as coverage.py JSON.
2. **Frontend.** The default Vitest run (`npx vitest run --coverage`, UTC only) with
   `@vitest/coverage-v8`, configured in `frontend/vite.config.ts`: `src/**/*.{ts,tsx}`
   minus tests, `src/test/`, `src/api/generated/` and declarations. The reports go to
   `frontend/.coverage/`.
3. **Report.** `scripts/coverage_report.py` counts frontend lines the way
   istanbul-lib-coverage does (a statement counts on its start line; a line is covered when
   any statement on it ran), checks the count against Vitest's `json-summary`, and adds the
   raw covered and executable counts of both sources for the combined percentage. Reporter
   percentages are never averaged.

Outside the measurement: tests, generated types, declarations, CSS, migrations, tooling,
Playwright and the second-timezone Vitest run. If browser coverage is added later, merge
it with the component coverage by file and line before computing a new total.

## Exclusions

An exclusion marks code no test can reach, never code that is merely untested. Each one
carries the reason on the same line or the line above, and the report lists it.

## Results

Dated measurements and the residual lists per area are in the issues that carry the work:
#480 (baseline at `333baba`, 2026-11-25) and its follow-ups.
