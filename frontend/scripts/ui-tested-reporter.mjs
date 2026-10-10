// A vitest reporter for the progress tracker (make progress): writes the data-ui ids
// that passing tests saw on screen through expectUi (src/test/expectUi.ts) as a sorted
// JSON array to the file named by UI_TESTED_OUT. A test that failed proves nothing, even
// if it called expectUi before failing. vite.config.ts adds this reporter only when
// UI_TESTED_OUT is set, so the normal test runs are unchanged.
import { writeFileSync } from 'node:fs';

export default class UiTestedReporter {
  onTestRunEnd(testModules) {
    const ids = new Set();
    for (const module of testModules) {
      for (const test of module.children.allTests()) {
        if (test.result().state !== 'passed') continue;
        for (const id of test.meta().uiTested ?? []) ids.add(id);
      }
    }
    writeFileSync(process.env.UI_TESTED_OUT, JSON.stringify([...ids].sort(), null, 2) + '\n');
  }
}
