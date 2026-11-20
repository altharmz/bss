import type { components as Internal } from './generated/internal';
import { accountPinHeader, getJson, getPage, request } from './client';

// Account security (docs/decisions/accounts.md, issue 226): the customer's account PIN status
// and its set/reset, the SIM swap and port-out locks of a line, and the read-only audit log.
// The PIN is never returned by the server and never kept here beyond the dialog that sends it.

const SECURITY = '/internal/v1/security';
export const AUDIT_PAGE = 25;

export type PinStatus = Internal['schemas']['AccountPin'];
export type LineLock = Internal['schemas']['LineLock'];
export type AuditEntry = Internal['schemas']['AuditEntry'];
export type AuditAction = AuditEntry['action'];
export type AuditOutcome = AuditEntry['outcome'];

export const PIN_PATTERN = /^[0-9]{6,8}$/;

export const AUDIT_ACTIONS: Record<AuditAction, string> = {
  sim_swap: 'SIM swap',
  number_change: 'Number change',
  number_transfer_pin: 'Number Transfer PIN',
  contact_change: 'Contact change',
  port_out: 'Port-out',
  pin_set: 'PIN set',
  pin_reset: 'PIN reset',
  lock_set: 'Lock set',
  lock_cleared: 'Lock cleared',
  pin_lockout: 'PIN lockout',
  e911_changed: 'E911 address changed',
  interaction_logged: 'Interaction logged',
  ticket_opened: 'Case opened',
  ticket_status_changed: 'Case status change',
  care_note_added: 'Care note added',
  care_sign_in: 'Self-care sign-in',
  care_locked: 'Self-care login locked',
  care_password_changed: 'Self-care password set',
  care_top_up: 'Self-care card top-up',
  care_card_saved: 'Self-care card saved',
  care_card_removed: 'Self-care card removed',
  care_auto_top_up: 'Self-care auto top-up set',
  care_add_on: 'Self-care add-on bought',
};

export const VERIFICATION: Record<AuditEntry['verification'], string> = {
  pin: 'PIN',
  notRequired: 'Not required',
  none: 'None',
  'password+code': 'Password and code',
  code: 'Emailed code',
  session: 'Self-care session',
};

export function loadPin(customerId: string, signal: AbortSignal): Promise<PinStatus> {
  return getJson<PinStatus>(`${SECURITY}/pin/${customerId}`, signal);
}

/** Sets or resets the PIN; the reason is recorded in the audit log. */
export async function setPin(customerId: string, pin: string, reason: string): Promise<PinStatus> {
  const response = await request('PUT', `${SECURITY}/pin/${customerId}`, {
    body: JSON.stringify({ pin, reason: reason.trim() }),
    headers: { 'Content-Type': 'application/json' },
  });
  return (await response.json()) as PinStatus;
}

export function loadLock(productId: string, signal: AbortSignal): Promise<LineLock> {
  return getJson<LineLock>(`${SECURITY}/lock/${productId}`, signal);
}

/** Both flags are sent (the new state); clearing a lock needs the customer's PIN. */
export async function setLock(
  productId: string,
  lock: { simSwap: boolean; portOut: boolean; reason: string },
  accountPin = '',
): Promise<LineLock> {
  const response = await request('PUT', `${SECURITY}/lock/${productId}`, {
    body: JSON.stringify({
      simSwap: lock.simSwap,
      portOut: lock.portOut,
      reason: lock.reason.trim(),
    }),
    headers: { 'Content-Type': 'application/json', ...accountPinHeader(accountPin) },
  });
  return (await response.json()) as LineLock;
}

export interface AuditFilter {
  customerId?: string;
  productId?: string;
  action: AuditAction | '';
  outcome: AuditOutcome | '';
}

export function auditQuery(filter: AuditFilter, offset: number): string {
  const query = new URLSearchParams();
  if (filter.customerId) query.set('customer.id', filter.customerId);
  if (filter.productId) query.set('product.id', filter.productId);
  if (filter.action) query.set('action', filter.action);
  if (filter.outcome) query.set('outcome', filter.outcome);
  query.set('offset', String(offset));
  query.set('limit', String(AUDIT_PAGE));
  return query.toString();
}

export function loadAudit(filter: AuditFilter, offset: number, signal: AbortSignal) {
  return getPage<AuditEntry>(`${SECURITY}/audit?${auditQuery(filter, offset)}`, signal);
}
