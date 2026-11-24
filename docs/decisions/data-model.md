# Data model and cross-cutting domain decisions

## Currency label rename (2026-10-20)

The human requested RUB renamed to USD everywhere, without conversion. USD is now
the UI and new-account default. Migration `0031` relabels stored currency columns
and nested Money documents, including historical charges, quotes, event payloads
and base64 JSON bill documents;
all numerical amounts stay unchanged. Other currencies stay unchanged. Foreign key
checks are deferred during the atomic relabel and their original timing restored.
Conflicting price timelines cause the migration to fail rather than discard records.
Downgrade keeps USD labels because original USD and renamed RUB cannot be distinguished.
Design references and past migrations remain immutable. Test examples use USD, with
GBP replacing previous USD examples where a distinct second currency is needed.

The first deployment rolled back because recorded refunds reject all updates.
The human approved correcting `0031` after it reached master: the migration disables
only `refund.refund_immutable` within its transaction and restores it before commit.
The regression fixture includes a payout and checks both its unchanged amount and
the restored rejection of subsequent edits.

Refund/bill-delivery milestone (2026-10-16): the balance rule below is extended by
subtracting completed cash payouts (`refund`), separately from negative refund ABRs.
On-demand bills are also written only by the billing owner, from posted ABRs.
See [billing-completion.md](billing-completion.md) for the approved semantics.

Index: [README.md](README.md).

## Domain model & decisions

- **SID for the database, TMF Open API v5 for the surface** (TMF676 Payment only exists
  as v4, so v4 there). Specs live in `spec/`, pinned by commit; see `spec/README.md`.
- **Orders are the only way to create or terminate products.** `product.created_by_item_id`
  is NOT NULL; termination records `terminated_by_item_id`.
- **Only the billing run writes ABRs** (`applied_customer_billing_rate.billing_run_id` is
  NOT NULL).
- **ABR rules are scoped by `charge_type`** (migration `0047`). `recurring` and `refund`
  require a product and an offering; a `oneTime` rate (a prepaid transfer's fee, TMF678
  `appliedBillingCharge`) has neither, carries a unique `balance_action_id` as its
  identity (one fee per transfer), is positive and covers one day (`abr_fee_scope`,
  `abr_fee_amount`). `abr_one_per_start` and `abr_grant_identity` ignore null products
  (NULLs are distinct), which is right: a fee is unique by its action. Code that joins an
  ABR to its product or offering must use a left join.
- **Half-open ranges everywhere.** `valid_for` is `tstzrange [start, end)`, NULL upper =
  open-ended (TMF TimePeriod without endDateTime). Billing periods are `daterange [start,
  end)`. `product.termination_date` is exclusive. Billability is driven by dates, not
  status: `start_date <= d < termination_date`, excluding only `cancelled`/`aborted`.
