// Event subscriptions (hubs), docs/decisions/events.md and docs/ui.md.
// TMF defines POST/DELETE /hub per API; the list is ours (internal API). The base paths and
// the event types below are the server's (app/events/types.py); tests/test_ui_requests.py
// compares them, so a change there fails until this file follows.

import type { components as Internal } from './generated/internal';
import { getAll, postJson, request } from './client';

export type EventSubscription = Internal['schemas']['EventSubscription'];

export interface HubApi {
  id: string;
  name: string;
  /** TMF654 and TMF676 are v4: their hub is an EventSubscription, the others are v5 Hubs. */
  version: 'v4' | 'v5';
  base: string;
}

export const HUB_APIS: [HubApi, ...HubApi[]] = [
  {
    id: 'TMF620',
    name: 'Product Catalog Management',
    version: 'v5',
    base: '/tmf-api/productCatalogManagement/v5',
  },
  {
    id: 'TMF622',
    name: 'Product Ordering Management',
    version: 'v5',
    base: '/tmf-api/productOrderingManagement/v5',
  },
  { id: 'TMF629', name: 'Customer Management', version: 'v5', base: '/tmf-api/customer/v5' },
  { id: 'TMF632', name: 'Party Management', version: 'v5', base: '/tmf-api/partyManagement/v5' },
  {
    id: 'TMF637',
    name: 'Product Inventory Management',
    version: 'v5',
    base: '/tmf-api/productInventory/v5',
  },
  {
    id: 'TMF639',
    name: 'Resource Inventory Management',
    version: 'v5',
    base: '/tmf-api/resourceInventory/v5',
  },
  {
    id: 'TMF654',
    name: 'Prepay Balance Management',
    version: 'v4',
    base: '/tmf-api/prepayBalanceManagement/v4',
  },
  {
    id: 'TMF666',
    name: 'Account Management',
    version: 'v5',
    base: '/tmf-api/accountManagement/v5',
  },
  { id: 'TMF676', name: 'Payment Management', version: 'v4', base: '/tmf-api/payment/v4' },
  {
    id: 'TMF678',
    name: 'Customer Bill Management',
    version: 'v5',
    base: '/tmf-api/customerBillManagement/v5',
  },
];

