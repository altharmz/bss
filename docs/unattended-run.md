# Unattended run

Work done while the owner was away, after Checkpoint 5 was approved. Scope, in order:
stage 5 checkpoint 6 (catalog), then stage 4 checkpoint 1 (worker design note, no code).
No deploy, no SSH, no vps-infra, no push. Local commits only when `make lint`, `make
test`, `make web-lint` and `make web-test` pass, through the pre-commit hook.

## Commits made

| Commit | What |
|---|---|
| `ab76a12` | make seed: deterministic demo data through the public TMF API |
| `4b652cf` | UI: subscribers list, subscriber card, Add product with backend preview, Deactivate via TMF622 |
| `0419c17` | CLAUDE.md: make seed |
| `7e6ea69` | docs: unattended-run handoff log |
| `f6e6679` | UI: product catalog, New offering, lifecycle steps (In Design -> Active -> Retired) |
| `ae64458` | tests: the admin UI's write requests replayed against the real API |
| `53cf297` | CLAUDE.md: catalog conventions, UI write requests pinned on both sides |
| `b4723e9` | docs: stage 4 worker design note (checkpoint 1, no code) |
| (this) | docs: unattended-run log complete |

Final state: all four gates green at every commit (backend 376 tests, UI 173 tests,
ruff, tsc/eslint/prettier), all through the pre-commit hook. Nothing deployed, no SSH, no
vps-infra change, nothing pushed. Scope finished: stopped after stage 4 checkpoint 1 as
instructed.

## Decisions taken unattended

- **Catalog lifecycle action lives in an offering detail dialog (row click), not a table
  column.** The design's 7 fixed columns leave the name ~110px at 1280px; an Actions column
  would overflow. The dialog reuses the design's inline-confirm pattern (Deactivate).
- **Only forward lifecycle steps in the UI: In Design -> Active -> Retired; Retired is
  final.** As the brief says. The backend still allows any PATCH of lifecycleStatus; I did
  not add a backend rule (not asked, and the UI never sends other steps). Worth deciding.
- **Retire confirm copy says existing products keep billing** (D12: retiring never ends
  prices).
- **New offering = two TMF620 calls** (price, then the offering referencing it), as the
  spec models it. If the second fails, the unattached price stays (it can price nothing);
  the error is shown, nothing is rolled back silently.
- **The new price is created with lifecycleStatus "Active", the offering "In Design".**
  The offering's status is what gates ordering; the price's lifecycle is not checked
  anywhere. Matches the seed.
- **"Valid from" defaults to tomorrow and cannot be earlier**; "Valid to" is inclusive in
  the UI and sent as the exclusive midnight of the next day. *Superseded after review:*
  dates are now sent with BILLING_TZ's offset for that specific date (`zonedDateTime`,
  Intl with `timeZone`), never the browser's; the suite also runs with
  TZ=America/New_York to prove it.
- **Code rule shown before submit**: `^[A-Z0-9][A-Z0-9_-]{0,31}$`, upper-cased as typed
  (migration 0003's rule; the backend still answers 409/422 itself).
- **Catalog price column**: the price valid now in the UI currency; if none, the next
  future price with "from DATE"; otherwise "—". Display only.
- **Recurrence select shows "month" only, disabled** (weekly dropped, decision 5).
- **Footer copy**: "Prices exclude taxes · taxes are not applied" (taxes out of scope).
- **UI write requests are also pinned on the backend** (`tests/test_ui_requests.py`):
  the exact bodies the UI sends, replayed through the contract-checking client on a
  throwaway database - instead of creating test offerings in the seeded demo database,
  which the API cannot delete.

## Pending my approval

- **Stage 4**: every D-W item is decided and recorded in `docs/stage4-design.md`;
  nothing is open. The price invariant it relies on is implemented: D-W18 (`fd0c0a7`),
  D-W20 (`60f2a58`), D-W21 (`845498b`, UI `aa231d4`). No worker code was written.
- When stage 4 is implemented it needs protected files: `compose.prod.yaml` and
  `compose.override.yaml` (worker service), `scripts/deploy.sh` (start the worker,
  smoke `/billing-status`, optional wait for idle), `Makefile` (`worker-once`). None was
  touched in this run.
- vps-infra (separate project): a Caddy carve-out for `/billing-status` without auth,
  like `/healthz` - to add to the existing change request.

## Stuck / unfinished

Nothing stuck; no command was refused during the run, and no protected file was needed.

## Manual review list (`make web-dev`, http://localhost:5173, after `make seed`)

1. **Subscribers list**: the table; the state filter with Suspended (Sergey Volkov) and
   Closed (Dmitry Sokolov); a search for "romash" and for "zzz" (No matches + Reset).
2. **Olga Kuznetsova's card**: large negative balance and "In arrears", credit limit 500,
   negative "Available incl. credit". Add product -> Fiber 100: prorated preview with the
   credit-limit warning.
3. **Anna Smirnova's card**: a product with an "ends ..." pill and no Deactivate.
   Deactivate the other one to see the confirm and the toast.
4. **Elena Morozova's card**: a "starts ..." (future-dated) product and an empty charge
   history ("No charges") - her orders are from today.
5. **Sergey Volkov's card**: "+ Add product" disabled with its caption.
6. **Ivan Petrov's card**: a refund in the charge history and a mix of transactions.
8. **Catalog list**: codes, "Price, RUB", "month", status pills, validity
   ("2026-06-04 – open-ended" style), TV Basic in In Design; search "net", the status
   filter, a search that matches nothing.
9. **Catalog -> + New offering**: the validation messages (type an existing code such as
   mob-l, a price of 0, a "Valid to" before "Valid from"); Create stays disabled until
   complete; after creating, the new row is highlighted and the toast reads "Offering X
   created in In Design".
10. **Catalog row -> details**: TV Basic (In Design) -> Activate (confirm) -> Active; an
    Active offering -> Retire (warning copy) -> Retired; a Retired one offers nothing.
    Note: these change the seeded demo data (the API cannot delete).
11. **Catalog row -> details -> Change price** (Mobile L): the copy about existing
    products and new orders, a date today (refused), then a real change from a later
    date; the dialog closes and the toast names the new price and date. Ordering that
    offering afterwards still works (the price chain stays gapless).
7. **Error states**: with web-dev running, `docker compose stop app`, reload (context
   error with the call and Retry), `docker compose start app`, Retry. Loading skeletons:
   throttle the network in devtools.
