# Customer notifications: SMS and email (issue #192, epic #182)

Customers are told about the events that matter for a prepaid line, through a sender adapter that
only logs for now. Design approved by the human's standing delegation (decided following TMF:
TMF681 Communication Management is the message log). The UI half is issue #222 (192b): the Messages tab on the subscriber card ([frontend.md](frontend.md#messages-tab-and-consent-2026-11-14-issue-222)).
Migration `0061`.

## TMF681 (vendored v4.0.0, base `/tmf-api/communicationManagement/v4`)

Implemented: `GET /communicationMessage`, `GET /communicationMessage/{id}`, `POST` (a message an
operator writes, queued for the worker) and `PATCH` (only `state: cancelled`, of an unsent message,
else 409). Not implemented, by decision: DELETE (405: a message is a record) and `/hub` (the spec
declares no listener events, like TMF635). `messageType` is `SMS` or `Email`; `state` is the spec's
enum: `initial` (queued, or waiting for the quiet hours to end), `inProgress` (a send failed, a retry
is scheduled), `completed` (sent), `cancelled` (`stateReason`: `noConsent`, `noRecipient`,
`operator`), `failed` (retries used up). One `receiver`, `sender` stored as sent, `characteristic`
carries `category`, `event`, `template`, `stateReason`, `lastError`, `providerReference`,
`resendOf`, `billingAccount`, `product`. A bill email lists its PDF in `attachment` (by url, #202);
an `attachment` in a POST and `logFlag: false` are 422; `state`,
`sendTime`, `sendTimeComplete`, `tryTimes` are read-only (400). Filters: `id`, `state`,
`messageType`, `receiver.party.id` (customer), `billingAccount.id`, `product.id`. MCP tools:
list, get, create, patch.

## Data (migration 0061)

- `notification_message`: one row per message. `dedupe_key` is the identity of the event; unique
  with `channel`, and `notify` skips an event whose key exists on any channel, so a repeated tick,
  or a contact added later, never sends an old event again. Operator messages and resends have no key.
- `notification_consent`: append-only history (customer, channel, category `marketing`, opted in or
  out, source, operator); the latest row (`recorded_at`, then `seq`) wins; no row = not opted in.

## Adapter

`app/integrations/notify/`: `send_sms(key, to, body)`, `send_email(key, to, subject, body,
attachments=())` (each attachment `(filename, mime_type, bytes)`, since #202); the key
is the message id and a repeated key must answer the first reference. `BSS_NOTIFY_ADAPTER=log`
(default; unknown value fails startup) records the message and accepts it; test values: an SMS to a
number ending `0000` or an email to `fail@example.test` is refused until `recover(to)`. A real SMS
and email provider is a later issue (#203).

## Triggers

`app/notify/events.py` reads durable facts (nothing hooks into the code that makes the event) in
the worker, after the entitlement observation (`app/worker/notify.py`, in every wake-up). Facts
older than `BSS_NOTIFY_LOOKBACK_DAYS` (2) are ignored, so deploying does not replay history; the
state-based events describe the present (a low-balance account is told once after deploy).

| Event | Fact | Once per |
|---|---|---|
| `low_balance` | spendable below `BSS_NOTIFY_LOW_BALANCE` (5.00) | top-up episode (latest completed top-up or payment) |
| `plan_expiring` | paid-through within `BSS_NOTIFY_EXPIRING_DAYS` (3) | paid interval |
| `renewal_done` / `renewal_failed` | renewal command funded / needs top-up | renewal command |
| `grace` | unfunded, before the suspension date | first unfunded day |
| `suspended` / `deactivated` | lifecycle order completed (network confirmed) | lifecycle event |
| `usage_80` / `usage_100` | `usage_threshold` row | product, period, allowance, mark |
| `card_declined` | `payment_command` declined | command |
| `port_in_pending` / `_rejected` / `_completed` | port-in request state | request, submission, state |
| `sim_swap` | `swap_sim` network command confirmed | command |
| `bill_issued` | a bill's PDF rendered, account with the `email` medium (email only, PDF attached, `BSS_BILL_EMAIL_ENABLED`; [bills.md](bills.md#pdf-documents-and-email-delivery-202-2026-11-18)) | bill |
| `dunning_reminder1` / `_reminder2` / `dunning_suspended` | a postpaid dunning step taken (`dunning_step`; the suspension only when it suspended a product; [dunning.md](dunning.md#notifications)) | account, first overdue date and step |

A port-in has no customer until an order claims the number; it is told from then on. Recipients:
the customer's (then the party's) `contactMedium` email and phone (preferred first); without a phone
the SMS goes to the line's number (an account event: its first open line), except for a
deactivation (the line is gone). Both channels are used when both exist; nobody reachable gives one
`cancelled` / `noRecipient` message. English templates per event and channel are in
`app/notify/templates.py`.

## Sending, consent, quiet hours

The worker sends due messages (`initial`/`inProgress`, `next_attempt_at` reached) in their own
transactions. A failure is `inProgress` with a retry after 1, 5, 15, 60 minutes, `failed` after
`BSS_NOTIFY_MAX_ATTEMPTS` (5) with the error kept. **Consent** is checked at creation and again at
the send: a marketing message (only an operator can write one) without the customer's latest
opt-in for its channel is `cancelled` / `noConsent` and never reaches the adapter; transactional
messages need no consent. **Quiet hours** apply to SMS only: `BSS_NOTIFY_QUIET_START`/`END`
(21:00/08:00 in the billing time zone, equal = off); an SMS created or due inside them is scheduled
for their end. A broken detector or event is logged and skipped: billing never waits for a message.

The detectors find an event on every wake-up for as long as its fact is recent. The worker
drops the ones that have a message already with one statement per detector
(`existing_events`), then looks up recipients only for new events. Before #281 it looked each
one up: on the 100k dataset about 47,500 told events, two statements and a commit each, some
100 s of every wake-up.

Since #340 the detectors that key an event on row ids (`renewals`, `lifecycle`, `usage`,
`card_declines`, `ports`, `sim_swaps`, `security`) also leave out told events in their own SQL:
`NOT EXISTS (SELECT FROM notification_message m WHERE m.dedupe_key = <the key in SQL>)`, served by
the unique `(dedupe_key, channel)` index, before any per-row enrichment (grants, line numbers in
one `recipients.line_numbers` call per detector). A wake-up then reads only new events: before,
`_renewals` alone fetched about 28,700 told events on every wake-up at 100k, and the worker
allocated and freed some 65 MiB each time. The `existing_events` check stays as the guard. The
SQL key must equal the Python one; the usage key has a test of that (zones included).

## Internal API (ours)

`GET`/`POST /internal/v1/notification/consent` (customer's consent per channel and history; record
an opt-in or opt-out) and `POST /internal/v1/notification/message/{id}/resend` (a copy of a sent,
failed or cancelled message, `resendOf`; 409 while open or without an address). Operator screens, so
not MCP tools (scope excluded).

## Not in this slice

A real provider, other languages, inbound STOP handling and unsubscribe links, delivery
receipts, other settings than the environment variables above.

## Tests

`tests/care/test_notifications.py`: every trigger once (and not again), recipients and the line-number
fallback, sender failure with backoff, `failed` and recovery, resend, consent (opt-in, opt-out,
history), quiet hours, TMF681 conformance and refusals, adapter selection; MCP tools in
`tests/mcp/test_mcp_tools.py`.
