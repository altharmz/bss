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
| First-touch timing outliers requiring reproduction | [#334](https://github.com/altharmz/bss/issues/334) |

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
