// Billing settings (TMF666 billingCycleSpecification, billFormat, billPresentationMedia):
// the lists and the writes of the Billing settings screen. Design:
// docs/next-slices-design.md section 18; rules in docs/decisions/accounts.md.

import { getAll, patchJson, postJson, request } from './client';
import { BUILT_IN_CYCLE, type Account } from './bss';

export type SettingKind = 'cycles' | 'formats' | 'media';

const BASE = '/tmf-api/accountManagement/v5';

export const SETTING_PATH: Record<SettingKind, string> = {
  cycles: `${BASE}/billingCycleSpecification`,
  formats: `${BASE}/billFormat`,
  media: `${BASE}/billPresentationMedia`,
};

const TYPE: Record<SettingKind, string> = {
  cycles: 'BillingCycleSpecification',
  formats: 'BillFormat',
  media: 'BillPresentationMedia',
};

/** The names the API stores for a bill format (it refuses any other). */
export const FORMAT_NAMES = ['JSON detailed', 'JSON summary'] as const;

export interface Setting {
  id: string;
  name: string;
  description: string | null;
  /** the built-in monthly specification: shown, never written */
  builtIn: boolean;
}

interface Body {
  id?: string;
  name?: string;
  description?: string;
}

export async function loadSettings(kind: SettingKind, signal: AbortSignal): Promise<Setting[]> {
  const rows = await getAll<Body>(SETTING_PATH[kind], signal);
  return rows.map((row) => ({
    id: row.id ?? '',
    name: row.name ?? '',
    description: row.description ?? null,
    builtIn: kind === 'cycles' && row.id === BUILT_IN_CYCLE,
  }));
}

/** How many of the loaded accounts name this setting. */
export function usedBy(kind: SettingKind, id: string, accounts: Account[]): number {
  return accounts.filter((account) => {
    const b = account.billing;
    if (!b) return false;
    if (kind === 'cycles') return b.cycleId === id;
    if (kind === 'formats') return b.formatId === id;
    return b.mediaIds.includes(id);
  }).length;
}

export interface SettingForm {
  name: string;
  description: string;
}

export function formOf(setting: Setting | null): SettingForm {
  return { name: setting?.name ?? '', description: setting?.description ?? '' };
}

export function createBody(kind: SettingKind, form: SettingForm): string {
  const body: Record<string, unknown> = { '@type': TYPE[kind], name: form.name.trim() };
  if (form.description.trim()) body.description = form.description.trim();
  return JSON.stringify(body);
}

/** The merge patch of only the changed fields (a cleared description is null); null =
 * nothing changed. */
export function patchBody(kind: SettingKind, before: Setting, after: SettingForm): string | null {
  const body: Record<string, unknown> = { '@type': TYPE[kind] };
  if (after.name.trim() !== before.name) body.name = after.name.trim();
  if (after.description.trim() !== (before.description ?? ''))
    body.description = after.description.trim() || null;
  return Object.keys(body).length === 1 ? null : JSON.stringify(body);
}

export async function createSetting(kind: SettingKind, body: string): Promise<void> {
  await postJson(SETTING_PATH[kind], body);
}

export async function patchSetting(kind: SettingKind, id: string, body: string): Promise<void> {
  await patchJson(`${SETTING_PATH[kind]}/${id}`, body);
}

export async function deleteSetting(kind: SettingKind, id: string): Promise<void> {
  await request('DELETE', `${SETTING_PATH[kind]}/${id}`);
}
