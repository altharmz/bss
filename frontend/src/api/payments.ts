import { getAll, getJson } from './client';
import { PAYMENTS, REFUNDS } from './bss';

// Payments and refunds history (docs/ui.md): TMF676 reads only.
// Both resources have one status today ("done"); the filter still sends it, so a later
// status needs no change here.

export const PAYMENT_STATUSES = ['done'] as const;
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number];

interface Money {
  value: number;
  unit: string;
}

interface AccountRef {
  id: string;
  name?: string;
}

export interface PaymentItem {
  item: { id: string };
  amount: Money;
}

export interface PaymentRecord {
  id: string;
  paymentDate: string;
  status: string;
  account: AccountRef;
  amount: Money;
  taxAmount: Money;
  totalAmount: Money;
  paymentMethod: { name?: string; '@type'?: string };
  name?: string;
  description?: string;
  authorizationCode?: string;
  correlatorId?: string;
  channel?: { name?: string };
  paymentItem?: PaymentItem[];
}

export interface RefundRecord {
  id: string;
  refundDate: string;
  status: string;
  account: AccountRef;
  payment?: { id: string };
  totalAmount: Money;
  paymentMethod: { name?: string };
  description?: string;
  correlatorId?: string;
  authorizationCode?: string;
}

export interface HistoryFilter {
  account: string | null;
  status: PaymentStatus | null;
}

function query(base: string, filter: HistoryFilter): string {
  const parts = [
    ...(filter.account !== null ? [`account.id=${encodeURIComponent(filter.account)}`] : []),
    ...(filter.status !== null ? [`status=${filter.status}`] : []),
  ];
  return parts.length === 0 ? base : `${base}?${parts.join('&')}`;
}

export function loadPayments(filter: HistoryFilter, signal?: AbortSignal) {
  return getAll<PaymentRecord>(query(PAYMENTS, filter), signal);
}

export function loadRefundHistory(filter: HistoryFilter, signal?: AbortSignal) {
  return getAll<RefundRecord>(query(REFUNDS, filter), signal);
}

export function loadPayment(id: string, signal?: AbortSignal) {
  return getJson<PaymentRecord>(`${PAYMENTS}/${id}`, signal);
}

/** What the operator typed or the bank returned to identify the payment. */
export function referenceOf(row: {
  correlatorId?: string;
  authorizationCode?: string;
  name?: string;
}): string {
  return row.correlatorId ?? row.authorizationCode ?? row.name ?? '—';
}
