# Event notifications: TMF hubs, events, delivery (2026-10-17)

The approved plan of 2026-10-17 (three slices, all 17 `/hub` operations, real delivery).
The specs' `/listener/*` operations are the client's side - what a subscriber
implements to receive events - so they stay out of the coverage count's reach by
design: this service creates hubs and sends to them, it never listens. Coverage after
slice 1: 122/129 operations of the vendored specs (94.6%); the seven left are excluded
by decision (TMF637 product writes, billingCycleSpecification writes, PATCH productOrder).

## Slice 1: hubs (subscriptions)

- **Operations:** `POST /hub` and `DELETE /hub/{id}` in all eight APIs, `GET /hub/{id}`
  in TMF629 (the only spec that has it). 201 with the hub; TMF676 is v4, so its hub is an
  `EventSubscription` (`id`, `callback`, `query`; no `href`, no `@type`, no Location),
  the others a v5 `Hub` with `href` and Location. DELETE: 204, no query parameters (400).
  Handlers are written out per API (`create_hub_<api>`, `delete_hub_<api>`,
  `get_hub_customer`; `app/api/hub.py`) - the function name is the operationId and the
  MCP tool name, and must be unique.
- **Storage:** migration `0017`, `event_subscription` - one table for every API's hubs
  (`api`), `callback`, `query` as sent, `event_types` parsed from it, `subscribed_at`
  (clock), `deleted_at`. DELETE marks it deleted (kept for slice 3's deliveries); a
  deleted hub, or another API's, is a 404 - a hub id belongs to the API that made it.
- **Query:** only `eventType=A` or `eventType=A,B`, each a listener event of the hub's
  own API (`app/events/types.py`, checked against `spec/` by a test), no duplicates.
  Anything else is a 400 naming the supported form. No query: every event of the API.
  Names we never emit (InformationRequired, JeopardyAlert, Milestone, ProductBatch,
  ErrorMessage, billingCycleSpecification changes) are valid but receive nothing.
- **Callbacks** (`app/events/callbacks.py`): an absolute http(s) URL without
  credentials or fragment (422 otherwise). **Outside `APP_ENV` dev and test** - and the
  VPS runs `staging` - it must be `https` and its host must resolve only to public
  addresses: `ipaddress.is_global`, not multicast, IPv4-mapped IPv6 unwrapped first. So
  no loopback, RFC 1918, Docker networks, link-local and cloud metadata
  (169.254.169.254), CGNAT (100.64/10), ULA, unspecified. The service shares the host
  with a VPN server and the `shared` network with other services; a callback is an
  outbound request on behalf of whoever created the hub. Checked on POST (422) and,
  in slice 3, again before every delivery.
- **MCP:** 17 tools; creating and deleting hubs needs the write scope, `get_hub_customer`
  is read.
- Tests: `tests/tmf/test_api_hub.py`. Negative proofs, each reverted: without the
  `is_global` check, all twelve private/special address cases and the staging API test
  fail; without the `api` filter in `hubs.get_live`, another API deletes a hub that is
  not its own.

## Slice 2: events (the outbox)

- **Storage:** migration `0018`: `event` (api, event type, resource, resource id,
  `occurred_at` from the clock, `payload` jsonb exactly as sent, `seq` identity for the
  order within a transaction) and `event_delivery` (one per event and subscription:
  `pending`/`delivered`/`failed`/`dropped`, attempts, `next_attempt_at`, last error).
  **Transactional:** a write stores its events in its own transaction
  (`app/events/emit.py`), so an event exists exactly when the change committed. **Only
  when wanted:** with no live hub wanting the event type nothing is stored, and the body
  is not even built (`emit.before` / lazy builders) - a write nobody listens to costs one
  small query.
- **Fan-out at write time:** one delivery per live hub of the API that wants the type; a
  hub created later never receives older events. DELETE /hub drops its pending ones.
- **Envelope:** v5 `Event`: `id` = `eventId`, `eventTime` = `timeOccurred`,
  `eventType`, `event: {<resource>: body}`, `@type` = the event type. TMF676 and TMF654 (v4):
  `eventId`, `eventTime`, `eventType`, `event`. The body is the resource as the API
  answers it (a Delete: as it was just before). Stored with `tmfjson` and sent as the
  stored JSON text, so money stays an exact number (never a float).
- **What emits what:**
  - every TMF POST: `<Resource>CreateEvent`; every DELETE: `DeleteEvent`;
  - every PATCH compares the body before and after: the state attribute changed
    (`lifecycleStatus`, `status`, `state`) gives a `StateChangeEvent`, any other
    attribute (lastUpdate aside) an `AttributeValueChangeEvent` - both, or none for a
    no-op PATCH;
  - orders: one `ProductOrderCreateEvent` with the final state (processing is
    synchronous), a `ProductCreateEvent` per add item, a `ProductStateChangeEvent` per
    delete item; a cancellation: `CancelProductOrderCreateEvent`,
    `ProductOrderStateChangeEvent`, and its products' StateChange; DELETE of a rejected
    order: `ProductOrderDeleteEvent`;
  - bills: `CustomerBillCreateEvent` once a bill is issued (its `new -> validated ->
    sent` steps are internal and emit nothing - `record_bill_state(notify=False)`),
    then a `CustomerBillStateChangeEvent` for every state change after that
    (settlement by payments, runs, refunds; hold/release);
  - the worker: `ProductStateChangeEvent` for every status flip, and
    `CustomerBillOnDemandStateChangeEvent` when it finishes a request;
  - **not emitted:** the specs' InformationRequired, JeopardyAlert, Milestone,
    ProductBatch, ErrorMessage and billingCycleSpecification StateChange events
    (billingCycleSpecification Create, AttributeValueChange and Delete are emitted since
    its writes exist, 2026-10-17); the entries an
    importJob creates (only its `ImportJobCreateEvent`); changes made through the
    internal API's price change (it is not a TMF write); a billing account's
    `accountBalance` moving because of charges or payments (not a change of the account).
- **Imports:** `app/events` never imports `app.api` at module level - the API modules
  import it, and importing the package loads all of them (a circular import otherwise).
- Tests: `tests/tmf/test_events.py` - every stored payload is validated against its
  listener's request schema in `spec/`. Negative proofs, each reverted: without the state
  comparison a customer's status change arrives as an AttributeValueChange; without
  `notify=False` an issued bill's first event is a StateChange of an internal step.

## Slice 3: delivery (the worker)

- **Step 6 of every wake-up** (`app/worker/loop.py`): `deliver.process` sends up to 50
  due deliveries, oldest first, each in its own short transaction holding its row
  (`FOR UPDATE SKIP LOCKED`). Old deliveries and events are deleted by the worker's
  housekeeping since #282 (2026-11-11): a delivery 30 days after it finished, an event
  after 90 days once no delivery refers to it ([data-model.md](data-model.md#retention-2026-11-11-282)).
- **The POST** (`app/events/deliver.py`, standard library only - no new dependency):
  the stored JSON, `Content-Type: application/json`, `User-Agent: bss-events/1`, 5 s
  timeout, redirects never followed. 2xx: delivered. Anything else - a status, a timeout,
  a refused connection - is retried after 1 min, 5 min, 30 min, 2 h and 12 h; the sixth
  failure makes it `failed` (last error kept, 1000 characters).
- **Outside dev and test the callback is checked again just before the POST** and the
  TCP connection goes to the checked address (`_PinnedHTTPSConnection`; SNI and the
  certificate are still verified for the host name). So a host that resolves to a
  private address by the time of the send - DNS rebinding - is refused and retried,
  never connected to.
- **New traffic:** on the VPS this means outbound HTTPS from the worker container to the
  hubs' callbacks (none until somebody creates a hub). No inbound port changes.
- Tests: `tests/worker/test_event_delivery.py` (a local listener on a thread). Negative proofs,
  each reverted: connecting to the host name instead of the checked address, and
  skipping the check before the send, each fail their test.

## Operator list of hubs (slice 23)

TMF has no hub list, so operators get `GET /internal/v1/eventSubscription` (ours, read only, live
hubs with their last delivery and failed count). Create and delete stay on each API's `/hub`. See
[frontend.md](frontend.md#slice-23-event-subscriptions-hubs-and-system-panel-2026-10-26).
