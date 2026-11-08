import type { components } from './generated/internal';
import { getJson, request } from './client';

export type LifecycleSettings = components['schemas']['LifecycleSettings'];
export const LIFECYCLE_SETTINGS = '/internal/v1/lifecycle/settings';

export function loadLifecycleSettings(signal: AbortSignal) {
  return getJson<LifecycleSettings>(LIFECYCLE_SETTINGS, signal);
}

export async function saveLifecycleSettings(body: Partial<Omit<LifecycleSettings, 'updatedAt'>>) {
  await request('PUT', LIFECYCLE_SETTINGS, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