- **Enums verbatim from the spec**, camelCase included (`pendingActive`). One exception
  rule: values are compared after `strip()` - upstream `ProductStatusType` has the typo
  `'aborted '`, stored as `aborted`. `tests/schema/test_enum_lock.py` locks TMF-derived enums
  against `spec/`. Where the spec field is a free string we define our own enum ("ours,
  not TMF" in the migration); casing: if the spec gives example values, use their exact
  spelling (`In Design`, `Active`), otherwise lowerCamel.
- **No balance column.** Balance = payments (`done`) - sum of all ABRs; refunds are stored
  negative, so they already count as credits. Since 2026-10-18 an ABR counts with its
  taxes (`amount + tax_amount`).
- **Money** is `numeric(12,2)` / `Decimal`, never float. One currency per billing account,
  enforced by composite FKs to `billing_account (id, currency)`. Taxes: a price's tax
  items, applied per ABR since 2026-10-18 ([billing-core.md](billing-core.md#taxes-2026-10-18)).
- **Injected clock.** Nothing in `app/` may call `datetime.now()`, `date.today()`,
  `datetime.utcnow()` or `time.time()` except `app/clock.py` (a test greps for it).
  "Today" for billing is the date in `BILLING_TZ` (default `Europe/Moscow`). Repository
  functions take `now`/`today` as arguments.
- **`bill_cycle_day` 1..28** with calendar-aligned billing periods (no month has fewer
  than 28 days, so every cycle day exists every month).
- **Exclusion constraints** (prices, ABRs, bills) forbid overlapping ranges, not just equal
  ones. `ON CONFLICT` with a conflict target does not work with exclusion constraints;
  only a bare `ON CONFLICT DO NOTHING` does.
- **Out of scope for now:** taxes, `modify` items and cancelProductOrder. A `modify` item
  is still storable - order processing rejects it with a reason instead of the schema
  raising an IntegrityError. **cancelProductOrder came into scope on 2026-10-17**
  ([tmf-api.md](tmf-api.md#order-cancellation-and-deleting-rejected-orders-2026-10-17)).
  **Taxes came into scope on 2026-10-18** ([billing-core.md](billing-core.md#taxes-2026-10-18)).
- **Stage order:** 1. schema + repository (done) -> 2. TMF Open API backend (done) -> 3.
  billing core (done) -> 4. worker with the daily run (deployed with slice f, `dcbedeb`,
  2026-10-05; slice g, the Billing runs screen, built on `cloud/billing-runs-ui`, not yet
  deployed) -> 5. frontend. Bill and ABR
  endpoints are read-only; the worker's daily run writes them through `bill_account`
  (see ["Billing worker"](worker.md#billing-worker-stage-4-in-progress)).
- **Rounding:** banker's rounding (`ROUND_HALF_EVEN`) to 2 decimals, applied once per ABR,
  never on intermediate values.
- **Billable days:** the activation day is billed, the termination day is not. This is the
  existing half-open `[start_date, termination_date)`: a product terminated "today" has
  `termination_date = today`, so today is not charged. A product activated and terminated
  on the same day is charged 0 days.
- **`frontend/`** is a separate directory built to static files and served by the shared
  Caddy.
- Stage 1 runs the migration as a NOSUPERUSER database owner in a test, like vps-infra's
  `make add-db` does; `btree_gist` works because it is a trusted extension (PG13+).

## Retention (2026-11-11, #282)

Several tables grew with subscribers x time and had no retention: at the 100k target
`usage_record` gains 2M rows a day and `billing_run_account` 100k. The design was
approved by the human on 2026-11-09; the growth inventory of the 100k dataset
(`make loadtest-growth`) is in the PR.

- **Kept forever** (financial or legal record, never deleted): `applied_customer_billing_rate`,
  `customer_bill` and its state changes and allocations, `payment`, `refund` and its
  sources, `tax_transaction`, `balance_action` and reversals, every `funding_*` table,
  `usage_charge`, every `prepaid_*` table, `payment_command`, `product*`, `party`,
  `customer`, `billing_account`, `account_audit`, `app_user_audit`, `billing_run`, and the
  non-ok rows of `billing_run_account` (they drive `blocked`).
- **Deleted by the worker's housekeeping** (`app/repository/housekeeping.py` RULES):

  | Table | Rule |
  |---|---|
  | `billing_run_account` | `ok` rows of completed runs with `run_date` more than 90 days back; the counts stay on `billing_run` (#284). The accounts list of such a run is then empty |
  | `app_session` | absolute (`expires_at`) or idle expiry more than 1 day past |
  | `idempotency_key` | `created_at` more than 30 days back, tombstoned or not: a replay after 30 days is a new request |
  | `event_delivery` | finished (not pending) more than 30 days ago |
  | `event` | `occurred_at` more than 90 days back and no delivery refers to it |
  | `worker_request` | done more than 90 days ago |
  | `notification_message` | created more than 13 months ago; one that a kept resend refers to (`resend_of`) stays until the resend goes |
  | `sign_in_attempt` | already self-cleaning (#288) |
  | `usage_record` | partitions older than 13 months are dropped ([usage.md](usage.md#partitions-2026-11-11-282)) |

- **Housekeeping** (`app/worker/housekeeping.py`, step 8 of the wake-up): from 04:00 to
  05:30 BILLING_TZ (after the run, the 02:15 backup and the 03:00 reboot), only in the
  worker that owns billing, never while a billing run is `running`. Batches of at most
  5000 rows, one transaction each, oldest first, 100 ms apart; each logs its table and
  count. It stops at 05:30 and continues the next day. **Added in implementation:** a
  wake-up gives it at most 45 s (`TICK_BUDGET`), so the usage feed and the event
  deliveries keep their one-minute cadence during a long first clean-up; a table with
  nothing left costs one indexed query per wake-up. Indexes for the batches (migration
  0076): `idempotency_key (created_at)`, `event_delivery (finished_at)` of finished
  deliveries, `notification_message (created_at)` and `(resend_of)`. Migration 0077 (#310)
  replaces `notification_message (created_at)` with `(created_at DESC, id)`, which the
  batch reads backwards.
- **`idempotency_key` stays protected:** its trigger still refuses every delete except in a
  transaction that set `bss.housekeeping = 'on'` (only the housekeeping does).
- **The old event purge is gone:** `deliver.purge` deleted an event with its deliveries 30
  days after they all finished. Deliveries and events now follow the two rules above.
- **Autovacuum:** `autovacuum_vacuum_scale_factor = 0.02` and
  `autovacuum_analyze_scale_factor = 0.01` on `billing_run_account`, `app_session`, `event`,
  `event_delivery`, `idempotency_key` and every `usage_record` partition.

## Indexes for hot paths, list sorts and references (2026-11-14, #310)

From the EXPLAIN review of the 100k load-test dataset and the SQL plan sweep (#327);
migration 0077, each index EXPLAINed before and after in the PR.

- **What gets an index:** a query the worker runs every wake-up or for every billed
  account; an admin list's exact `ORDER BY` (a mixed `created_at DESC, id` needs
  `(created_at DESC, id)`; `DESC, DESC` reads `(created_at, id)` backwards); and every
  foreign key whose parent can be deleted or whose PATCH guard asks whether it is
  referenced. Partial where the query's own condition is selective (a state, a kind, a
  non-null reference).
- **A wider index replaces a narrower one** when it serves the old queries too:
  `event_delivery (subscription_id)` became `(subscription_id, last_attempt_at DESC, id
  DESC)` and `notification_message (created_at)` became `(created_at DESC, id)`.
- **Presence guards ask EXISTS:** `is_referenced` (`app/repository/_sql.py`) stops at the
  first row. `count_references` stays for the delete guards, whose 409 names the counts.
- **Not indexed:** the Balances screen's status filter (computed from the account state:
  the join, not an index, decides it) and the product-by-number semijoin (an existing
  index; the planner's join choice). The numbers list no longer sorts by value.

## Queries an index alone could not fix (2026-11-15, #311)

From the same EXPLAIN review (100k dataset); migration 0079, each query EXPLAINed before
and after in the PR.

- **The latest grant of a product** is the grant no later grant of the product follows
  (`entitlement.LATER_GRANT`: later by first day, then authorization, then the
  superseding grant before the one it supersedes, then id). A superseding grant may start
  on the superseded grant's first day and, under a test clock, share its authorization
  time, so every "latest grant" `ORDER BY` now ends with the same two tiebreaks: one total
  order, never an arbitrary pick. The renewal producer
  and the plan-expiring detector read the grants ending by today or in the notice window
  (`funded_interval (upper(period))`) and keep the latest of them, instead of a
  DISTINCT ON over every grant. The producer still reads every grant ended by today, so
  it grows with the grant history, but without the sort; a bound needs renewal state
  stored on the grant, a data-model change left for later.
- **A uuid filter is compared as a uuid.** `_sql.conditions` is the one typed filter
  builder (`select_page`, the TMF654 balance-action lists, the resource inventory); the
  inventory's derived states are text. A renewal's grant is looked up by `id = ANY(uuid[])`.
- **`/metrics` queue counts** put each condition in WHERE (`metrics.queue_query`), so a
  partial index answers it; 0079 adds the missing ones for the failed rows.
- **Not changed:** `/metrics` products by status (100 ms on 115k products, every 30 s;
  an index-only scan of a status index was no faster), the billing runs list (stored
  counts since #284: 0.3 ms) and the resource list's `allocationStatus`/`usageState`
  filters, which derive the state of every number before filtering (3.4 s at 115k
  numbers): that needs the state stored or a different filter shape.
