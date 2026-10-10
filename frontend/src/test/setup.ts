import { cleanup } from '@testing-library/react';
import { afterEach, expect, vi } from 'vitest';
import { takeUnexpectedRequests } from './fetch';

// Testing Library only cleans up on its own when the test runner exposes globals; ours
// does not (explicit imports), so unmount after every test here. Fetch stubs and fake
// timers never leak into the next test either.
afterEach(() => {
  const unexpected = takeUnexpectedRequests();
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
  expect(unexpected, 'requests without a stub (src/test/fetch.ts)').toEqual([]);
});
