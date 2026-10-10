import { expect, TestRunner } from 'vitest';

// expectUi(id): asserts the UI shows the element carrying data-ui="<id>" (the progress
// tracker's marker, docs/progress/scope.yaml) and records the id on the running test.
// The ui-tested reporter (scripts/ui-tested-reporter.mjs, used by make progress) lists
// the ids of the tests that passed: a ui item counts as tested when a passing test has
// seen its marker on screen, not when the marker merely exists in the source.

declare module 'vitest' {
  interface TaskMeta {
    uiTested?: string[];
  }
}

export function expectUi(id: string): HTMLElement {
  const found = document.querySelectorAll<HTMLElement>(`[data-ui="${CSS.escape(id)}"]`);
  expect(found.length, `an element with data-ui="${id}"`).toBeGreaterThan(0);
  const test = TestRunner.getCurrentTest();
  if (test) {
    const seen = new Set(test.meta.uiTested ?? []);
    seen.add(id);
    test.meta.uiTested = [...seen].sort();
  }
  return found[0] as HTMLElement;
}
