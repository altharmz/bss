import type { components } from './generated/internal';
import { getJson, request } from './client';

export type LogSettings = components['schemas']['LogSettings'];
export const LOGGING = '/internal/v1/logging';

export function loadLogging(signal: AbortSignal) {
  return getJson<LogSettings>(LOGGING, signal);
}

/** The three PUT bodies the screen sends (pinned in tests/internal/test_ui_requests.py). */
export type LogChange =
  { baseLevel: 'WARNING' | 'INFO' | null } | { debugMinutes: 15 | 30 | 60 } | { debugMinutes: 0 };

export async function saveLogging(body: LogChange) {
  await request('PUT', LOGGING, {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
