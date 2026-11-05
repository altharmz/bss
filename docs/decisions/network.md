# Network provisioning (issue #183, epic #182)

Every order item that changes a prepaid line sends the matching command to the network
through an adapter, and the item completes only when the network confirms. The network is
a placeholder today: `BSS_NETWORK_ADAPTER=stub` (default; an unknown value fails startup,
`app/config.py`). The interface and the stub are in `app/integrations/network/`; a real
T-Mobile integration is a later issue and keeps this contract.

## What is a line

An offering is a **network line** when its product specification declares network profile
characteristics. Items on other offerings send nothing and behave as before. Existing
products get no command on deploy; only new order items are sent.

## Network profile (no new table)

Held as TMF620 characteristics (`app/network/profile.py`), edited in the catalog:
`productSpecCharacteristic` entries named `dataAllowanceMb`, `dataCapMb`, `throttleKbps`,
`voiceMinutes`, `smsCount` (integers of zero or more) and the flags `hotspot`,
`international`, `voicemail` (0 or 1: the vendored TMF620 schema does not validate boolean
values). The values listed are what offerings may use, the default first. Profile values may
be **narrowed per offering** with a `prodSpecCharValueUse` entry of the same name holding
one of the specification's values; the command takes the offering's **effective** values
(default, narrowed). Both writes are validated (400). The profile is copied into the command
at acceptance and sent unchanged: a later catalog edit never alters a command already made.
T-Mobile enforces caps and throttling; the BSS only sends the profile.

## Commands

`network_command` (migration 0052): one per order item, `id` = idempotency key
(uuid5 of the item and the kind). Kinds: add -> `activate`, suspend -> `suspend`, resume ->
`restore`, terminate -> `deactivate`, offering change -> `change_profile` (the new
offering's profile). A number-only modify sends none. Each is due on the order's date. The
worker (`app/worker/network.py`, called first in every prepaid dispatch) sends the due ones;
the request (line identifiers MSISDN/IMSI/ICCID from the product's number claims, the
profile, a TMF641-shaped `serviceOrderItem`: add / modify / delete) is built once and
retained; every retry resends it unchanged. Outbound shape follows TMF641 Service Order;
no public API is added and TMF641/640 are not vendored.

## Answers

- **Confirmed**: the command is `confirmed`.
- **Temporary error** (transport, unknown outcome): the worker retries with backoff
  (1, 5, 15, 60 minutes), the item stays `inProgress`. After `network_max_attempts` (5)
  the command is `exhausted`: nothing is sent automatically; the internal
  `POST /internal/v1/network/command/{id}/retry` (409 in any other state) returns it to
  `pending` with the same key and request.
- **Refusal** is final: no retry, the fix is a new order. The item and the order become
  `failed` with a `networkRefused` error (the network's reason, its code in `message`).

## Gating (how the order waits)

The order keeps its `order` prepaid command; the worker posts it only when every network
command of the order is confirmed (`gate`). Status changes of lines always go through the
dated-command path (`schedule_statuses`), even when dated today, so they wait too.
Terminations are applied at acceptance as before but credits are posted only after the
confirmation. An offering change at a renewal on a line no longer completes at once: a
`profile_changes` order command, due on the renewal date, completes the items after
`change_profile` is confirmed, and the renewal does not apply the retained change before
that answer (`_renewal_blocked`).

## Refusal leaves the product as before the order

Nothing is posted before the confirmation, so a refusal only undoes acceptance
(`refuse_order`, in the command's transaction): the hold is released and the number claim
cancelled; **suspend / restore** drop the retained status change; **change_profile** drops
the retained offering change (and releases its hold); **deactivate** restores the product
(no termination date, status and number release back) so billing is never ended; a refused
**activate** ends the product at its start date (`cancelled`, zero billable days) with no
funded interval, charge or bill to reverse. The dated start is kept: the ordered date, never
the confirmation date, is the product start, and billing follows dates.

## Cancellation

`cancelProductOrder` cancels the order's unsent network commands (`cancelled`); an order
whose command the network already confirmed, or an offering change waiting for its
`change_profile`, is refused (409).

## Stub test values

The line's MSISDN ending `0000`: refused. Ending `0001`: temporary error until
`adapter().recover(msisdn)`. `adapter().refuse_kinds` refuses a command kind for every line.
The stub answers a repeated key with the first answer.

## Not in this slice

The admin UI (network profile editor, network step and state on order and product, Retry
button) is issue 183b; later slices add commands (SIM swap, number pool, porting, device
check, usage feed).
