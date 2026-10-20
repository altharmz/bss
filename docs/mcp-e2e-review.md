# MCP end-to-end review (2026-10-19)

An external agent (ChatGPT) called all 145 MCP tools against the deployed bss and
reported six findings and one observation. Each was checked against the code and the
tool schemas the live connector serves. This file is the work list; tick items off,
record the decisions in `docs/decisions/`, and delete this file when it is done.

Usual rules apply (CLAUDE.md "Per change"): plan, tests, negative proofs, `make lint`,
`make test` x3, decision recorded, diff shown, commit only after the human's OK. Deploy
only with a separate approval.

## Release status

Slices A-C were committed as `40805f4` and are included in deployed `7ad101e`
(2026-10-20). The release checks and successful deployment retries are recorded in
CLAUDE.md. Implementation is complete; the unchecked production follow-ups below
remain unverified and keep this review record open.

## Implementation validation

2026-10-19: slices A-C are implemented locally, with the human selecting same-day
status undo and scheduled termination for future products. No migration. The focused
ordering/suspension/product tests pass; negative proofs for A, B and C failed as
expected and were reverted. Three final `make test` passes each passed all 1,366 tests
(552.69 s, 568.02 s, 459.91 s). Lint, decisions-check and whitespace checks pass.

The edit hook blocked the reviewed harness patch; the human then explicitly requested
and approved a one-time shell application. The local MCP `spec/` mount and TMF622/637
API notes are applied, the API docs regenerated, and `make mcp-smoke` passes (10 checks).
The production image already includes `spec/`. Commit, push and deployment are
authorized (2026-10-19); the release gate is `make verify`.

## Verdicts

