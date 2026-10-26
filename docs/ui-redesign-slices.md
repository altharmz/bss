# UI redesign "Ledger" v2: slice tracker (planned 2026-10-26)

This file says **which slice is next and when it is done**. Every decision and each slice's
full spec are in [ui-redesign-design.md](ui-redesign-design.md) (section RN = slice RN).
The handoff is `design-ref/design_handoff_bss_admin_v2/` (read-only). The human delegated
every design decision on 2026-10-26. **No slice needs approval: build what the design
says.**

## How to run (Codex)

One Codex session per slice, one slice at a time, in order. The slices share the working
tree, the local stack and the pre-commit hook, so never run two at once.

To start a session, give it this prompt:

> Read CLAUDE.md, then docs/ui-redesign-slices.md, and run the first slice whose status is
> not `done`, following "How to run a slice". Stop after that slice's commit and push.

### Before the slice

1. `git status` must be clean on `master`, and `git log --oneline -3` must show the
   previous slice's commit. If either is not so, stop and report.
2. `make ps`. If the stack is down, `make up`. If the frontend container's
   `node_modules` volume is missing, `make web-install` (the only step that downloads).
3. For slices that touch screens, the local data must come from `make seed`. If the
   database is empty, run `make migrate && make seed`.

### How to run a slice

1. **Read** CLAUDE.md, then from the design: "Rules for every slice", "When the spec is
   wrong", "Decisions", and your section RN. Then read the artboards and the code the
   section names.
2. **Build** it, with new tests (each must fail without the change). Iterate with targeted
   runs:
   - `docker compose run --rm --no-deps web-offline sh -c 'sh scripts/check-deps.sh && npm run types && npx vitest run src/<path>'`;
   - `make web-test`, `make web-lint` and `make web-fmt` as needed. Node only in
     containers.
3. **Look** at it: `make web-shots` (from R1 on). Check `.shots/*-1280.png` and
   `*-1600.png` of the touched screens against the artboard, and fix what differs.
4. **Docs:** follow design rule 8 and set this file's row to `done: <commit subject>`.
5. **Commit:**
   - one commit: code, tests, docs, the tracker row;
   - run `make web-fmt` first, then `git commit`. The pre-commit hook runs lint and tests:
     that is the one full test run;
   - never use `--no-verify`;
   - add a `TEST-CHANGE: <reason>` line for each existing test changed by design;
   - end the message with `Co-Authored-By:` only if your tool requires it.
6. **Push** with `git push`. **Never deploy** (`make deploy`, `make verify`): the human
   decides.
7. **Report:**
   - the slice id and the commit subject;
   - the hook's test counts;
   - each deviation from the design and why;
   - anything you left for a later slice.

### When it does not go well

- **The hook fails:** fix the code and commit again; never weaken a test. After three
  failed hook runs on the same slice, stop and report the output.
- **The slice is too large:** split it into `RNa`/`RNb` rows here, finish and commit `RNa`,
  and stop.
- **Stop and ask the human only for:** a backend or API change, a migration, a billing
  change, or a contradiction between the design and a test that asserts behaviour.

Status: `todo` · `done: <commit subject>` · `split`

## Slices

| # | Slice | Done when | Status |
|---|---|---|---|
| R1 | Tokens, fonts, `make web-shots`, hex/px guard test | v2 tokens and fonts live; legacy aliases keep every screen working; shots written; the guard test has an allow-list | todo |
| R2 | Primitives: Button, fields, Segmented, StatusPill (D4), Tag/Flag/Badge, Amount, Id | Every D4 mapping tested; `/__kit` (dev only) matches Components buttons/fields/pills | todo |
| R3 | Primitives: Table, list states, Tabs, Menu, Note, KeyValue, MoneyStrip, PageHeader, Panel, Dialog + write states, Toast | Keyboard tests for Tabs/Menu/rows; Footer refused/unsure notes; States.dc.html matches on `/__kit`; all existing tests green | todo |
| R4 | Shell: sidebar, top bar, business-date chip, avatar menu | Chip drift states tested with a fixed time; sidebar matches the artboard | todo |
| R5 | Subscribers list | Filters, over-limit flag, `/` focus, row menu; matches Subscribers.dc.html at 1280 | todo |
| R6 | Subscriber card: header, money strip, tabs | Every existing panel reachable in its tab; hash deep links; both strip variants | todo |
| R7 | Subscriber card: Overview (products, pending sub-row, in-progress steps, activity rail) | Matches SubscriberCard.dc.html at 1280 and 1600 | todo |
| R8 | Billing runs | Latest-run band, outcomes with Failed default, history selection; "Retry N failed" only when failed | todo |
| R9 | Product catalog and offering detail | Lifecycle groups, active-products count from one request, stepper detail | todo |
| R10 | Add product dialog | L layout; shortfall makes Top up primary and Add disabled; server preview unchanged | todo |
| R11 | Money dialogs: transfer, top-up, adjust, hold, record payment | Transfer 4 steps with acknowledgement; outcome labels; write states | todo |
| R12 | Sweep: Products, Orders, Numbers, Parties, subscriber forms | No legacy token, no inline style, no `Pill` left in these files | todo |
| R13 | Sweep: Prepaid commands, Accounts, Bills, Payments, Balances, Billing settings | Same as R12 for these files | todo |
| R14 | Sweep: Integrations, Users, sign-in, password, not found; delete legacy layer; dark theme | `legacy-tokens.css` gone; allow-list empty; Theme menu works; dark shots | todo |
| R15 | Inspector ≥ 1536 px (Subscribers, Catalog) | matchMedia tests; matches the -1600 artboards | todo |
| R16 | Visual QA, e2e, release evidence | `make web-e2e` green; `docs/ui-redesign-release.md` written; CLAUDE.md state updated | todo |
