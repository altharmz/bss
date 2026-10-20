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

Refund/bill-delivery milestone (2026-10-16): the balance rule below is extended by
subtracting completed cash payouts (`refund`), separately from negative refund ABRs.
On-demand bills are also written only by the billing owner, from posted ABRs.
See [billing-completion.md](billing-completion.md) for the approved semantics.

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md).

## Domain model & decisions

- **SID for the database, TMF Open API v5 for the surface** (TMF676 Payment only exists
  as v4, so v4 there). Specs live in `spec/`, pinned by commit; see `spec/README.md`.
- **Orders are the only way to create or terminate products.** `product.created_by_item_id`
  is NOT NULL; termination records `terminated_by_item_id`.
- **Only the billing run writes ABRs** (`applied_customer_billing_rate.billing_run_id` is
  NOT NULL).
- **Half-open ranges everywhere.** `valid_for` is `tstzrange [start, end)`, NULL upper =
  open-ended (TMF TimePeriod without endDateTime). Billing periods are `daterange [start,
  end)`. `product.termination_date` is exclusive. Billability is driven by dates, not
  status: `start_date <= d < termination_date`, excluding only `cancelled`/`aborted`.
- **Enums verbatim from the spec**, camelCase included (`pendingActive`). One exception
  rule: values are compared after `strip()` - upstream `ProductStatusType` has the typo
  `'aborted '`, stored as `aborted`. `tests/test_enum_lock.py` locks TMF-derived enums
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
- **`frontend/`** is planned (stage 5): a separate directory built to static files and
  served by the shared Caddy.
- Stage 1 runs the migration as a NOSUPERUSER database owner in a test, like vps-infra's
  `make add-db` does; `btree_gist` works because it is a trusted extension (PG13+).
