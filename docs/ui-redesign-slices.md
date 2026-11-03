# UI redesign "Ledger" v2: orchestration and slice tracker (final, 2026-10-26)

All decisions are made in [ui-redesign-design.md](ui-redesign-design.md): section "Slice N"
specifies slice N. The handoff is `design-ref/design_handoff_bss_admin_v2/` (read-only).
**Nothing needs the human's approval.** The human only starts the run and deploys
afterwards.

> **2026-10-28: the slice tracker is the task list of epic #43** (R7b-R16 interleaved with
> the GUI review fixes G4b-G15, one GitHub issue per slice). Done slices are in `git log`.
> D13 (currency) in the design applies to every remaining row.

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
4. Find the first open slice issue in the task list of epic #43. That is where you start,
   and it is also how an interrupted run resumes.

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
- deploy (`make deploy`, `make verify`);
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
