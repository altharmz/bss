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
offering's profile), IMSI change -> `swap_sim` ([sim.md](sim.md)). A number-only modify sends none. Each is due on the order's date. The
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

## Device (issue #194)

Activation records the device and refuses one that does not work on the network. Design
approved by the human's standing delegation (decided following TMF). The UI is issue #225
([frontend.md](frontend.md#device-ui-2026-11-14-issue-225)).

- **IMEI** = 15 digits with a Luhn check digit (`app/network/device.py`). An invalid value is a
  422 at the API (`invalidImei` in the order engine). It is **optional**: orders without one behave
  as before; a device is accepted only on a prepaid network line (`deviceNotApplicable`).
- **TMF637 mapping.** The IMEI is a `productCharacteristic` named `imei` (`StringCharacteristic`).
  Orders alone write it: an **add** item carries it next to `simType`; a **device change** is a
  `modify` item whose only content is the `imei` characteristic (no offering, status or
  realizingResource, `deviceChangeAlone`; migration 0063 widens the item check constraint for it).
  It completes at acceptance, sends no network command and ignores the requested start date. The
  product shows the current `imei`, `deviceCompatibility` (`compatible` | `unknown`) and, for a
  warning, `deviceReasons`: derived from `product_device`, never from what an order sent.
- **History.** `product_device` (migration 0063): one row per device a line has had (IMEI,
  compatibility, reasons, `checked_at`, `started_at`, `ended_at`, the order items that recorded and
  ended it). One open row per product (unique partial index); a change closes the open row and
  inserts the next, so the old device is kept. `GET /internal/v1/device?product.id=` lists it
  (newest first); also the MCP tool `list_devices`.
- **Network adapter** `check_device(imei) -> DeviceCheck(result, reasons)` on the interface:
  `compatible`, `incompatible` or `unknown`, with reasons (bands, lock, VoLTE). It is a read-only
  question asked while the order is validated (not a durable command: nothing changes outside the
  BSS). `NetworkTemporary` counts as `unknown`. **Incompatible** rejects the order with
  `deviceIncompatible` and the reasons; nothing is created or sent. **Unknown** is allowed and
  stored as a warning (also echoed on the order item as `deviceCompatibility` / `deviceReasons`).
  `GET /internal/v1/device/check?imei=` (and MCP `check_device`) asks without storing.
- **Stub fixture:** TAC (first 8 digits) `35000000`-`35999999` compatible, `99000000`-`99000099`
  incompatible (band 71, carrier lock, VoLTE), `98000000`-`98999999` temporary error (recorded
  unknown), anything else unknown.
- **Not in this slice:** a mandatory IMEI at activation (a later setting if wanted);
  a real device database (#203).

## Not in this slice

The admin UI (network profile editor, network step and state on order and product, Retry
button) is issue 183b (#205, [frontend.md](frontend.md#network-provisioning-ui-2026-11-09-issue-205)); later slices add commands (SIM swap is #186, number pool, porting, usage feed; the
device check is a query, below).
