# Customer care: interaction notes and cases (issue #199, epic #182)

Agents record what happened with a subscriber (notes) and track simple cases with a status,
both linked to the account audit log. Design decided following TMF under the human's
standing delegation for epic #182 (the issue's design comment, 2026-11-15). This slice is the
backend; the UI (an Interactions and a Cases tab on the subscriber card, a global Cases
screen) is a separate issue blocked by this one.

## The APIs (vendored, `spec/`)

- **TMF683 Party Interaction v5.0.0**, base `/tmf-api/partyInteractionManagement/v5`: the
  note on a subscriber, one recorded contact (call, chat, email, store visit) with its notes.
  The spec's `servers` URL names no API path (`https://serverRoot`); the base follows the
  pattern of the others (TMF API name + major version).
- **TMF621 Trouble Ticket v5.0.1**, base `/tmf-api/troubleTicket/v5` (its `servers` URL):
  the case. The repo publishes no v5.0.0.

Both: `GET` list and by id, `POST`, `PATCH` (the four PATCH formats), `/hub` (TMF621 also
`GET /hub/{id}`, as its spec defines). `DELETE` is 405: interactions and cases are records.

## Interactions (TMF683)

- `relatedParty` names exactly one party, the customer (role `customer`, a `PartyRoleRef` to
  a Customer); anything else is a 422.
- `direction` is `inbound` or `outbound`; `reason` is free text (required by the spec).
- `relatedChannel` is exactly one channel, named by id: `phone`, `chat`, `email`, `store`,
  `other` (we have no channel resource); its `role` is kept as sent.
- `status` is the spec's example enum `open`, `inProgress`, `completed`, default `completed`
  (most notes are written after the contact). Not a Postgres enum: the spec's type is an
  example, not a constraint.
- `interactionItem[].item` links what the contact was about: a `Product` (the line), a
  `BillingAccount`, a `TroubleTicket` or an `AuditEntry` (by `entity.@referredType`). Each
  must exist and belong to the same customer (an audit entry may name none), else 422. An
  item keeps `id` (the client's, else its number), `reason`, `resolution` and
  `interactionItemType`; its own dates, channels, parties, notes, attachments and
  relationships are refused (the interaction has them).
- **Notes are append-only.** `author` is the operator and `date` the clock; a client sending
  them on a new note gets a 422. A PATCH `note` adds the entries that do not name an existing
  note; an entry naming one must repeat it unchanged (a JSON Patch sends the whole array).
- PATCH adds notes and may change `status`, `description` and `reason`; a `completed`
  interaction only takes notes (409). `relatedParty`, `relatedChannel`, `interactionItem`
  and `interactionDate` are read-only on PATCH (400): they record the contact that happened.
- Not implemented (422): `attachment`, `interactionRelationship`, `externalIdentifier`.
- Filters: `id`, `relatedParty.id`, `product.id`, `billingAccount.id` (through the items),
  `status`, `direction`; newest first.

## Cases (TMF621)

- Required by the spec: `description`, `severity` (`minor`, `major`, `critical`),
  `ticketType` (`incident`, `complaint`, `request`). Also `name`, `priority` (`low`,
  `medium`, `high`, `critical`; default `medium`), `requestedResolutionDate`,
  `expectedResolutionDate`, `channel` (as for interactions), `relatedParty` (one customer,
  required), `relatedEntity` (`Product`, `BillingAccount`, `PartyInteraction`, `AuditEntry`,
  same customer), `note` (append-only as above). A PATCH `relatedEntity` replaces the list.
- `status` is the spec's `TroubleTicketStatusType`, the Postgres enum
  `trouble_ticket_status_type` under the enum lock. A ticket is created `acknowledged`
  (sending `status` on POST: only that value, else 422). Transitions, others 409:
  - `acknowledged` -> `inProgress`, `rejected`, `cancelled`
  - `inProgress` -> `pending`, `held`, `resolved`, `cancelled`
  - `pending`, `held` -> `inProgress`, `cancelled`
  - `resolved` -> `closed`, `inProgress` (reopened)
  - `closed`, `rejected`, `cancelled` are final: no change at all (409), an empty patch aside.
- `statusChangeReason` is required for `rejected`, `cancelled`, `pending`, `held` and a
  reopening (422), and refused without a status change. `statusChangeHistory` (every status,
  the first `acknowledged`) and `statusChangeDate` are the server's; `resolutionDate` is set
  at `resolved`, kept at `closed` and cleared by a reopening (a CHECK holds it).
- Not built: `troubleTicketSpecification` (its endpoints are absent: simple cases have no
  templates), `attachment`, `externalIdentifier`, `troubleTicketRelationship`,
  `troubleTicketCharacteristic` (422), assignment to an operator, SLA timers.
- Filters: `id`, `relatedParty.id`, `product.id`, `billingAccount.id`, `status`, `severity`,
  `ticketType`, `priority`; newest first.

A client may choose the `id` on POST (a UUID), and `@baseType`/`@schemaLocation` are kept,
as on every resource (tmf-api.md, "Nothing dropped or rewritten").

## Link to the audit log

Both directions. A case or an interaction can reference an `account_audit` entry (the action
the customer calls about); its `href` is the audit read filtered to it
(`/internal/v1/security/audit?id=<id>`, a filter added for this). Every interaction logged,
case opened, case status change and note added writes one `account_audit` row: actions
`interaction_logged`, `ticket_opened`, `ticket_status_changed`, `care_note_added`, outcome
`done`, verification `none`, the operator, the customer, the product and billing account when
the resource is about exactly one (a product's account counts), and `detail` with the
interaction or ticket id, the note id, or the status from/to (a status change's reason is the
entry's `reason`). These actions raise no customer security alert (`audit_since` leaves them
out).

## Data (migration 0080)

`party_interaction`, `party_interaction_item`, `party_interaction_note`, `trouble_ticket`,
`trouble_ticket_related_entity`, `trouble_ticket_note`, `trouble_ticket_status_change`. A
link row has one column per kind and a CHECK that exactly the one its `entity_type` names is
set. Customer, product, billing account and the other care resource are foreign keys; the
audit reference has none (the log outlives what it describes). Notes and status changes are
append-only (triggers). Rolling back removes the care audit entries too (the old CHECK would
not hold them).

## Events and MCP

Hub events as the specs name them: Create, AttributeValueChange, StatusChange (their state
event; `emit` takes the name), and TMF621's `TroubleTicketResolvedEvent` at `resolved`.
Delete, InformationRequired and the specification events are valid hub names that are never
sent. A status change alone (with its date, reason, history, resolution date) is only a
StatusChange; a note or another attribute is an AttributeValueChange. TMF683's spec names
the status change payload schema `PartyInteractionStatusChange` (no `Event`): the tests map
it. MCP tools: list, get, create, patch for both resources and the hubs; the resource tools
are allowlisted in the UI/MCP parity check until the UI issue; the hubs are on the
Integrations screen.

Tests: `tests/care/test_care.py`; the gap lock records the deliberate exclusions above as
accepted deviations.
