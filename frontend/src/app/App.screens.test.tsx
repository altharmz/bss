import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, pending, stubFetch } from '../test/fetch';
import { RUN } from '../test/fixtures';
import { App } from './App';

// The screens App.test.tsx does not open: each route shows its own screen inside the
// shell. Their data stays in flight (the screens have their own tests); only which screen
// a path opens matters here.
const ID = '1f3a9c2b-0000-4000-8000-000000000001';

function start(path: string) {
  stubFetch({
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    'GET /internal/v1/context': () => json(CONTEXT),
    [NAV_RUNS]: () => json([{ ...RUN, status: 'completed' }], { total: 1 }),
    'GET /*': pending,
  });
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('App screens', () => {
  it.each([
    ['/numbers', 'Numbers', 'ui.numbers.list'],
    ['/sims', 'SIMs', 'ui.sims.list'],
    ['/ports', 'Port requests', 'ui.ports.list'],
    ['/reports', 'Prepaid KPIs', 'ui.reports'],
    ['/signups', 'Signups', null],
    [`/wholesale/${ID}`, 'Wholesale invoice', 'ui.wholesale.detail'],
  ])('opens %s on its own screen', async (path, title, marker) => {
    start(path);
    const main = await screen.findByRole('main');
    expect(await within(main).findByRole('heading', { level: 1, name: title })).toBeTruthy();
    if (marker) expectUi(marker);
  });

  it('opens the UI kit gallery', async () => {
    start('/ui-kit');
    await screen.findByRole('navigation', { name: 'Main' });
    expectUi('ui.kit');
  });
});
