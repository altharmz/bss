// Work the API accepted and the billing worker finishes within about a minute (an accepted
// transfer, a prepaid order). The subscriber screen follows each one: every 5 s, for at most
// 3 minutes from the submission (docs/decisions/frontend.md P6c).

export interface Watched {
  kind: 'transfer' | 'order';
  id: string;
  label: string;
  until: number; // epoch ms: the polling stops here
}

export const POLL_MS = 5_000;
export const POLL_CAP_MS = 3 * 60_000;

/** Call from an event handler, not while rendering: it reads the clock. */
export function watch(kind: Watched['kind'], id: string, label: string): Watched {
  return { kind, id, label, until: Date.now() + POLL_CAP_MS };
}
