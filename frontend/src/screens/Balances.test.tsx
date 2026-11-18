import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, type Handler } from '../test/fetch';

// The global Balances screen (slice 22). Reads only, plus the Balance action dialog's writes
// (pinned in SubscriberCard.test.tsx). The query strings asserted here are replayed against the
// real API by tests/test_ui_requests.py - change both together.

const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
const BUCKETS = `${PREPAY}/bucket`;
const ACC = `${PREPAY}/accumulatedBalance`;
/** a page of the buckets and totals lists (#314) */
const LIST = 'offset=0&limit=50';
const A1 = 'c0000001-0000-4000-8000-000000000001';
const A2 = 'c0000002-0000-4000-8000-000000000002';
const B1 = 'b0000001-0000-4000-8000-000000000001';
const B2 = 'b0000002-0000-4000-8000-000000000002';
const AB1 = 'd0000001-0000-4000-8000-000000000001';
const AB2 = 'd0000002-0000-4000-8000-000000000002';
const T1 = 'a1000000-0000-4000-8000-000000000001';
const X1 = 'a2000000-0000-4000-8000-000000000002';
const OLGA = { id: A1, name: 'Olga Kuznetsova' };
const JAN = { id: A2, name: 'Jan Petrov' };
const money = (value: number) => ({ amount: value, units: 'USD' });

const bucket = (
  id: string,
  party: { id: string; name: string },
  status: string,
  remaining: number,
) => ({
  '@type': 'Bucket',
  id,
  status,
  partyAccount: party,
  remainingValue: money(remaining),
  reservedValue: money(5),
});
const accumulated = (id: string, party: { id: string; name: string }, total: number) => ({
  '@type': 'AccumulatedBalance',
  id,
  partyAccount: party,
  totalBalance: money(total),
});
const B_OLGA = bucket(B1, OLGA, 'active', 120);
const B_JAN = bucket(B2, JAN, 'suspended', 40);
const AB_OLGA = accumulated(AB1, OLGA, 125);
const AB_JAN = accumulated(AB2, JAN, 40);

const topup = {
  '@type': 'TopupBalance',
  id: T1,
  status: 'completed',
  requestedDate: '2026-10-01T05:00:00Z',
  confirmationDate: '2026-10-01T05:00:01Z',
  amount: money(50),
  reason: 'Cash at the desk',
  partyAccount: OLGA,
  bucket: { id: B1 },
  paymentMethod: { id: 'cash', name: 'cash' },
};
const transfer = {
  '@type': 'TransferBalance',
  id: X1,
  status: 'completed',
  requestedDate: '2026-10-01T08:00:00Z',
  amount: money(30),
  partyAccount: OLGA,
  bucket: { id: B1 },
  receiverBucket: { id: B2 },
  transferCost: { unit: 'USD', value: 1 },
  costOwner: 'originator',
};

function backend(routes: Record<string, Handler>) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    // a bucket page joins each of its accounts' accumulated balance (#314)
    [`GET ${ACC}?partyAccount.id=${A1}&limit=1`]: () => json([AB_OLGA]),
    [`GET ${ACC}?partyAccount.id=${A2}&limit=1`]: () => json([AB_JAN]),
    ...routes,
  });
}

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

const tab = (name: string) => fireEvent.click(screen.getByRole('button', { name }));