// The listener events of each API (every one a hub may name in its query).
export const EVENT_TYPES: Record<string, string[]> = {
  TMF620: [
    'CategoryAttributeValueChangeEvent',
    'CategoryCreateEvent',
    'CategoryDeleteEvent',
    'CategoryStateChangeEvent',
    'ExportJobCreateEvent',
    'ExportJobStateChangeEvent',
    'ImportJobCreateEvent',
    'ImportJobStateChangeEvent',
    'ProductCatalogAttributeValueChangeEvent',
    'ProductCatalogCreateEvent',
    'ProductCatalogDeleteEvent',
    'ProductCatalogStateChangeEvent',
    'ProductOfferingAttributeValueChangeEvent',
    'ProductOfferingCreateEvent',
    'ProductOfferingDeleteEvent',
    'ProductOfferingPriceAttributeValueChangeEvent',
    'ProductOfferingPriceCreateEvent',
    'ProductOfferingPriceDeleteEvent',
    'ProductOfferingPriceStateChangeEvent',
    'ProductOfferingStateChangeEvent',
    'ProductSpecificationAttributeValueChangeEvent',
    'ProductSpecificationCreateEvent',
    'ProductSpecificationDeleteEvent',
    'ProductSpecificationStateChangeEvent',
  ],
  TMF622: [
    'CancelProductOrderCreateEvent',
    'CancelProductOrderInformationRequiredEvent',
    'CancelProductOrderStateChangeEvent',
    'ProductOrderAttributeValueChangeEvent',
    'ProductOrderCreateEvent',
    'ProductOrderDeleteEvent',
    'ProductOrderErrorMessageEvent',
    'ProductOrderInformationRequiredEvent',
    'ProductOrderJeopardyAlertEvent',
    'ProductOrderMilestoneEvent',
    'ProductOrderStateChangeEvent',
  ],
  TMF629: [
    'CustomerAttributeValueChangeEvent',
    'CustomerCreateEvent',
    'CustomerDeleteEvent',
    'CustomerStateChangeEvent',
  ],
  TMF632: [
    'IndividualAttributeValueChangeEvent',
    'IndividualCreateEvent',
    'IndividualDeleteEvent',
    'IndividualStateChangeEvent',
    'OrganizationAttributeValueChangeEvent',
    'OrganizationCreateEvent',
    'OrganizationDeleteEvent',
    'OrganizationStateChangeEvent',
  ],
  TMF637: [
    'ProductAttributeValueChangeEvent',
    'ProductCreateEvent',
    'ProductDeleteEvent',
    'ProductProductBatchEvent',
    'ProductStateChangeEvent',
  ],
  TMF639: [
    'ResourceAttributeValueChangeEvent',
    'ResourceCreateEvent',
    'ResourceDeleteEvent',
    'ResourceStateChangeEvent',
  ],
  TMF654: [
    'AdjustBalanceCancelEvent',
    'AdjustBalanceCreateEvent',
    'AdjustBalanceFailureEvent',
    'ReserveBalanceCancelEvent',
    'ReserveBalanceCreateEvent',
    'ReserveBalanceFailureEvent',
    'TopupBalanceCancelEvent',
    'TopupBalanceCreateEvent',
    'TopupBalanceFailureEvent',
    'TransferBalanceCancelEvent',
    'TransferBalanceCreateEvent',
    'TransferBalanceFailureEvent',
  ],
  TMF666: [
    'BillFormatAttributeValueChangeEvent',
    'BillFormatCreateEvent',
    'BillFormatDeleteEvent',
    'BillFormatStateChangeEvent',
    'BillPresentationMediaAttributeValueChangeEvent',
    'BillPresentationMediaCreateEvent',
    'BillPresentationMediaDeleteEvent',
    'BillPresentationMediaStateChangeEvent',
    'BillingAccountAttributeValueChangeEvent',
    'BillingAccountCreateEvent',
    'BillingAccountDeleteEvent',
    'BillingAccountStateChangeEvent',
    'BillingCycleSpecificationAttributeValueChangeEvent',
    'BillingCycleSpecificationCreateEvent',
    'BillingCycleSpecificationDeleteEvent',
    'BillingCycleSpecificationStateChangeEvent',
    'FinancialAccountAttributeValueChangeEvent',
    'FinancialAccountCreateEvent',
    'FinancialAccountDeleteEvent',
    'FinancialAccountStateChangeEvent',
    'PartyAccountAttributeValueChangeEvent',
    'PartyAccountCreateEvent',
    'PartyAccountDeleteEvent',
    'PartyAccountStateChangeEvent',
    'SettlementAccountAttributeValueChangeEvent',
    'SettlementAccountCreateEvent',
    'SettlementAccountDeleteEvent',
    'SettlementAccountStateChangeEvent',
  ],
  TMF676: [
    'PaymentAttributeValueChangeEvent',
    'PaymentCreateEvent',
    'PaymentDeleteEvent',
    'PaymentStateChangeEvent',
    'RefundAttributeValueChangeEvent',
    'RefundCreateEvent',
    'RefundDeleteEvent',
    'RefundStateChangeEvent',
  ],
  TMF678: [
    'CustomerBillCreateEvent',
    'CustomerBillOnDemandCreateEvent',
    'CustomerBillOnDemandStateChangeEvent',
    'CustomerBillStateChangeEvent',
  ],
};

const SUBSCRIPTIONS = '/internal/v1/eventSubscription';
/** Every live hub, newest first as served. */
export function loadSubscriptions(signal: AbortSignal) {
  return getAll<EventSubscription>(SUBSCRIPTIONS, signal);
}

/** The hub query for a choice of event types: none chosen means every event (no query). */
export function hubQuery(eventTypes: string[]): string | null {
  return eventTypes.length === 0 ? null : `eventType=${eventTypes.join(',')}`;
}

/** The POST body: the callback and, for a choice of events, the query. */
export function hubBody(callback: string, eventTypes: string[]): string {
  const query = hubQuery(eventTypes);
  return JSON.stringify(query === null ? { callback } : { callback, query });
}

export function createHub(api: HubApi, callback: string, eventTypes: string[]) {
  return postJson<{ id: string }>(`${api.base}/hub`, hubBody(callback, eventTypes));
}

/** DELETE /hub/{id} of the API that owns it (204, no body). */
export async function deleteHub(subscription: EventSubscription): Promise<void> {
  // parity: DELETE /tmf-api/{}/{}/hub/{}
  await request('DELETE', subscription.hub);
}

export interface Probe {
  ok: boolean;
  status: number;
  body: Record<string, unknown> | null;
}

/** GET a monitor endpoint that answers 503 with a body when unwell: never throws on a status. */
export async function probe(path: string, signal: AbortSignal): Promise<Probe> {
  const response = await fetch(path, { credentials: 'same-origin', signal });
  const body = await response.json().then(
    (parsed: unknown) => parsed as Record<string, unknown>,
    () => null,
  );
  return { ok: response.ok, status: response.status, body };
}
