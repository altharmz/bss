# Reporting

Index: [README.md](README.md).

## Prepaid KPIs by month (2026-11-15, issue #198)

An idea from the MVNO scoping (epic #182). The design was decided under the human's standing
delegation and recorded on the issue. TMF has no reporting API for business KPIs, so this is
an internal read, like the usage summary. **It is read-only and computed on request from
existing tables: no migration, no new table.**

**Operation:** `GET /internal/v1/report/kpi?from=YYYY-MM&to=YYYY-MM&currency=XXX`
(`app/internal/reports.py`, the queries are in `app/repository/reports.py`; contract
`KpiReport` in `spec/internal/bss-internal.oas.yaml`). It reports calendar months of business
dates in BILLING_TZ:

- `to` defaults to the business month and `from` to 11 months before `to`, and a request
  covers at most 24 months. A month that is not `YYYY-MM`, `from` after `to`, `to` after the
  business month, a currency that is not three capitals and an unknown parameter are each a
  400. `currency` defaults to `UI_CURRENCY`.
- There is one row per month, oldest first: `month`, `firstDay`, `lastDay` (the business date
  for the current month) and `complete` (false for the current month).
- The MCP read tool is `kpi_report`. The admin UI screen is **Reports → Prepaid KPIs**
  (`/reports`, `data-ui="ui.reports"`); its range buttons ask for 6, 12 or 24 months.

**Definitions.** Only the prepaid accounts in the currency count; postpaid is not extended
(epic #182).

- **Line (subscriber):** a product of a prepaid account that is not an add-on
  (`parent_product_id` null) and is not `cancelled` or `aborted`, with
  `start_date < termination_date` or no termination date. It is live on day d when
  `start_date <= d < termination_date`, billing's own half-open rule
  ([data-model.md](data-model.md)). A suspended line (grace, lifecycle or operator) still
  counts; it ends at deactivation. Add-ons are packs on a line, not lines. The offering kind
  is not split out: plan and pay-as-you-go lines count alike.
- **opening** is the number of lines live on the day before the month, and **closing** the
  number live on `lastDay`.
- **grossAdds** counts lines whose `start_date` is in the month. **disconnects** counts lines
  whose `termination_date` is in the month: a line is gone on its termination date, whether
  it ended by lifecycle deactivation, voluntary termination or port-out.
- **netAdds** = grossAdds − disconnects. The invariant closing = opening + netAdds holds, and
  a month's opening is the previous month's closing; the endpoint carries the closing forward
  from one opening count. **average** = (opening + closing) / 2.
- **churnRate** = disconnects / opening × 100, a percentage rounded half-even to 2 decimals.
  It is null when opening is 0.
- **revenue** is tax-excluded Money from the ABRs of those accounts, dated by the run date of
  their billing run (the date the transaction ledger uses; a prepaid command's run keeps its
  authorization date). It is split into four sources:
  - `plans`: recurring charges less their refunds
  - `addOns`: one-time fees of add-on products
  - `usage`: rated pay-as-you-go usage (`usage_command_id`)
  - `fees`: every other one-time fee (activation, transfer)

  This is **billed revenue, not deferred or recognised revenue**: a plan bought on the 30th
  counts in full in that month.
- **arpu** = revenue total / average, rounded half-even to 2 decimals. It is null when the
  average is 0.

**Cost.** Both queries read only the range: products of prepaid accounts through
`billing_account`, and ABRs joined to their run's `run_date`. They have not been measured on
the 100k load-test dataset. If that read proves slow, stored monthly snapshots are the next
step, as their own issue.

**Not here:** postpaid KPIs, splits per offering or kind, churn by cause, stored snapshots and
export.

**Tests:** `tests/test_kpi_report.py` inserts prepaid, postpaid and other-currency accounts
whose lines start and end around three months, plus an add-on, a cancelled product, a line
that was never live and a start after today. Its ABRs cover every source, include a refund
and run on dates inside and outside the range. The tests check the counts and the invariant,
churn and ARPU with their nulls, the revenue split, the defaults and the 400s.
`frontend/src/screens/Reports.test.tsx` checks the tiles, both tables newest first, the range
request and the error state. `tests/test_mcp_tools.py` calls the tool.
