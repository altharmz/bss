# UI redesign "Ledger" v2: orchestration and slice tracker (final, 2026-10-26)

All decisions are made in [ui-redesign-design.md](ui-redesign-design.md): section "Slice N"
specifies slice N. The handoff is `design-ref/design_handoff_bss_admin_v2/` (read-only).
**Nothing needs the human's approval.** The human only starts the run and deploys
afterwards.

## Start (the human pastes this into Codex)

> Read CLAUDE.md, then docs/ui-redesign-slices.md, and act as the orchestrator described in
> its "Orchestrator" section until every slice is done or a stop condition is reached.

---

## Orchestrator

You are the **main agent**. You track the slices and delegate each one; you never write
code, tests or docs yourself, apart from the one tracker fix described in step 3.

### Before the first slice

1. Read CLAUDE.md and this file. From the design, read only "Rules for every slice" and
   "When the spec is wrong".
2. Run `git status` and `git log --oneline -5`. The tree must be clean on `master`; if it
   is not, stop and report.
3. Run `make ps`. If the local stack is down, run `make up`. Then run `make migrate` and
   `make seed` (seed is idempotent: it does nothing when its data exists).
4. Find the first row of the table below whose status is not `done`. That is where you
   start, and it is also how an interrupted run resumes.

### For each slice, strictly one at a time

1. **Start a new subagent with a clean context** for slice N. Give it the "Subagent
   prompt" below with N filled in, and nothing else from your context. **Wait until it
   finishes.** Never start the next subagent while one is running: the slices share the
   working tree, the local stack and the pre-commit hook.
2. **Verify** what it reports. Do not trust the report alone:
   - `git log --oneline -3` shows a new commit for slice N, and `git status` is clean.
   - `git status -sb` shows no "ahead": the push happened.
   - `git show --stat HEAD` contains changed or new test files under `frontend/src`, and
     this file with row N changed to `done: <commit subject>`.
   - `git show --stat HEAD` contains no file under `app/`, `alembic/`, `design-ref/` or
     `tests/test_ui_requests.py`.

   A commit can exist only if the pre-commit hook (lint and the full test suite) passed.
   That hook run is the slice's verification. Do not run the tests again.
3. **If the checks pass,** tell the human in one line: slice N, the commit subject, the
   test counts from the subagent's report, and any deviation it recorded. Then continue
   with the next row that is not `done`. If row N is marked `done` but the commit lacks
   the tracker change, edit the row, amend nothing, and commit it with the next slice.
   Tell the next subagent so.
4. **If the slice failed** (no commit, the hook failed, the subagent stopped, or a check
   above failed):
   - start **one** fresh subagent with the same prompt, plus the first subagent's report,
     the failure output and `git status`;
   - tell it to continue from the current tree and not to discard work;
   - if this second attempt also fails, stop and report to the human.
5. **If the subagent split the slice** (rows `Na`/`Nb` appear, with `Na` done), continue
   with `Nb` as its own subagent.

### Stop and ask the human only when

- a subagent reports that the slice needs a backend/API change, a migration or a billing
  change;
- two attempts at the same slice failed;
- the tree is not clean before a slice starts, and the cause is not the previous slice's
  work.

### Never

- run two subagents at once;
- write code yourself;
- deploy (`make deploy`, `make verify`, `/ship`);
- force-push;
- use `--no-verify`;
- edit `design-ref/`.

---

## Subagent prompt (one slice)

