# Numbers: carrier pools, port-in and port-out (issue #189, epic #182)

Numbers come from the carrier by area code, customers bring their own (port-in) and
leave with it (port-out). Design approved by the human's standing delegation (decided
following TMF; TMF has no porting, pool or PIN API, so all of it is the internal API under
`/internal/v1`). The carrier is a placeholder: `BSS_NETWORK_ADAPTER=stub` (default,
[network.md](network.md)); the interface and the stub are in `app/integrations/network/`.
The UI half (issue #214, 189b) is in [ui.md](../ui.md). The numbers themselves stay TMF639 resources
([prepaid.md](prepaid.md#n3-subscriber-number-inventory-api-2026-10-20)); the API never
contacts the carrier, the billing worker does (`app/worker/ports.py`, called in every
prepaid dispatch after the network commands and the payments).

## Data (migration 0056)

| Column / table | Meaning |
|---|---|
| `subscriber_number.origin` | `manual` (default, TMF639), `pool`, `port_in` |
| `subscriber_number.area_code` | NPA for pool and port-in numbers (11 digits starting with 1), else null |
| `port_request` | one port: `direction` in/out, `state` (`submitted`, `pending`, `rejected`, `completed`, `cancelled`), `number_value`, `subscriber_number_id` (null for a port-out of a number the BSS does not hold), port-in data (`carrier_account_number`, `billing_zip`, names, `transfer_pin`), `carrier_reference`, `submissions`, rejection code and reason, backoff, port-out: product, account, termination `product_order_id`, `answered_at` |
| `number_transfer_pin` | `pin_hash`, `expires_at`, `used_at`, `revoked_at`, per product and number |

The losing carrier's PIN (`port_request.transfer_pin`) is kept only until the carrier has
accepted the submission and is then cleared; no API returns it. A **Number Transfer PIN** (ours,
for port-out) is generated on request, shown **once** in the response, stored only as a hash
(sha256 over the PIN id and the PIN; a 6-digit PIN is not brute-force proof against a database
leak, which is why it is short-lived and single use) and listed as metadata. A newer PIN revokes
the older one. It is valid for `BSS_NUMBER_TRANSFER_PIN_DAYS` (default 7, optional, no deployment
step) and single use. Generating one needs a session (the internal API) and an active or
suspended product with a current number; it is not an MCP tool (`scope.yaml` excluded).

## Number pool

`POST /internal/v1/number/pool {areaCode, count}` (NPA `[2-9]XX`, 1..100) calls the adapter's
`request_numbers`; the numbers are created as unlocked `msisdn` resources (origin `pool`) with a
TMF639 create event. Stub: `1<NPA>5550100`..`0199`, the range reserved for fiction (100 per area
code, then 409). Synchronous, like eSIM generation (an operator request that changes no line); a
real adapter may make it asynchronous. A carrier failure is 503. A crash between the carrier's
answer and the commit leaks the numbers on the carrier side; a real integration should reserve
with an idempotency key (a later issue, #203).

## Port-in (the design gate)

1. `POST /port/in {number, carrierAccountNumber, transferPin, billingZip, firstName, lastName}`
   creates the number in inventory (origin `port_in`) and a request `submitted`. The worker submits
   it with an idempotency key (request id and submission count) -> `pending` (the carrier's
   reference is kept); on a later wake-up it reads the answer: `completed`, or `rejected` with the
   carrier's code and reason. Temporary errors retry with the network backoff; after
   `network_max_attempts` the request is `rejected` (`networkUnavailable`).
2. **No temporary number in the BSS; the order completes on port completion.** The line's
   activation order claims the ported number like any inventory number. Its `activate` network
   command is **held** (not sent, not counted as an attempt) while the number is a port-in without
   a completed port request (`network_commands.due`); the existing gate keeps the order
   `inProgress` and posts nothing until the network confirms, so the line is created and charged
   when the port completes. A temporary number during the port is the carrier's own feature, not
   tracked here. `activate` carries `portedIn: true`.
3. A number whose port-in is rejected or cancelled cannot be claimed by a new order (a rejected one
   can, to wait for its resubmit); until completion only a network line can claim a ported number
   (`numberUnavailable` otherwise).
4. `POST /port/{id}/resubmit` (only `rejected`) takes the corrected data and the PIN again ->
   `submitted`, `submissions` + 1 (a new idempotency key). `POST /port/{id}/cancel` cancels an
   open port-in unless an order holds the number (409: cancel the order). Cancelling an order
   cancels its open port-in (`_network_blocks_cancel`). The carrier is not told of a cancellation by
   the stub; a real adapter should.
5. `GET /port` (filters `direction`, `state`, `number`) and `GET /port/{id}`.

## Port-out

The carrier reports a port-out request (`adapter.port_out_requests()`; the stub's
`inject_port_out`; a real carrier may push to a webhook that records the same request). Each is
recorded once by its reference and **decided in one transaction**:

- number unknown -> `numberUnknown`; no line (no current claim, or the product is not active or
  suspended, or ends) -> `lineNotActive`; the PIN is not the line's current one, or was used ->
  `pinInvalid`; it expired -> `pinExpired`.
- **valid:** the PIN is marked used, the product ends through an order (a `delete` item dated
  today, placed through the order engine with `OrderRequest.network=False`: the carrier moves the
  line itself, so **no `deactivate` is sent**), the number is released by that order and then
  **locked** (a ported-away number must never be assigned again; TMF639 shows
  `administrativeState: locked`), the request is `completed` with the order linked. If the order
  engine rejects the termination the request is `rejected` (`terminationRefused`) and nothing else
  changes.
- Either way the answer (approve, or reject with reason) goes through
  `adapter.answer_port_out(key, reference, approved, reason)` with a key derived from the request;
  until the carrier acknowledges it (`answered_at`) the worker sends it again on every tick.
- The BSS ends the line when it approves (the port date is today); the carrier's later completion
  is not modeled separately.
- The carrier's wrong Number Transfer PINs are not counted. The account PIN that guards issuing one,
  the port-out lock and the audit are in [accounts.md](accounts.md#account-security-pin-verification-locks-and-audit-issue-193-epic-182) (#193).

## Stub test values

Port-in: PIN `000000` -> rejected `pinMismatch`, billing ZIP `00000` -> `zipMismatch` (read on
the poll, so the request is `pending` first); a number ending `0001` stays pending until
`adapter().complete_port_in(number)`, ending `0002` fails temporarily on submit until
`adapter().recover(number)`. Network commands keep their own rules (MSISDN endings `0000` refused,
`0001` temporary until `recover`). `inject_port_out(reference, msisdn, pin)` queues a port-out;
`port_answers` keeps what we answered.

## Not in this slice

Carrier-side cancellation of a port-in, a webhook receiver for port-out, a separate port-out
completion date, reserving pool numbers with a hold period.

## Tests

`tests/test_number_porting.py`: pool by area code in TMF639 and refusals; port-in happy path
(activation held until completion, ported number sent), rejection with reason and resubmit with
corrected data, pending until the carrier completes, temporary errors then `networkUnavailable`,
order cancellation cancelling the port, non-network offerings refused, the PIN never stored or
listed and replaced by a newer one, PIN expiry, port-out valid (product ended by an order, no
deactivate, number locked, carrier answered once) / invalid / expired / unknown / used, and an
unacknowledged answer resent with the same key.
