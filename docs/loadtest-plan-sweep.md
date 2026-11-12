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
