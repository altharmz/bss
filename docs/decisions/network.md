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
`international`, `voicemail`, `wifiCalling` (0 or 1: the vendored TMF620 schema does not
validate boolean values; `wifiCalling` since #201, [below](#e911-address-issue-201)). The values listed are what offerings may use, the default first. Profile values may
be **narrowed per offering** with a `prodSpecCharValueUse` entry of the same name holding
one of the specification's values; the command takes the offering's **effective** values
(default, narrowed). Both writes are validated (400). The profile is copied into the command
at acceptance and sent unchanged: a later catalog edit never alters a command already made.
T-Mobile enforces caps and throttling; the BSS only sends the profile.

**Roaming and international (issue #200):** the flag `roaming` (the line may roam outside the
US) and the allowances `roamingDataMb`, `roamingMinutes`, `roamingSmsCount` (while roaming) and
`internationalMinutes` (calls from the US to non-US numbers), integers of zero or more.
`profile.combine` adds them up and switches the flags on, like the others. T-Mobile blocks or
bills usage beyond the profile; the BSS sends only the profile.

## Commands

`network_command` (migration 0052): one per order item, `id` = idempotency key
(uuid5 of the item and the kind). Since #200 (migration 0084) an add-on pass item can also
have one `purpose = passEnd` command: the change_profile on the day the pass ends before its
plan period ([prepaid.md](prepaid.md#roaming-and-international-passes-2026-11-16-issue-200)). Kinds: add -> `activate`, suspend -> `suspend`, resume ->
`restore`, terminate -> `deactivate`, offering change -> `change_profile` (the new
offering's profile), IMSI change -> `swap_sim` ([sim.md](sim.md)), a new E911 address on a Wi-Fi
calling line -> `update_e911` ([below](#e911-address-issue-201)). A number-only modify sends none. Each is due on the order's date. The
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
A command carrying an E911 address with postcode `00911` (also `00911-xxxx`): refused
`e911AddressInvalid`.
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

## E911 address (issue #201)

US rules (Kari's Law, RAY BAUM'S Act) require a dispatchable location for 911 calls made over
Wi-Fi. The carrier routes them, so the MVNO registers an address per line. Design approved on
the issue (the human's delegation, decided following TMF); the UI is #384; the real T-Mobile
field mapping comes with #203. Migration `0085`. Code: `app/network/e911.py` (validation,
shape), `app/ordering/e911.py` (order rules), `app/worker/network.py` (send, confirm).

- **Which lines.** A line whose effective profile has `wifiCalling = 1` needs an address. For an
  add-on pack, the plan line's combined profile counts.
- **Where it lives.** TMF637 `product.place`: one `RelatedPlaceRefOrValue` with role
  `e911Address` and a `GeographicAddress` (`streetNr`, `streetName`, `streetSuffix`, `city`,
  `stateOrProvince`, `postcode`, `country`, `geographicSubAddress`). It is stored normalized
  (trimmed, `country` US, without `@type`) in `product.e911_address` (jsonb, null), not in the
  product's documents. On a network line the product's `place` is derived from the column and
  is never what an order sent.
- **Validation** (TMF622 add or modify item, and TMF637 POST /product of a network line): as
  the service address ([taxes.md](taxes.md#service-address-design-approved-2026-11-05)): US
  only, a state or territory code, a ZIP or ZIP+4, `streetName` and `city` non-empty
  (`streetNr` and `streetSuffix` optional, never empty). An invalid address is a 422 naming the
  path (`productOrderItem.0.product.place.0.place.postcode`). Another role, or more than one
  place, is a 400: we implement only this role. `place` on a delete or noChange item is a 422.
- **Add.** The address sent is stored on the new product. A Wi-Fi calling line without one takes
  the billing account's service address, **copied, not linked** (`street1` becomes `streetName`,
  `street2` a `geographicSubAddress` name; a later service address change leaves it). With
  neither, the order is rejected `e911AddressRequired`. A line without Wi-Fi calling stores an
  address sent (customers may pre-register) and the network gets it only once the line's
  profile enables Wi-Fi calling.
- **An offering change or an add-on pack** that enables Wi-Fi calling on a line without an
  address copies the service address the same way at acceptance, or is rejected
  `e911AddressRequired`. An add-on item cannot carry a place (`e911NotApplicable`).
- **Sent to the network.** `activate` and `change_profile` to a Wi-Fi calling profile carry the
  line's address as `e911Address` in the request and as a TMF641 `serviceCharacteristic`
  named `e911Address`. The request is built at the first send and retained, so every retry sends
  the same address.
- **A change** is a TMF622 **modify** item whose only content is `product.place` (anything else
  with it is a 422; an order mixing it with other kinds of items is rejected `e911ChangeAlone`).
  Its requested start date is ignored, as for a device change. On a Wi-Fi calling line it makes
  an `update_e911` command (TMF641 modify, the line's current profile and the new address), due
  today, with the same gating, retry, `exhausted` and operator retry rules. The worker stores the
  new address when the network confirms; the order's `e911_changes` command then completes the
  items. A refusal fails the order (`networkRefused`, the network's code in `message`) and leaves
  the old address ([refusal rule](#refusal-leaves-the-product-as-before-the-order)).
  `cancelProductOrder` cancels a change the network has not confirmed. On a line without Wi-Fi
  calling the change is stored at acceptance and the order completes at once, with no command.
  In an order with both kinds of lines, the lines without Wi-Fi calling take theirs at
  acceptance and a later cancellation does not undo them.
- **Protected action.** A change is audited like a contact change: it needs the account PIN of a
  protected customer (`X-Account-Pin`; refusals as for a SIM swap) and writes an `account_audit`
  row with action `e911_changed`, which raises the `security_e911_changed` alert
  ([accounts.md](accounts.md#sensitive-actions)). An address given at activation or copied
  from the service address is not a change and is not audited.
- **Postpaid** products and offerings that are no network line take no address
  (`e911NotApplicable`); on other products TMF637 `place` stays a plain document. TMF637 PATCH
  of `place` on a network line is a 409: the address changes only through an order.
- **Read.** TMF637 `product.place` returns the role and the address.
  `GET /internal/v1/billingAccount/{id}/products` adds `e911Address` (the stored address or null)
  and `wifiCalling` (the offering's flag).

## Data pool commands (2026-11-20, issue #379)

The account data pool ([usage.md](usage.md#pooled-data-2026-11-20-issue-379)) reaches the
network as fields on each member's profile commands. Migration `0089`.

- **Fields:** a member's `activate` and `change_profile` carry `dataPoolId` (the billing account
  id) and `dataPoolMb` (the pool size on the due date), at the top of the request and as
  TMF641 `serviceCharacteristic`s of the same names. They are computed at the first send and
  retained with the request, so a retry sends the same size. `network_command.data_pool`
  keeps `{id, mb, periodStart}`.
- **Pool changes:** each wake-up (`sync_pools`, before the due commands) looks at the
  accounts with a live line on a pooled offering. A live member that was told another size
  or period, or nothing yet while it is active on the network, gets one `change_profile`
  with purpose **`dataPool`**: its current profile (plan and live add-ons), the new pool
  fields, due today, and sent by the same wake-up. It belongs to no order
  (`product_order_id` and its item are null; the internal API shows `productOrder: null` and
  `purpose`), so it gates nothing. The same retry, `exhausted` and operator-retry rules
  apply. A refusal is recorded and final.
- **When:** a line joins (its own activate carries the size, and the others are told), an add-on adds
  data, or a pool period starts. A line that leaves changes nothing until the next period,
  because its share stays. A member whose own activate or change_profile is due and unsent
  is skipped: it carries the size when it is sent.
- **Once:** the id is uuid5 of the member, the period start and the size. The size never falls
  inside a period, so each size is told once, and an unsent older size is cancelled when a
  newer one is made.

## Not in this slice

The admin UI (network profile editor, network step and state on order and product, Retry
button) is issue 183b (#205, [frontend.md](frontend.md#network-provisioning-ui-2026-11-09-issue-205)); later slices add commands (SIM swap is #186, number pool, porting, usage feed; the
device check is a query, below).
