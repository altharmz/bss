import { getAll, getJson, getPage } from './client';
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

/** One account's payments, every page (the subscriber card). */
export function loadPayments(account: string, signal?: AbortSignal) {
  return getAll<PaymentRecord>(query(PAYMENTS, { account, status: null }), signal);
}

export const PAYMENT_PAGE = 50;

type Paged<T> = { items: T[]; more: boolean; offset: number };

function pageQuery(filter: HistoryFilter, offset: number): string {
  const query = new URLSearchParams();
  if (filter.account !== null) query.set('account.id', filter.account);
  if (filter.status !== null) query.set('status', filter.status);
  query.set('offset', String(offset));
  query.set('limit', String(PAYMENT_PAGE));
  return query.toString();
}

/** One page of payments, newest first on the server (#314). */
export async function loadPaymentPage(
  filter: HistoryFilter,
  offset: number,
  signal?: AbortSignal,
): Promise<Paged<PaymentRecord>> {
  const page = await getPage<PaymentRecord>(`${PAYMENTS}?${pageQuery(filter, offset)}`, signal);
  return { ...page, offset };
}

/** One page of refunds, newest first on the server (#314). */
export async function loadRefundPage(
  filter: HistoryFilter,
  offset: number,
  signal?: AbortSignal,
): Promise<Paged<RefundRecord>> {
  const page = await getPage<RefundRecord>(`${REFUNDS}?${pageQuery(filter, offset)}`, signal);
  return { ...page, offset };
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
