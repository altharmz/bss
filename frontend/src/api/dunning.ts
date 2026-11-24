import type { components } from './generated/internal';
import { AMOUNT, getJson, getPage, jsonWithAmount, patchJson } from './client';

// Postpaid dunning (docs/decisions/dunning.md, the UI is #386): the settings, the accounts in
// dunning and one account's state and exemption. Nothing is derived here: the server computes
// O, the steps and the next step, the UI shows what it answers.

export type DunningSettings = components['schemas']['DunningSettings'];
export type DunningAccount = components['schemas']['DunningAccount'];
export type AccountDunning = components['schemas']['AccountDunning'];
export type DunningStep = components['schemas']['DunningStep'];
export type LastStep = DunningAccount['lastStep'];

export const DUNNING_SETTINGS = '/internal/v1/dunning/settings';
export const DUNNING_ACCOUNTS = '/internal/v1/dunning/accounts';
export const DUNNING_PAGE = 50;
export const LAST_STEPS: LastStep[] = ['none', 'reminder1', 'reminder2', 'suspend'];

export const STEP_LABEL: Record<LastStep, string> = {
  none: 'No step yet',
  reminder1: 'First reminder',
  reminder2: 'Second reminder',
  suspend: 'Suspended',
};

export function accountDunningPath(accountId: string): string {
  return `/internal/v1/billingAccount/${encodeURIComponent(accountId)}/dunning`;
}

export function loadDunningSettings(signal: AbortSignal) {
  return getJson<DunningSettings>(DUNNING_SETTINGS, signal);
}

export interface DunningChange {
  reminder1Days?: number;
  reminder2Days?: number;
  suspendDays?: number;
  /** canonical decimal string ("7.50"), sent as an exact JSON number */
  minimumOverdue?: string;
}

/** The PATCH body of the changed settings only; the minimum is an exact number token. */
export function dunningSettingsBody(change: DunningChange): string {
  const { minimumOverdue, ...days } = change;
  if (minimumOverdue === undefined) return JSON.stringify(days);
  return jsonWithAmount({ ...days, minimumOverdue: AMOUNT }, minimumOverdue);
}

export function saveDunningSettings(change: DunningChange): Promise<DunningSettings> {
  return patchJson<DunningSettings>(DUNNING_SETTINGS, dunningSettingsBody(change));
}

export interface DunningFilter {
  step: LastStep | null;
  candidates: boolean;
}

/** One page of the accounts in dunning, oldest O first (the server's order). */
export async function loadDunningAccounts(
  filter: DunningFilter,
  offset: number,
  signal?: AbortSignal,
): Promise<{ items: DunningAccount[]; more: boolean; offset: number }> {
  const query = new URLSearchParams();
  if (filter.step !== null) query.set('step', filter.step);
  if (filter.candidates) query.set('deactivationCandidate', 'true');
  query.set('offset', String(offset));
  query.set('limit', String(DUNNING_PAGE));
  return { ...(await getPage<DunningAccount>(`${DUNNING_ACCOUNTS}?${query}`, signal)), offset };
}

export function loadAccountDunning(accountId: string, signal: AbortSignal) {
  return getJson<AccountDunning>(accountDunningPath(accountId), signal);
}

export function exemptionBody(exempt: boolean, reason: string) {
  return reason.trim() === '' ? { exempt } : { exempt, reason: reason.trim() };
}

export function setDunningExemption(
  accountId: string,
  exempt: boolean,
  reason: string,
): Promise<AccountDunning> {
  return patchJson<AccountDunning>(accountDunningPath(accountId), exemptionBody(exempt, reason));
}