> You implement **slice N** of the UI redesign in this repository, and only that slice.
>
> 1. Read CLAUDE.md. Then in docs/ui-redesign-design.md read "Rules for every slice", "When
>    the spec is wrong", all of "Decisions", and "Slice N". Then read
>    design-ref/design_handoff_bss_admin_v2/HANDOFF.md, the artboards slice N names, and
>    docs/patterns.md. Open other code only as you need it.
> 2. Implement slice N exactly as specified. Do not make design decisions. If something is
>    not covered, follow "When the spec is wrong".
> 3. Add the tests the slice lists. Each must fail without your change. While working, run
>    targeted tests:
>    `docker compose run --rm --no-deps web-offline sh -c 'sh scripts/check-deps.sh && npm run types && npx vitest run src/<path>'`.
>    Node, npm and npx run only in containers. Never run them on the host.
> 4. From slice 1 on, run `make web-shots`. Look at the PNGs in `frontend/.shots/` for every
>    page you touched, at 1280 and 1600, compare them with the artboard, and fix what
>    differs. (In slice 1 itself, run it once the target exists.)
> 5. Update the docs per design rule 8. In docs/ui-redesign-slices.md, set row N's status to
>    `done: <commit subject>`.
> 6. Run `make web-fmt`, then `git add` your files and `git commit`.
>    - The subject is one imperative line naming the slice's result.
>    - The body has one `TEST-CHANGE: <reason>` line per existing test you changed by
>      design.
>    - The pre-commit hook runs lint and the full test suites; that is the verification.
>      Never use `--no-verify`.
>    - If the hook fails, fix the cause (never weaken a test) and commit again. After three
>      failed hook runs, stop and report the last output.
> 7. `git push`. Never deploy.
> 8. If the slice is too large to finish, split it. Change row N into `Na` (what you
>    finished) and `Nb` (the rest, with its "done when"). Commit and push `Na`, and report.
> 9. Report, in this order:
>    - the commit subject and hash;
>    - the hook's test counts (backend if it ran, frontend in UTC and New York);
>    - the TEST-CHANGE lines;
>    - any deviation from the design and why;
>    - anything left for a later slice.
>
>    If you stopped, say why and what remains.

---

## Slices

Status: `todo` · `done: <commit subject>` · `split`

| # | Slice | Done when | Status |
|---|---|---|---|
| 1 | Tokens, fonts, legacy aliases, style guard test, `make web-shots` | v2 tokens and fonts live; every screen works through the aliases; the guard test passes; shots are written | done: Establish Ledger tokens and visual reference captures |
| 2a | Button, fields, Segmented, initial `/ui-kit` | Busy Button, radio Segmented and native field behavior tested; controls gallery at both widths | done: Build Ledger buttons, fields and segmented controls |
| 2b | StatusPill with explicit kinds, Tag/Flag/Badge, Amount, Id, screen migrations, complete `/ui-kit` | Every D4 row tested; every Pill caller migrated and Pill/old status tokens deleted; danger-outline callers migrated and compatibility removed; Amount and Id adopted per slice 2; gallery covers every D4 status, amounts, ids and tags; all touched screen inline styles removed | todo |
| 3 | Table, list states, Tabs, Menu, Note, KeyValue, MoneyStrip, PageHeader, Panel, Dialog, write states, Toast | Keyboard tests for Tabs, Menu and rows; Footer refused and unsure notes; `/ui-kit` matches Components and States | todo |
| 4 | Shell: sidebar, top bar, business-date chip | Chip drift states tested from the context; sidebar matches the artboard | todo |
| 5 | Subscribers list | Search and `/`, filters with counts, over-limit flag; matches Subscribers.dc.html at 1280 | todo |
| 6 | Subscriber card: header, money strip, tabs | Every existing panel in its tab with markers kept; hash deep links; both strip variants | todo |
| 7 | Subscriber card: Overview (products table, pending sub-row, In progress steps, activity rail) | Matches SubscriberCard.dc.html at 1280 and 1600 | todo |
| 8 | Billing runs: header actions, latest-run band, inline outcomes, history selection | "Retry N failed" only for a failed latest run; outcomes inline; requests unchanged | todo |
| 9 | Product catalog: lifecycle groups, offering detail with stepper, other tabs restyled | Groups, search and stepper tested; matches Catalog.dc.html | todo |
| 10 | Add product dialog | L two-column layout; shortfall makes Top up primary and Add disabled | todo |
| 11 | Money dialogs: transfer (3 steps), top-up, adjust, hold, record payment | Acknowledgement gates the transfer; outcome labels; write states | todo |
| 12 | Sweep 1: Products, Orders, Numbers, Parties, subscriber forms | No inline style or old pattern left in these files; kinds tested | todo |
| 13 | Sweep 2: Prepaid commands, Accounts, Bills, Payments, Balances, Billing settings | The same for these files | todo |
| 14 | Sweep 3: Integrations, Users, sign-in, password, not found; delete the legacy layer; dark theme | `legacy-tokens.css`, `Modal` and `PageTitle` gone; guard (d)(e) pass; Theme menu works; dark shots | todo |
| 15 | Inspector at ≥ 1536 px (Subscribers, Catalog) | matchMedia tests; no new requests; matches the -1600 artboards | todo |
| 16 | Visual QA, e2e, release evidence | `make web-e2e` green; `docs/ui-redesign-release.md` written; CLAUDE.md updated | todo |
