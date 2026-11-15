## Behaviour and deviations

- A `partyInteraction` is one recorded contact with a customer (docs/decisions/care.md):
  `relatedParty` is exactly the customer (role `customer`), `relatedChannel` exactly one
  channel by id (`phone`, `chat`, `email`, `store`, `other`), `direction` `inbound` or
  `outbound`, `status` `open`, `inProgress` or `completed` (the default).
- `interactionItem[].item.entity` is a `Product`, `BillingAccount`, `TroubleTicket` or
  `AuditEntry` (by `@referredType`) of the same customer (422 otherwise). An item keeps `id`,
  `reason`, `resolution`, `interactionItemType`.
- Notes are append-only: the server sets `id`, `author` (the operator) and `date`. PATCH adds
  notes and changes `status`, `description` and `reason`; a completed interaction only takes
  notes (409). `relatedParty`, `relatedChannel`, `interactionItem` and `interactionDate` are
  read-only on PATCH.
- `attachment`, `interactionRelationship` and `externalIdentifier` are 422. DELETE is 405.
- Every interaction logged and every note added writes an account audit entry
  (`/internal/v1/security/audit`).
- Lists filter by `id`, `relatedParty.id`, `product.id`, `billingAccount.id`, `status` and
  `direction`, newest first.
