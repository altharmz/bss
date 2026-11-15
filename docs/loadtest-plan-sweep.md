# SQL plan sweep results (#327)

Measured on 2026-11-12 with the repeatable [plan sweep](loadtest.md#sql-plan-sweep-327),
using the restored `seed100k` dataset: 100,000 accounts, billing dates 2026-10-05
through 2026-11-04. PostgreSQL 16.15 ran on the local WSL2 workstation, SUT cpuset
4-7, shared_buffers 2GB, work_mem 16MB and JIT enabled. These are offline SQL
diagnostics; they do not measure concurrent capacity or production sizing.

| Result | Count |
| --- | ---: |
| Full-suite tests passed during capture | 2,598 |
| Application statement shapes | 989 |
| Successfully analyzed | 898 (90.80%) |
| Successful in all four profiles | 897 |
| Successful in some profiles | 1 |
| Unbound, with errors retained | 91 |
| Measured shapes using a typed UUID proxy | 126 |
| Flagged in the final repeated measurement | 68 |
| Statistics evictions | 0 |

The four profiles are the median-id account, the account with most lines, a prepaid
account and a postpaid account. Real related rows, foreign keys and dataset dates
are sampled. Typed UUID proxies exercise empty/missing-row paths when a feature
target cannot be resolved; they establish SQL execution coverage, not performance
with populated feature tables. One application capture could not be matched to
server statistics and remains explicitly included with a warning. There were 9,454
excluded fixture/utility entries, retained with reasons.

Every analyzed write rolled back. Sequence restoration was verified, and account
counts were 100,000 before and after. Failed constraints remain unbound; constraints
and triggers were never disabled. Commit-only deferred-trigger costs are outside
these statement timings.

Findings include source locations, originating tests, SQL, four profile timings,
buffers, plan excerpts and acceptance criteria:

| Finding | Follow-up |
| --- | --- |
| Existing index work and related list/FK plans | [#310](https://github.com/altharmz/bss/issues/310#issuecomment-6831344680) |
| Existing structural query work and estimate observations | [#311](https://github.com/altharmz/bss/issues/311#issuecomment-6831345374) |
| Registry joins, search and pagination | [#330](https://github.com/altharmz/bss/issues/330) |
| Catalogue affected-product lookup | [#331](https://github.com/altharmz/bss/issues/331) |
| Worker account selection and product transitions | [#332](https://github.com/altharmz/bss/issues/332) |
| Billing-run membership and outcomes | [#333](https://github.com/altharmz/bss/issues/333) |
| First-touch timing outliers requiring reproduction | [#334](https://github.com/altharmz/bss/issues/334): reproduced and dispositioned, no statement changed, [below](#first-touch-timing-outliers-334) |

The slowest final inventory-number query took 1,483 ms; registry joins reached
234 ms, lifecycle account selection 244 ms, catalogue impact lookup 157 ms and
billing outcome-gap lookup 153 ms. Flags are review candidates: a bulk scan may be
appropriate, and an estimate-only observation on an empty table is not a latency
defect. No application query is changed by this task.

The earlier fresh pass flagged 94 shapes; repeated, mostly warm measurement flagged
68. Findings seen only in the earlier pass are preserved in the linked issues,
including a separate reproduction task for timing-only outliers. Neither pass is
a controlled cold-cache benchmark.

Full per-shape JSON, every profile/error, normalized query IDs/calls, source/test
attribution, binding provenance and archived attempts are retained as
`plan-sweep-final2-20261112` under worker2's persistent operator state after task
finish (`/home/petrg/projects/bss-agents/worker2/state/issue327-plans/`).
The generated output is intentionally outside version control; the linked findings
retain the reviewable SQL and measured excerpts.

## Worker account selection and product transitions (#332)

Re-measured on 2026-11-14 on `seed100k` restored and migrated to head (0076), warm
cache, the same lt-db settings. Before/after are the second of two runs. Query results
were compared on the dataset: identical (69,450 lifecycle accounts; 524 offering-change
rows over a widened window, since the run's own window has none).

| Shape | Where | Before | After | Disposition |
| --- | --- | ---: | ---: | --- |
| 5 | `app/worker/lifecycle.py` `ACCOUNTS` | 210.5 ms | 151.3 ms | Rewritten as a semi-join (`EXISTS`): the hash aggregate over every open line goes. The full pass runs once per business date (#292; later ticks pass the reviewed accounts) and then works ~70k accounts in their own transactions, so the two remaining scans of `billing_account` and `product` are intended. Not indexed: a partial index would save ~100 ms once a day. Budget: once per tick |
| 13 | `app/repository/products.py` `announce_changes` | 83.6 ms | 1.7 ms | `NOT IN (prepaid accounts)` became `IS NOT NULL AND NOT EXISTS`: the plan now starts from the pending changes (109 rows) instead of scanning `product_order` against a hashed set of 70k ids. NULL accounts stay excluded, as before. The 200x estimate error was on an empty result: estimate-only |
| 31 | `app/repository/products.py` `flip_statuses` | 40.9 ms | 29.2 ms (unchanged SQL) | Accepted: once per billing run, within its 100 ms budget. Two scans of `product` by status that find nothing due on the dataset; a partial index is not worth a migration at this cost |
| 52 | `app/worker/prepaid.py` entitlement accounts | 19.0 ms | 17.0 ms (unchanged SQL) | Reclassified: an index-only scan run once per pass, now `_entitlement_accounts`, so the sweep applies the once-per-tick budget. Measured under the per-account default before |

The sweep's cadence list (`loadtest/plan_measure.py`) now includes `process_lifecycle` and
`_entitlement_accounts`; shape 5 was measured against the 5 ms per-account default before.

## First-touch timing outliers (#334)

Reproduced on 2026-11-15 on `seed100k` restored and migrated to head (0078). The 24
statements of the issue were taken from a fresh full-suite collection (2,691 tests; all 24
unchanged in the code) and measured with `PHASE=measure` on that subset. That ran three
rounds: each from a fresh restore, then `lt-db` restarted (empty shared buffers), then
three passes. Pass 1 is cold; passes 2 and 3 are warm. Each pass is a new process, so a
new backend. The OS page cache could not be dropped (no root in WSL2), so "cold" means
cold shared buffers and a cold backend. The host was shared with two other tasks'
stacks (load average 13 on 12 CPUs), which accounts for the 5-9 ms single warm samples.

Result: pass 1 flagged 20-21 of the 24 every round; passes 2 and 3 flagged 1-7. The plans (node types, relations and indexes) were identical in every
pass for all 96 shape/profile pairs: only buffers and time differ. **No statement is
changed.** Times are the worst profile, in ms. Warm is the median / max over 24 samples.

| Shape | Source | #327 | Cold (3 rounds) | Cold reads | Warm median / max |
| --- | --- | ---: | ---: | ---: | ---: |
| 30 | `app/repository/_sql.py:170` payment list | 62.7 | 14.3-97.1 | 160 | 0.1 / 2.7 |
| 32 | `app/repository/_sql.py:170` billCycle list | 57.1 | 0.2-0.4 | 0 | 0.2 / 0.3 |
| 55 | `app/repository/billing.py` `forget_member` | 21.0 | 25.2-30.6 | 69 | 0.8 / 16.7 |
| 62 | `app/repository/prepaid_command_views.py:92` | 16.9 | 15.5-256.4 | 999 | 1.1 / 4.0 |
| 64 | `app/repository/_sql.py:170` productOrder list | 15.2 | 16.0-71.2 | 124 | 0.1 / 5.0 |
| 67 | `app/repository/_sql.py:222` order item parent | 12.6 | 10.5-13.1 | 47 | 2.2 / 6.5 |
| 69 | `app/repository/_sql.py:170` billCycle by date | 12.2 | 0.1-0.2 | 0 | 0.1 / 0.2 |
| 71 | `app/repository/balance_actions.py:128` | 10.9 | 14.3-20.4 | 41 | 1.9 / 7.8 |
| 72 | `app/repository/usage.py:276` | 9.8 | 10.0-15.5 | 69 | 0.6 / 2.8 |
| 73 | `app/repository/parties.py:42` | 9.1 | 6.8-11.4 | 26 | 1.1 / 2.0 |
| 75 | `app/repository/_sql.py:222` subscriber number | 8.9 | 6.9-9.8 | 19 | 0.6 / 1.5 |
| 76 | `app/repository/billing.py:1230` | 7.7 | 10.7-13.8 | 37 | 2.2 / 9.4 |
| 77 | `app/repository/_sql.py:170` party list | 7.3 | 6.2-11.8 | 99 | 0.1 / 8.0 |
| 80 | `app/repository/usage.py:271` | 7.1 | 4.3-5.2 | 0 | 1.6 / 4.2 |
| 81 | `app/repository/payments.py:60` | 7.0 | 6.5-8.0 | 21 | 1.5 / 3.9 |
| 82 | `app/repository/billing.py:1258` | 6.9 | 4.8-5.8 | 17 | 1.3 / 4.0 |
| 83 | `app/repository/_sql.py:170` customer list | 6.9 | 4.2-10.8 | 100 | 0.1 / 3.8 |
| 86 | `app/repository/balance_actions.py:214` | 6.3 | 7.6-9.9 | 19 | 4.0 / 8.6 |
| 65 | `app/repository/parties.py:125` | 5.9 | 8.3-9.5 | 48 | 1.8 / 3.2 |
| 87 | `app/repository/billing.py:534` | 5.7 | 11.6-15.9 | 74 | 1.2 / 3.4 |
| 88 | `app/repository/billing.py:373` | 5.6 | 6.3-9.0 | 16 | 1.6 / 3.1 |
| 89 | `app/repository/funding.py:186` | 5.4 | 7.1-8.7 | 27 | 2.6 / 8.6 |
| 90 | `app/ordering/numbers.py:380` | 5.3 | 8.2-12.0 | 55 | 0.8 / 2.0 |
| 92 | `app/repository/_sql.py:222` customer update | 5.1 | 4.7-6.8 | 30 | 0.4 / 1.1 |

Dispositions:

- **32, 69 - not reproduced; a binder value the app never sends.** The #327 fresh pass
  bound `kind = 'prepaidCommand'` (70,408 rows) and got a sequential scan. The only
  caller, `GET /billCycle` (`app/api/bill.py`), always filters `kind = 'daily'` (31 rows),
  which the partial unique index `billing_run_daily_date` serves. The current binder
  samples the latest daily run. Directly: `prepaidCommand` 34.7 ms (parallel seq scan),
  `daily` 0.28 ms (index scan). No index is added for a value no request produces.
- **30, 64, 77, 83 - cold-only, deep page.** Only the `largest` profile is slow: it binds
  OFFSET 5000, so the index scan reads 5,021 rows and their heap pages (99-160 blocks
  cold). Warm it takes 2-3 ms, and the other profiles (OFFSET 0) take 0.1 ms. OFFSET/limit
  is the TMF paging contract. The cost grows with the offset, not with the table.
- **62 - cold-only.** A bitmap heap scan of ~730 bills of a page of prepaid commands:
  999 blocks read cold (15-256 ms, depending on the OS cache), 1-4 ms warm.
- **55 - accepted, rare.** `forget_member` runs only when a billing account without
  history is deleted. It updates one `billing_run` row per daily run since the account
  opened (24 for `largest`, 0.3-1.7 ms for the others). The 9-17 ms warm `largest` samples
  read 15-16 new blocks each: page growth from the sweep's own repeated rolled-back
  updates of the same 24 rows, not a plan cost.
- **All the single-row writes (65, 67, 71-73, 75, 76, 80-82, 86-90, 92) - cold-only,
  first call in a backend.** The cold pass reads the index leaves and heap pages the
  write maintains (16-74 blocks). It also pays per-backend set-up: plpgsql compilation of
  the triggers (`account_review`, `credit_account_lock`, the ledger and retention
  triggers), the RI checks' SPI plans and the catalog caches. The sweep always measures
  `typical` first in each backend, which is why `typical` is the worst profile of most
  writes in #327. Shown directly on shape 86, with four inserts in one backend, three
  backends: the first took 30.6-84.3 ms (triggers 20.1-45.7 ms), the next 5.8-16.9 ms
  (triggers 1.6-3.4 ms). The app pool (`app/db.py`, psycopg_pool defaults) keeps
  connections for up to an hour, so a request pays this once per pooled connection, not
  per call. Warm medians are 0.4-4.0 ms. Shape 86's `typical` stays at 5.4-7.3 ms in the
  warm passes too: each pass is a new backend, and it is that backend's first INSERT
  into `balance_action`, so its RI plans are prepared in that call. The other 5-9 ms warm
  samples are single values under host contention.

The rounds' reports are in `loadtest/out/plan-sweep-334repro/round{1,2,3}/` of worker2
(ignored, not committed).
