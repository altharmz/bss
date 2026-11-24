import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// The Dunning screen under Billing (#386, docs/ui.md): the accounts in dunning with a step
// filter on the server, and the deactivation candidates.

const LIST = '/internal/v1/dunning/accounts';
const PAGE = 'offset=0&limit=50';
const A1 = 'a1000000-0000-4000-8000-000000000001';
const A2 = 'a2000000-0000-4000-8000-000000000002';
const usd = (value: number) => ({ unit: 'USD', value });

const OLGA = {
  billingAccount: { id: A1, name: 'Olga Kuznetsova' },
  overdueSince: '2026-07-01',
  overdueAmount: usd(1290.5),
  lastStep: 'suspend',
  suspendedSince: '2026-07-15',
  deactivationCandidate: true,
};
const IVAN = {
  billingAccount: { id: A2, name: 'Ivan Ivanov' },
  overdueSince: '2026-09-20',
  overdueAmount: usd(30),
  lastStep: 'reminder1',
  suspendedSince: null,
  deactivationCandidate: false,
};

function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    ...extra,
  });
}

async function open() {
  window.history.replaceState(null, '', '/dunning');
  render(<App />);
  await screen.findByRole('heading', { name: /^Dunning/ });
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name)) as HTMLElement;

describe('dunning screen', () => {
  it('lists the accounts in dunning oldest first, linking to the subscriber card', async () => {
    backend({ [`GET ${LIST}?${PAGE}`]: () => json([OLGA, IVAN]) });
    await open();
    await screen.findByText('Olga Kuznetsova');
    expectUi('ui.dunning');
    const olga = rowOf('Olga Kuznetsova');
    expect(within(olga).getByRole('link', { name: 'Olga Kuznetsova' }).getAttribute('href')).toBe(
      `/subscribers/${A1}#bills`,
    );
    expect(within(olga).getByText('2026-07-01')).toBeTruthy();
    expect(within(olga).getByText('1,290.50')).toBeTruthy();
    expect(within(olga).getByText('Suspended')).toBeTruthy();
    expect(within(olga).getByText('Candidate')).toBeTruthy();
    expect(within(olga).getByText('2026-07-15')).toBeTruthy();
    const ivan = rowOf('Ivan Ivanov');
    expect(within(ivan).getByText('First reminder')).toBeTruthy();
    expect(within(ivan).queryByText('Candidate')).toBeNull();
    const rows = screen.getAllByRole('row');
    expect(rows.indexOf(olga)).toBeLessThan(rows.indexOf(ivan)); // as served
    // the sidebar's Billing section links to the screen
    expect(screen.getByRole('link', { name: /^Dunning/ }).getAttribute('href')).toBe('/dunning');
  });

  it('filters by the last step on the server and resets the filter', async () => {
    const { calls } = backend({
      [`GET ${LIST}?${PAGE}`]: () => json([OLGA, IVAN]),
      [`GET ${LIST}?step=reminder1&${PAGE}`]: () => json([IVAN]),
      [`GET ${LIST}?step=none&${PAGE}`]: () => json([]),
    });
    await open();
    await screen.findByText('Olga Kuznetsova');
    const steps = within(screen.getByRole('group', { name: 'Last step' }));
    fireEvent.click(steps.getByRole('button', { name: 'First reminder' }));
    await waitFor(() => expect(screen.queryByText('Olga Kuznetsova')).toBeNull());
    expect(screen.getByText('Ivan Ivanov')).toBeTruthy();
    fireEvent.click(steps.getByRole('button', { name: 'No step yet' }));
    fireEvent.click(await screen.findByRole('button', { name: /Reset filters/ }));
    await screen.findByText('Olga Kuznetsova');
    expect(calls).toContain(`GET ${LIST}?step=reminder1&${PAGE}`);
    expect(calls).toContain(`GET ${LIST}?step=none&${PAGE}`);
  });

  it('lists the deactivation candidates on their own tab', async () => {
    backend({
      [`GET ${LIST}?${PAGE}`]: () => json([OLGA, IVAN]),
      [`GET ${LIST}?deactivationCandidate=true&${PAGE}`]: () => json([OLGA]),
    });
    await open();
    await screen.findByText('Ivan Ivanov');
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Dunning lists' })).getByRole('button', {
        name: 'Deactivation candidates',
      }),
    );
    await waitFor(() => expect(screen.queryByText('Ivan Ivanov')).toBeNull());
    expect(screen.getByText('Olga Kuznetsova')).toBeTruthy();
    expect(screen.getByText(/Suspended for more than 60 days/)).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Last step' })).toBeNull();
  });

  it('pages with Next while the server says there is more', async () => {
    const { calls } = backend({
      [`GET ${LIST}?${PAGE}`]: () => json([OLGA], { more: true }),
      [`GET ${LIST}?offset=50&limit=50`]: () => json([IVAN]),
    });
    await open();
    await screen.findByText('Olga Kuznetsova');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Ivan Ivanov');
    expect(calls).toContain(`GET ${LIST}?offset=50&limit=50`);
  });

  it('shows the empty state and retries a failed read', async () => {
    let reads = 0;
    backend({
      [`GET ${LIST}?${PAGE}`]: () => (++reads === 1 ? tmfError(503, 'Unavailable') : json([])),
    });
    await open();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No accounts in dunning')).toBeTruthy();
  });
});
