# Wholesale reconciliation with the partner's invoices (issue #197, epic #182)

Index: [README.md](README.md). Related: [prepaid.md](prepaid.md) (lines and the partner),
[usage.md](usage.md) (usage records and number matching), [network.md](network.md) (the
placeholder pattern for the partner's systems).

An operator imports a wholesale invoice of the network partner (T-Mobile). The BSS compares it
with the lines and usage it knows for the invoice period, stores the discrepancies, and reports
cost, revenue and margin per line. The API is #197 and the operator UI is #381.
Design approved on the issue (2026-11-15, the human's delegation to decide when preparing
issues). Migration `0083`.

## Ours, not TMF

TMF has no wholesale reconciliation API, and a partner's invoice is not a TMF678 customer bill.
The API is our internal one, under `/internal/v1/wholesale`; contract in
`spec/internal/bss-internal.oas.yaml`.

## The file

Our own format until the partner's real one is mapped (#203): CSV, UTF-8, header
`msisdn,iccid,period_start,period_end,charge_type,quantity,unit,amount,currency`.
`app/integrations/network/wholesale.py` `parse_invoice(content) -> InvoiceLines` is the only code
that knows it; replacing the format is replacing that function.

- `msisdn` (E.164 digits) or `iccid` (18 to 22 digits), at least one.
- `period_start`, `period_end`: dates, the end **exclusive** (half-open, as every range here).
- `charge_type`: `line` (the monthly access charge), `data`, `voice`, `sms`, `other`; `unit`:
  `MB`, `minute`, `message` for the usage kinds, empty for `line` and `other`.
- `quantity` (empty = 0, up to 3 decimals) and `amount` (up to 2 decimals) are read as Decimal
  from their text, never through a float: `0.1` + `0.2` is `0.30`.
- One currency per file. The invoice period runs from the earliest line start to the latest end.

A problem is a 422 whose message names the row (1 = the first row after the header) and the
column. More than 200,000 rows or 64 MB is a 413.

**Upload as the raw body, not multipart (deviation from the issue).** The app has no multipart
parser, and adding one (`python-multipart`) is a new pinned dependency for one endpoint. The
upload is `POST /internal/v1/wholesale/invoice?invoiceNumber=...` with the file as the body,
`Content-Type: text/csv` (415 otherwise); the UI sends the file's bytes the same way.

## Data (migration 0083)

- `wholesale_invoice`: `invoice_number` (unique: the same number again is a 409; a corrected
  invoice is a new import under another number), `period`, `currency`, `total` (the sum of the
  lines), `line_count`, `state` (`imported` -> `reconciled`, both in the import's transaction),
  `imported_at`, `imported_by`.
- `wholesale_invoice_line`: the row as imported (`line_number`, `msisdn`, `iccid`, `period`,
  `charge_type`, `quantity`, `unit`, `amount`) and the matched `product_id` (null when none).
- `wholesale_discrepancy`: `kind`, `product_id`, `msisdn`, the `invoice_line_id` it is about,
  `usage_kind` (for `usageMismatch`), `expected`, `invoiced` and a `detail` document.
- Immutable: triggers refuse any update or delete of lines and discrepancies, and of an invoice
  once it is reconciled (only the `imported` -> `reconciled` step is allowed).

## Matching

An invoice line belongs to the product that held its MSISDN on the line's period start, from
number assignment history with cancelled claims ignored - as usage matching does - else to the
product whose SIM had its ICCID then (the IMSI resource's `iccid`). A number that moved between
products in the period maps each line to the holder on that line's start.

## Discrepancies

One INSERT ... SELECT per kind, in the import's transaction (`app/repository/wholesale.py`):

- **`billedNotActive`**: a `line` charge whose number no product held on its period start, or
  whose product was live 0 days of the line's period (`expected` = days live, `invoiced` = days
  charged).
- **`activeNotBilled`**: a product of a prepaid account, not an add-on, live 1 day or more of the
  invoice period, with no `line` charge (`expected` = days live). Live means status `active`,
  `suspended`, `pendingTerminate` or `terminated`, from `start_date` to `termination_date`.
- **`usageMismatch`**: for a matched product and kind, invoiced data, voice or SMS against the
  BSS's `usage_record` total in the invoice period (not rejected; bytes to MB, seconds to
  minutes), when the difference is more than 2% of the BSS figure **and** more than 10 MB,
  10 minutes or 10 messages.
- **`duplicateCharge`**: two `line` charges for one MSISDN in overlapping periods; one entry for
  each later line, naming the first line it overlaps.

## Cost, revenue and margin

Per matched product, and per number no product held: **cost** is the sum of its invoice lines;
**revenue** the product's ABR amounts before tax, in the invoice's currency, whose period
overlaps the invoice period, each prorated by its overlapping days (refunds included, negative);
**margin** = revenue − cost. Revenue is summed exactly in SQL and rounded once to the cent, half
to even, in Python. The invoice summary has the totals of the same figures.

## API

- `POST /invoice?invoiceNumber=` (the file): 201 with the summary. Not an MCP tool (a file is the
  UI's; scope `excluded`).
- `GET /invoice` (newest first, paged), `GET /invoice/{id}` (summary with totals, the count per
  discrepancy kind, cost, revenue, margin, matched products, unmatched numbers),
  `GET /invoice/{id}/discrepancies?kind=` (by kind, then line), `GET /invoice/{id}/lines?sort=`
  (`msisdn` default, `margin` lowest first, `-margin`). The four read operations are MCP tools
  and power the Wholesale screen under Billing.

## Operator UI (#381)

`/wholesale` lists partner invoices by import order and opens a shareable invoice detail URL.
Import sends the original CSV bytes and opens the reconciled invoice. Server validation and
duplicate errors stay in the dialog with the inputs intact. The Discrepancies tab can filter by
kind. For matched products it resolves the billing account through product inventory and links
to the subscriber card. The Lines tab asks the server for lowest margin first and can reverse
the order. All three lists use server paging.

## Size and cost

An import runs a fixed number of statements whatever its size: one INSERT of all lines (with
the matching), one statement per discrepancy kind (a test counts them for 10 and 100 lines).
200,000 rows is the synchronous limit; a larger file is a 413 (split it).

## Not in this slice

The partner's real format and transfer (#203), roaming and other charge types in
the margin's detail, a currency other than the lines' own for revenue.

## Tests

`tests/billing/test_wholesale.py`: every discrepancy kind and clean lines in one invoice, a number that
moves mid-period, a SIM-only match, cost/revenue/margin exact with proration, the usage
tolerance, a duplicate number (409), malformed files (422 naming row and column), a float-looking
amount, content type and invoice number, the row limit (413), immutability, and the statement
count. MCP tools in `tests/mcp/test_mcp_tools.py`.
