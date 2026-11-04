import { vi } from 'vitest';

/** Stubs window.matchMedia so `(min-width: 1536px)` matches when `wide`. */
export function mockWidth(wide: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('1536px') ? wide : false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}
