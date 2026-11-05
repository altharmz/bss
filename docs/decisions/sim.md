# SIM and eSIM inventory, assignment and swap (issue #186, epic #182)

Physical SIMs and eSIM profiles are stocked, picked at activation and swapped, so every
active prepaid line has a known SIM. Design approved by the human's standing delegation
(decided following TMF). The UI half is issue #209 (186b).

## A SIM is the IMSI resource

The IMSI `LogicalResource` of [prepaid.md](prepaid.md#imsi-issue-66) (`subscriber_number`,
kind `imsi`, with its `iccid`) **is** the SIM: orders already claim it, release it and lock
it, so assignment, history and TMF639 events stay as they were. Migration `0054` adds columns
to it, and one table:

| Column / table | Meaning |
|---|---|
| `sim_type` | `physical`, `esim`, or null for an IMSI resource created through the plain TMF639 POST (kept as it was: a `LogicalResource` without batch or extra characteristics) |
| `sim_state` | `in_stock`, `reserved`, `active`, `retired` (imsi rows; msisdn rows keep the unused default) |
| `sim_product_id` | the line holding the SIM while reserved or active |
| `sim_batch` | the batch it came in (`sim_batch`: name, type, `import`/`generated`) |
| `esim_smdp_address`, `esim_matching_id` | the parts of an eSIM's activation code; both set iff `esim` |

**Ki and OPc are never stored**: no column exists and an import with such a column is
refused (422). Unique partial indexes on `sim_product_id` (one for `active`, one for
`reserved`) make a SIM held by at most one line, and a line hold one active SIM, on top of the
existing no-overlap exclusion on claims: **a SIM can never be active on two lines**.

**Existing rows** (migration): `active` with its line when a claim runs without an end,
`retired` when locked after a release, else `in_stock`; type null, no batch. The ICCID column
and its rules are unchanged.

## TMF639

A physical SIM is a `PhysicalResource` (`serialNumber` = ICCID, `batchNumber`, plus the
characteristics `iccid`, `simType`, `simState`); an eSIM is a `LogicalResource` with the same
characteristics. Both have `resourceSpecification.id = imsi`. List filters next to the
existing ones: `simState`, `simType`, `batchNumber` (all rows, legacy ones included).
An eSIM's `smdpAddress`, `matchingId` and `activationCode` (`LPA:1$<smdp>$<matchingId>`) are
shown **only while it is reserved or active**: the matching ID is a secret that lets anyone
install the profile, so a stocked profile never exposes it. The derived characteristics may
be echoed back on PATCH, never changed (422). Unlocking a retired SIM (PATCH
`administrativeState: unlocked`) stocks it again unless a claim still runs.

## Stocking (internal API, ours: TMF has no bulk import)

`POST /internal/v1/sim/batch`: JSON `{batch, simType, items:[{iccid, imsi, smdpAddress,
matchingId}]}` or `text/csv` (header `iccid,imsi[,smdpAddress,matchingId]`, query `batch` and
`simType`). All or nothing, at most 5000 rows, 422 with the bad rows (format, repeats in the
file, eSIM parts missing or sent for a physical SIM, Ki/OPc), 409 for a batch name or an
ICCID/IMSI already in inventory. `GET /sim/batch`: batches with counts by state.
`POST /sim/esim-generate {batch, count}`: the network adapter's `generate_esim_profiles`
(stub: deterministic in the batch name, IMSI `00199...`, ICCID `8999...`, SM-DP+
`smdp.stub.example`) stocks an `esim` batch. Generation is synchronous (an operator request
that changes no line); a real adapter may make it asynchronous later. The three operations
are excluded from the MCP tools (`scope.yaml`): an operator UI action, MCP reads SIMs through
`list_resources`. Stocking emits no TMF639 events (up to 5000 rows at once).

## Activation picks a SIM

An add item may carry a product characteristic `simType` (`physical` | `esim`, a TMF
`StringCharacteristic`) instead of an IMSI resource: the server picks the first unlocked SIM
of that type in stock (oldest batch, lowest IMSI, `FOR UPDATE SKIP LOCKED`) and stores it as
the item's `realizingResource`. None in stock: rejected `simUnavailable`; an offering without
an IMSI: `imsiNotRequired`; both a simType and an IMSI: `realizingResourceDuplicate`. Without
a simType the old rules apply (`imsiRequired`). An explicit IMSI must be `in_stock`
(`imsiUnavailable` otherwise: reserved, active or retired).

States follow the order: claimed at acceptance -> `reserved` when the offering is a network
line (its `activate` waits for the network), `active` when the network confirms (a
non-network order goes straight to `active`). A refused activation or a cancelled order
puts the SIM back **in stock**; a release (termination, swap) makes it `retired` (still
locked, as in #66). `activate` already sent MSISDN, IMSI and ICCID; it now also sends
`simType`.

## SIM swap

A prepaid `modify` item on a network line whose `realizingResource` (the complete resulting
set, as before) changes the IMSI only. The order is gated like an offering change:

1. Acceptance reserves the new SIM (`reserved`, held for the line), creates the `swap_sim`
   network command (request: the old `imsi`/`iccid`, `newImsi`/`newIccid`/`newSimType`, MSISDN)
   and an `order` command (`sim_swaps`) that waits for the confirmation. Nothing else moves.
2. **Confirmed**: the claims move on the ordered date (the old one released, the new one
   assigned), the old SIM is `retired` and locked, the new one `active`, items and order
   complete, TMF639 and TMF637 events follow.
3. **Refused** (final, as in network.md): the reservation is released, items and order
   `failed` with `networkRefused`; **nothing else changes** (claims, SIM states and the
   product stay as before).
4. Cancelling an unconfirmed swap releases the reservation and cancels the command; a
   confirmed one cannot be cancelled.

Refused at acceptance: a swap that also changes the offering, the status or the number
(`simSwapAlone`), and a second swap or activation of the line still waiting for the network
(`imsiUnavailable`). IMSI changes on postpaid or non-network offerings behave as before
(immediate, nothing sent).

## Not in this slice

UI (186b, #209): stock screen with filters, CSV import and eSIM generation, activation
code as text and QR, simType choice and swap action. The retired SIM's later reuse stays an
operator unlock. A real carrier SIM or eSIM (SM-DP+) integration is a later issue (#203).

## Tests

`tests/test_sim_inventory.py`: import, list and filter, refusals, CSV, generation, activation
(physical and eSIM, ICCID/IMSI sent), activation code visibility, refused and cancelled
activation, SIM never active on two lines (order and database), swap confirmed / refused /
cancelled / alone, legacy rows, unlocking, migration backfill and round trip.
