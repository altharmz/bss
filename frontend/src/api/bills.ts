import { getAll, getJson } from './client';
import { BILLS } from './bss';

// Global bills, bill cycles and rates (docs/ui.md): TMF678 reads
// only. The bill list filters on the server by state and billingAccount.id; the billing date
// is applied client side to the loaded rows (the API has no billingDate filter on bills).

export const BASE = '/tmf-api/customerBillManagement/v5';
export const BILL_CYCLES = `${BASE}/billCycle`;
export const RATES = `${BASE}/appliedCustomerBillingRate`;

export const BILL_STATES = [
  'new',
  'onHold',
  'validated',
  'sent',
  'partiallyPaid',
  'settled',
] as const;
export type BillStateFilter = (typeof BILL_STATES)[number];

interface Money {
  value: number;
  unit: string;
}

export interface BillRow {
  id: string;
  billNo: string;
  billDate: string;
  state: string;
  runType?: string;
  category?: string;
  amountDue: Money;
  billingAccount: { id: string; name?: string };
  billCycle?: { id: string };
}

export interface CycleRow {
  id: string;
  name: string;
  billingDate: string;
  paymentDueDate?: string;
  description?: string;
}

export interface RateRow {
  id: string;
  name?: string;
  description?: string;
  date?: string;
  appliedBillingRateType?: string;
  taxExcludedAmount?: Money;
  taxIncludedAmount?: Money;
  appliedTax?: { taxCategory?: string; taxRate?: number; taxAmount?: Money }[];
  periodCoverage?: { startDateTime?: string; endDateTime?: string };
  product?: { id: string; name?: string };
}

export interface BillFilter {
  account: string | null;
  state: BillStateFilter | null;
}

export function loadAllBills(filter: BillFilter, signal?: AbortSignal) {
  const parts = [
    ...(filter.account !== null ? [`billingAccount.id=${encodeURIComponent(filter.account)}`] : []),
    ...(filter.state !== null ? [`state=${filter.state}`] : []),
  ];
  return getAll<BillRow>(parts.length === 0 ? BILLS : `${BILLS}?${parts.join('&')}`, signal);
}

export function loadBillCycles(signal?: AbortSignal) {
  return getAll<CycleRow>(BILL_CYCLES, signal);
}

export function loadBillCycle(id: string, signal?: AbortSignal) {
  return getJson<Record<string, unknown>>(`${BILL_CYCLES}/${id}`, signal);
}

export function loadRates(billId: string, signal?: AbortSignal) {
  return getAll<RateRow>(`${RATES}?bill.id=${encodeURIComponent(billId)}`, signal);
}

export function loadRate(id: string, signal?: AbortSignal) {
  return getJson<Record<string, unknown>>(`${RATES}/${id}`, signal);
}
