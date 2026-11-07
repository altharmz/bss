import { getAll, getJson, postJson } from './client';

// Number pool, port requests and Number Transfer PINs (docs/decisions/numbers.md): ours, not
// TMF, so all under /internal/v1. The API never contacts the carrier; the billing worker does.
const BASE = '/internal/v1';

export const PORT_STATES = ['submitted', 'pending', 'rejected', 'completed', 'cancelled'] as const;
export type PortState = (typeof PORT_STATES)[number];
export type PortDirection = 'in' | 'out';

export interface PortRequest {
  id: string;
  direction: PortDirection;
  state: PortState;
  number: string;
  areaCode: string | null;
  carrierAccountNumber: string | null;
  billingZip: string | null;
  firstName: string | null;
  lastName: string | null;
  carrierReference: string | null;
  submissions: number;
  rejectionCode: string | null;
  rejectionReason: string | null;
  attempts: number;
  nextAttemptAt: string | null;
  lastError: string | null;
  resource: { id: string } | null;
  product: { id: string } | null;
  billingAccount: { id: string } | null;
  productOrder: { id: string } | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

export interface PortFilters {
  direction: '' | PortDirection;
  state: '' | PortState;
  number: string;
}

export interface PortInData {
  number: string;
  carrierAccountNumber: string;
  transferPin: string;
  billingZip: string;
  firstName: string;
  lastName: string;
}

export interface PortCorrection {
  transferPin: string;
  carrierAccountNumber?: string;
  billingZip?: string;
  firstName?: string;
  lastName?: string;
}

export interface TransferPin {
  id: string;
  state: 'active' | 'used' | 'revoked' | 'expired';
  number: string;
  createdAt: string;
  expiresAt: string;
  /** only in the response that creates it: the server keeps a hash */
  pin?: string;
}

export interface PoolResult {
  areaCode: string;
  requested: number;
  numbers: { id: string; value: string }[];
}

export function loadPorts(filters: PortFilters, signal: AbortSignal): Promise<PortRequest[]> {
  const query = new URLSearchParams();
  if (filters.direction) query.set('direction', filters.direction);
  if (filters.state) query.set('state', filters.state);
  if (filters.number) query.set('number', filters.number);
  return getAll<PortRequest>(`${BASE}/port?${query}`, signal);
}

export function loadPort(id: string, signal: AbortSignal): Promise<PortRequest> {
  return getJson<PortRequest>(`${BASE}/port/${id}`, signal);
}

export function createPortIn(data: PortInData): Promise<PortRequest> {
  return postJson<PortRequest>(`${BASE}/port/in`, data);
}

export function resubmitPort(id: string, data: PortCorrection): Promise<PortRequest> {
  return postJson<PortRequest>(`${BASE}/port/${id}/resubmit`, data);
}

export function cancelPort(id: string): Promise<PortRequest> {
  return postJson<PortRequest>(`${BASE}/port/${id}/cancel`, {});
}

export function requestPool(areaCode: string, count: number): Promise<PoolResult> {
  return postJson<PoolResult>(`${BASE}/number/pool`, { areaCode, count });
}

/** A prepaid line needs the customer's account PIN, sent as a header and never stored. */
export function createPin(productId: string, accountPin = ''): Promise<TransferPin> {
  return postJson<TransferPin>(
    `${BASE}/port/pin`,
    { productId },
    accountPin ? { 'X-Account-Pin': accountPin } : {},
  );
}

export function loadPins(productId: string, signal: AbortSignal): Promise<TransferPin[]> {
  return getAll<TransferPin>(`${BASE}/port/pin?product.id=${productId}`, signal);
}
