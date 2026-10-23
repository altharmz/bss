## Behaviour and deviations

- A `bucket` is a view of a prepaid account's funding ledger: one `monetary` bucket per
  prepaid billing account and currency, with a stable id. `remainingValue` is the
  spendable amount L - H and `reservedValue` the outstanding holds H, both
  `{amount, units}` in the account currency (exact cents); `remainingValueName` is the
  same amount as text. L is the account's TMF666 `currentBalance`: payments and signed
  adjustments, reversals and completed transfers in/out, less tax-inclusive charges and
  payouts. L - H is not clamped: a debt shows as a negative amount.
- `status` follows the account: `active` (Active), `suspended` (Suspended), `expired`
  (Closed; the bucket stays readable). `partyAccount` is the billing account (with its
  state as `status`), `relatedParty` its customer. `product[]` lists the account's
  products not yet ended today, `logicalResource[]` the number each of them holds (today's
  assignment, else its next accepted claim); `isShared` is true with more than one number.
  `validFor` starts when the bucket was created and has no end: monetary credit does not
  expire. A bucket is never a deduction, so it has no `requestedDate`/`confirmationDate`.
- `accumulatedBalance` totals the account's buckets (one today): `totalBalance` is the
  sum of their `remainingValue`, `bucket[]` references them. Its v4 `logicalResource` is a
  single reference, given only when exactly one number is funded.
- Reads never write: a bucket exists only once the account has one (prepaid account
  creation, P5a). Lists filter by `id`, `partyAccount.id`, `status`, `usageType`
  (bucket) and `id`, `partyAccount.id` (accumulatedBalance), with standard paging and
  `fields`.
- POST/DELETE `/hub` subscribe to the twelve balance-action events (v4
  EventSubscription: no href, no `@type`). The actions that emit them (top-up,
  adjustment, reservation, transfer) and `balanceActionHistory` are not implemented yet;
  their endpoints answer 404 (`tests/tmf-gaps.txt`).

Tests: `tests/test_api_balance.py`, `tests/test_api_hub.py`, `tests/test_mcp_tools.py`;
the gap ledger locks the absent endpoints and the bucket's deviations.
