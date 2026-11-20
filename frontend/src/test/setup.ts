import { cleanup, configure } from '@testing-library/react';
import { afterEach, expect, vi } from 'vitest';
import { resetReadGate } from '../api/client';
import { takeUnexpectedRequests } from './fetch';
import { unlabeledControls } from './unlabeled';

// findBy/waitFor give up after asyncUtilTimeout of wall-clock time. Testing Library's
// 1 s default made Catalog's first test flaky ("no row Mobile L", 3.5 s, in the single
// vitest pass of make progress): the first render of a file pays every cold cost (stubbed
// fetches, JSON, Intl formatters, the table), and on a loaded workstation that took longer
// than 1 s. The timeout guards against a hang, not a slow machine: no test waits for it
// to expire. vite.config.ts keeps testTimeout above it, so a real miss still reports the
// query's own error.
configure({ asyncUtilTimeout: 5000 });

// Testing Library only cleans up on its own when the test runner exposes globals; ours
// does not (explicit imports), so unmount after every test here. Fetch stubs and fake
// timers never leak into the next test either.
afterEach(() => {
  const unexpected = takeUnexpectedRequests();
  const unlabeled = unlabeledControls();
  cleanup();
  vi.unstubAllGlobals();
  resetReadGate(); // a `pending` stub holds its read slot for good
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
  expect(unexpected, 'requests without a stub (src/test/fetch.ts)').toEqual([]);
  expect(unlabeled, 'form controls without a label or aria name').toEqual([]);
});
