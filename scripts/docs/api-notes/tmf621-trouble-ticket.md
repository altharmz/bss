## Behaviour and deviations

- A `troubleTicket` is a customer case (docs/decisions/care.md): `relatedParty` is exactly
  the customer (role `customer`); `relatedEntity[].entity` is a `Product`, `BillingAccount`,
  `PartyInteraction` or `AuditEntry` of the same customer. `priority` defaults to `medium`;
  `channel` is one channel by id (`phone`, `chat`, `email`, `store`, `other`).
- Created `acknowledged`. Transitions (others 409): acknowledged -> inProgress, rejected,
  cancelled; inProgress -> pending, held, resolved, cancelled; pending, held -> inProgress,
  cancelled; resolved -> closed, inProgress (reopened). `closed`, `rejected` and `cancelled`
  are final: a final ticket takes no change.
- `statusChangeReason` is required for rejected, cancelled, pending, held and a reopening.
  `statusChangeHistory`, `statusChangeDate` and `resolutionDate` (set at resolved, cleared by
  a reopening) are the server's. `TroubleTicketResolvedEvent` is sent at resolved.
- Notes are append-only (the server sets `id`, `author`, `date`); a PATCH `relatedEntity`
  replaces the list. `relatedParty` is read-only on PATCH.
- `troubleTicketSpecification` is not implemented; `attachment`, `externalIdentifier`,
  `troubleTicketRelationship` and `troubleTicketCharacteristic` are 422. DELETE is 405.
- Opening, every status change and every note added write an account audit entry.
- Lists filter by `id`, `relatedParty.id`, `product.id`, `billingAccount.id`, `status`,
  `severity`, `ticketType` and `priority`, newest first.