describe('balances screen', () => {
  it('lists independent account totals, filters by account and GETs fresh detail', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?partyAccount.id=${A1}&${LIST}`]: () => json([], { total: 0 }),
      [`GET ${ACC}?partyAccount.id=${A1}&${LIST}`]: () => json([AB_OLGA], { total: 1 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA, AB_JAN], { total: 2 }),
      [`GET ${ACC}/${AB1}`]: () => json({ ...AB_OLGA, totalBalance: money(130) }),
    });
    open(`/balances?account=${A1}`);
    // TEST-CHANGE (#314): filtered on the server, an empty page under the chip is no matches
    await screen.findByText('No matches');
    tab('Account totals');
    expect(await screen.findByText('125.00')).toBeTruthy();
    expectUi('ui.balances.totals');
    expect(screen.queryByLabelText('Status')).toBeNull();
    expect(screen.queryByText('Jan Petrov')).toBeNull();
    fireEvent.click(screen.getByText('125.00'));
    const dialog = await screen.findByRole('dialog', { name: 'Account total' });
    expect(await within(dialog).findByText('130.00')).toBeTruthy();
    expectUi('ui.balances.totalDetail');
    expect(calls).toContain(`GET ${ACC}/${AB1}`);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByLabelText('Remove the account filter'));
    expect(await screen.findByText('Jan Petrov')).toBeTruthy();
    expect(calls).toContain(`GET ${ACC}?${LIST}`);
  });
  it('lists the buckets joined with each account accumulated balance', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA, B_JAN], { total: 2 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA, AB_JAN], { total: 2 }),
    });
    open('/balances');
    expect(await screen.findByText('Olga Kuznetsova')).toBeTruthy();
    expectUi('ui.balances');
    expectUi('ui.balances.buckets');
    expect(screen.getByText('120.00')).toBeTruthy();
    expect(screen.getByText('125.00')).toBeTruthy();
    expect(screen.getByText('Suspended', { selector: 'span' })).toBeTruthy();
    // the buckets tab reads buckets and balances only: no action list
    expect(calls).toContain(`GET ${BUCKETS}?${LIST}`);
    // TEST-CHANGE (#314): one accumulated balance read per account on the page, not all
    expect(calls).toContain(`GET ${ACC}?partyAccount.id=${A1}&limit=1`);
    expect(calls).toContain(`GET ${ACC}?partyAccount.id=${A2}&limit=1`);
    expect(calls).not.toContain(`GET ${ACC}?${LIST}`);
    expect(calls.filter((c) => /(topup|adjust|reserve|transfer)Balance/.test(c))).toEqual([]);
  });

  it('filters buckets on the server by status and by the account chip', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA, B_JAN], { total: 2 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA, AB_JAN], { total: 2 }),
      [`GET ${BUCKETS}?status=suspended&${LIST}`]: () => json([B_JAN], { total: 1 }),
      [`GET ${BUCKETS}?partyAccount.id=${A1}&${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?partyAccount.id=${A1}&${LIST}`]: () => json([AB_OLGA], { total: 1 }),
    });
    open('/balances');
    expect(await screen.findByText('Jan Petrov')).toBeTruthy();
    expect(screen.getByLabelText('Status')).toBe(expectUi('ui.balances.status'));
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'suspended' } });
    await waitFor(() => expect(calls).toContain(`GET ${BUCKETS}?status=suspended&${LIST}`));
    expect(await screen.findByText('Jan Petrov')).toBeTruthy();
    expect(screen.queryByText('Olga Kuznetsova')).toBeNull();
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    expect(await screen.findByText('Olga Kuznetsova')).toBeTruthy();
  });

  it('filters on the account chip from the subscriber card link and removes it', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA, B_JAN], { total: 2 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA, AB_JAN], { total: 2 }),
      [`GET ${BUCKETS}?partyAccount.id=${A1}&${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?partyAccount.id=${A1}&${LIST}`]: () => json([AB_OLGA], { total: 1 }),
    });
    open(`/balances?account=${A1}`);
    expect(await screen.findByText('Olga Kuznetsova')).toBeTruthy();
    expectUi('ui.balances.accountChip');
    expect(calls).toContain(`GET ${BUCKETS}?partyAccount.id=${A1}&${LIST}`);
    expect(calls).toContain(`GET ${ACC}?partyAccount.id=${A1}&limit=1`);
    expect(screen.queryByText('Jan Petrov')).toBeNull();
    fireEvent.click(screen.getByLabelText('Remove the account filter'));
    expect(await screen.findByText('Jan Petrov')).toBeTruthy();
    expect(calls).toContain(`GET ${BUCKETS}?${LIST}`);
  });

  it('pages buckets and totals with Previous and Next', async () => {
    const many = Array.from({ length: 51 }, (_, i) =>
      bucket(`b${i}`, i === 50 ? JAN : OLGA, 'active', i),
    );
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json(many.slice(0, 50), { more: true }),
      [`GET ${BUCKETS}?offset=50&limit=50`]: () => json(many.slice(50)),
    });
    open('/balances');
    expect(await screen.findByText('Showing 1–50')).toBeTruthy();
    expectUi('ui.balances.previous');
    expectUi('ui.balances.next');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 51–51')).toBeTruthy();
    expect(screen.getByText('Jan Petrov')).toBeTruthy();
    expect(calls).toContain(`GET ${BUCKETS}?offset=50&limit=50`);
  });

  it('opens a bucket with the bucket GET and its accumulated balance GET', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA], { total: 1 }),
      [`GET ${BUCKETS}/${B1}`]: () => json(B_OLGA),
      [`GET ${ACC}/${AB1}`]: () => json(AB_OLGA),
    });
    open('/balances');
    // the account name is a link of its own; the row opens from another cell
    fireEvent.click(await screen.findByText('120.00'));
    const dialog = await screen.findByRole('dialog', { name: 'Bucket' });
    expectUi('ui.balances.bucketDetail');
    expect(await within(dialog).findByText('125.00')).toBeTruthy();
    expect(calls).toContain(`GET ${BUCKETS}/${B1}`);
    expect(calls).toContain(`GET ${ACC}/${AB1}`);
  });

  it.each([
    ['Top-ups', 'topupBalance', 'ui.balances.topups'],
    ['Adjustments', 'adjustBalance', 'ui.balances.adjustments'],
    ['Holds', 'reserveBalance', 'ui.balances.holds'],
    ['Transfers', 'transferBalance', 'ui.balances.transfers'],
  ])('%s reads its own list only', async (name, path, ui) => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA], { total: 1 }),
      [`GET ${PREPAY}/${path}?${LIST}`]: () => json([], { total: 0 }),
    });
    open('/balances');
    await screen.findByText('Olga Kuznetsova');
    tab(name);
    expect(await screen.findByText('Nothing here')).toBeTruthy();
    expectUi(ui);
    expect(calls).toContain(`GET ${PREPAY}/${path}?${LIST}`);
    // the other kinds are not read until their tab is opened
    const others = ['topupBalance', 'adjustBalance', 'reserveBalance', 'transferBalance'].filter(
      (p) => p !== path,
    );
    expect(calls.filter((c) => others.some((p) => c.includes(p)))).toEqual([]);
  });

  it('lists top-ups with a status filter, and cancels one from its detail', async () => {
    const { calls, fetchMock } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA], { total: 1 }),
      [`GET ${PREPAY}/topupBalance?${LIST}`]: () => json([topup], { total: 1 }),
      [`GET ${PREPAY}/topupBalance?status=failed&${LIST}`]: () => json([], { total: 0 }),
      [`GET ${PREPAY}/topupBalance/${T1}`]: () => json(topup),
      [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => json({ ...topup, status: 'cancelled' }),
    });
    open('/balances');
    await screen.findByText('Olga Kuznetsova');
    tab('Top-ups');
    fireEvent.click(await screen.findByText('Top-up'));
    const dialog = await screen.findByRole('dialog', { name: 'Balance action' });
    expectUi('ui.subscriber.prepaid.historyDetail');
    expect(await within(dialog).findByText('Cash at the desk', { exact: false })).toBeTruthy();
    expect(calls).toContain(`GET ${PREPAY}/topupBalance/${T1}`);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByText('Top-up cancelled')).toBeTruthy();
    const writes = fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'PATCH')
      .map(([url, init]) => [String(url), String(init?.body)]);
    expect(writes).toEqual([[`${PREPAY}/topupBalance/${T1}`, '{"status":"cancelled"}']]);
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'failed' } });
    await waitFor(() =>
      expect(calls).toContain(`GET ${PREPAY}/topupBalance?status=failed&${LIST}`),
    );
  });

  it('shows a transfer to the receiver named through its receiver bucket', async () => {
    const { calls } = backend({
      [`GET ${BUCKETS}?${LIST}`]: () => json([B_OLGA], { total: 1 }),
      [`GET ${ACC}?${LIST}`]: () => json([AB_OLGA], { total: 1 }),
      [`GET ${PREPAY}/transferBalance?${LIST}`]: () => json([transfer], { total: 1 }),
      [`GET ${PREPAY}/transferBalance/${X1}`]: () => json(transfer),
      [`GET ${BUCKETS}/${B2}`]: () => json(B_JAN),
    });
    open('/balances');
    await screen.findByText('Olga Kuznetsova');
    tab('Transfers');
    expect(await screen.findByText('Transfer to Jan Petrov')).toBeTruthy();
    expect(calls).toContain(`GET ${BUCKETS}/${B2}`);
    fireEvent.click(screen.getByText('Transfer to Jan Petrov'));
    const dialog = await screen.findByRole('dialog', { name: 'Balance action' });
    expect(await within(dialog).findByText('Transfer cost')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Cancel top-up' })).toBeNull();
    expect(calls).toContain(`GET ${PREPAY}/transferBalance/${X1}`);
  });
});