| ID | Verdict | Evidence |
|---|---|---|
| 002 `patch_order` has no `state` | **Bug** | `app/api/ordering.py:591` pops `state` before parsing `OrderPatch`; the tool schema comes from `OrderPatch` with `additionalProperties: false`, so an MCP client cannot send `state` and a draft cannot be submitted. The tool description contradicts itself (`bss_mcp/operations.py:422` "State ... read-only" vs the handler's docstring "`state: acknowledged` processes it"). |
| 006 `list_bill_cycles` `billingDate` filter is a bare string | **Bug** | Filters are read from the handlers' 400 ("filters: a, b"), names only, so every filter is `{"type": "string"}`. The server needs a date-time with offset (`app/api/bill.py:403`). |
| 003 `create_offering.externalIdentifier` typed `any` | **Bug (schema only)** | One of the "TMF620 x, as the spec defines it" passthrough attributes: validated against `spec/` at runtime (`@type` required by `Extensible_FVO`), untyped in the tool. TMF622 `externalId` is our own lenient model (adds `@type`); keep it lenient - tightening could break callers. |
| 001 discovery shows 57 of 145 | **Not ours** | `list_tools` in `bss_mcp/server.py` returns all tools unpaginated (145 for a write token: 79 write, 66 read). The cut is the client's tool-search layer. Mitigate only. |
| 004 future delete of a not-yet-started product -> `productNotStarted` | **Original design; changed locally with approval** (slice C): a later end is now scheduled. |
| 005 same-day suspension cannot be undone | **Original design; changed locally with approval** (slice B): same-day cancellation restores the previous status. Billing is unchanged. |
| OBS-001 `environment: staging` | **Expected** - `docs/decisions/worker.md:160`: `vps-test` runs `staging` on purpose. No action. |

Also found: `bss_mcp/operations.py:79` still labels TMF637 "(read-only)"; it has
writes since 2026-10-17.

## Slice A - MCP tool schemas tell the truth (no migration; `bss_mcp/` + decisions)

- [x] **002** `patch_order`: add `state` (enum `["acknowledged"]`, nullable) to the tool's
      body schema; fix the description in `bss_mcp/operations.py:422`.
      Test: through the MCP tool, POST a draft -> PATCH items/date -> PATCH
      `state: acknowledged` -> completed (or rejected with reasons); a second submit /
      a placed-with change after processing is a 409. Correction: the existing API
      returns 422 for a second submit; this slice preserves that behavior.
- [x] **002 guard:** a test that every key a write handler takes out of the body before
      parsing its model (e.g. `body.pop("state")`) is in that tool's schema - so a
      schema can't silently drop an accepted attribute again. Negative proof: remove
      the field, see it fail, revert.
- [x] **006** typed filters: let the registry (`bss_mcp/operations.py`) declare a schema
      per filter; `billingDate`: `format: date-time`, example
      `"2026-10-12T21:00:00Z"`, description "local midnight of the billing day
      (BILLING_TZ), with offset". Sweep the other list filters for dates/date-times.
- [x] **003** passthrough attributes: generate their description from `spec/` - the FVO
      name, array or object, required keys (e.g. "array of ExternalIdentifier_FVO;
      required: id, @type"). Generic for all "as the spec defines it" attributes; no
      full inlining (145 tools' size). Test on `create_offering.externalIdentifier`.
- [x] **001 mitigation:** MCP server `INSTRUCTIONS` (`bss_mcp/server.py`) name the tool
      count and the API families and say "if your client lists fewer, search by
      family". Fix the TMF637 "(read-only)" label.
- [x] Decision in `docs/decisions/mcp.md`; `docs/mcp.md` if client-facing text changes;
      `make lint`, `make test` x3, `make mcp-smoke`; diff; commit after OK.

## Slice B - undo a same-day status change (005) - policy approved

Recommended: `cancelProductOrder` may undo a status change whose day is **today**
(already in effect): delete the `product_status_change` row, restore the product's
status, emit a ProductStateChangeEvent, order `cancelled`. Billing untouched (suspension
does not bill differently). A TMF637 PATCH `status` refused with `openModifyItem` for
a same-day change names this route in its 409. Rejected alternative: "last change of
the day wins" (hides the first order's effect).

- [x] Human confirms the policy.
- [x] Tests: active -> suspended -> cancel -> active same day; suspended -> active ->
      cancel same day; a change dated earlier than today still 409; a pending (future)
      change cancels as now; events emitted.
- [x] Update [suspension](decisions/tmf-api.md#suspension-2026-10-19) decision; API
      notes for TMF622/637 (`scripts/docs/api-notes/`, harness file: needs the OK).

## Slice C - future delete of a not-yet-started product (004) - option (a) approved

- **(a) relax (recommended, fits the "follow TMF" rule):** a delete dated after the start
  becomes a scheduled termination: `termination_date` set, status `created` until the
  start, then `pendingTerminate`, then `terminated` (check the worker's flips and
  `ENDED` handling, cancelProductOrder of such a delete, modify/status rules with an end
  set). TMF637 POST `terminationDate` for a future product follows (no longer 409).
- **(b) keep the rule:** only reword the reason, e.g. "the product starts on 2026-10-20:
  until then it can only be cancelled (a delete dated on or before 2026-10-20), not
  given a later end".

- [x] Human picks (a) or (b): **(a)** approved.
- [x] Implement, tests (matrix: start D+1 with delete D / D+1 / D+2; active + D+2;
      already ended), update decision `tmf-api.md` and API notes (harness: OK needed).

## Follow-ups

- [ ] On/after **2026-10-20**: via MCP, check that restore order
      `10da28f9-36bf-4705-80f1-87210e9b10d8` made product
      `44322d88-7734-480b-944a-74ef853c6f54` (Galina, Mobile S) `active` again.
- [ ] Test leftovers on the VPS (by design, have history): individual `a88f1f43-...`,
      customer `95680b10-...`, billing account `5d82435c-...` (all named
      `TMF-E2E-20261019`), bill-on-demand `39fd122e-...` (waits for the next run). No
      action unless the human wants them gone.
- [x] Deploy slices A-C only with the human's separate approval: granted 2026-10-19.
