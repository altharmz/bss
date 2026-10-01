import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Testing Library only cleans up on its own when the test runner exposes globals; ours
// does not (explicit imports), so unmount after every test here. Fetch stubs and fake
// timers never leak into the next test either.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
});
