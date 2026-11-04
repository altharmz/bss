import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { billBody } from '../test/fixtures';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// Global bills, bill cycles and rates (docs/ui.md). Reads only:
// the query strings asserted here are replayed against the real API by
// tests/test_ui_requests.py - change both together.

const BASE = '/tmf-api/customerBillManagement/v5';
const BILL = `${BASE}/customerBill`;
const CYCLE = `${BASE}/billCycle`;
const RATE = `${BASE}/appliedCustomerBillingRate`;
const A1 = 'a0000001-0000-4000-8000-000000000001';
const A2 = 'a0000002-0000-4000-8000-000000000002';
const B1 = 'b1000001-0000-4000-8000-000000000001';
const B2 = 'b2000002-0000-4000-8000-000000000002';
const K1 = 'c1000001-0000-4000-8000-000000000001';
const R1 = 'd1000001-0000-4000-8000-000000000001';
const usd = (value: number) => ({ unit: 'USD', value });
const PAGE = 'offset=0&limit=100';

const BILL1 = billBody(B1, '2026-09-01', 300, 100, 'partiallyPaid', {
  billingAccount: { id: A1, name: 'Ivan Ivanov' },
  billCycle: { id: K1, name: 'Daily billing 2026-09-01' },
});
const BILL2 = billBody(B2, '2026-10-01', 50, 50, 'sent', {
  billingAccount: { id: A2, name: 'Olga Kuznetsova' },
  runType: 'offCycle',
});
const CYCLE1 = {
  id: K1,
  name: 'Daily billing 2026-09-01',
  billingDate: '2026-08-31T21:00:00Z',
  paymentDueDate: '2026-09-30T21:00:00Z',
  '@type': 'BillCycle',
};
const RATE1 = {
  id: R1,
  name: 'Mobile L',
  taxExcludedAmount: usd(250),
  taxIncludedAmount: usd(300),
  appliedTax: [{ taxCategory: 'VAT', taxRate: 20, taxAmount: usd(50) }],
  periodCoverage: { startDateTime: '2026-08-31T21:00:00Z', endDateTime: '2026-09-30T21:00:00Z' },
  product: { id: 'p1', name: 'Mobile L' },
  '@type': 'AppliedCustomerBillingRate',
};
const DETAIL = {
  id: B1,
  href: `${BILL}/${B1}`,
  billNo: 'BL-B1000001',
  billingAccount: { id: A1, href: '/x' },
  billDate: '2026-09-01',
  period: { start: '2026-09-01', end: '2026-10-01' },
  paymentDueDate: '2026-10-01',
  amountDue: usd(300),
  remainingAmount: usd(100),
  state: 'partiallyPaid',
  category: 'normal',
  overdue: false,
  allowedStates: [],
  charges: [],
  sources: [],
  history: [],
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
    [`GET ${CYCLE}?${PAGE}`]: () => json([CYCLE1], { total: 1 }),
    ...routes,
  });
}

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('bills screen', () => {
  it('lists bills newest first with account links, kind and amount', async () => {
    backend({ [`GET ${BILL}?${PAGE}`]: () => json([BILL1, BILL2], { total: 2 }) });
    open('/bills');
    await screen.findByText('Ivan Ivanov');
    expectUi('ui.bills');
    expect(screen.getByRole('columnheader', { name: 'Total' })).toBeTruthy();
    expect(screen.queryByRole('columnheader', { name: 'Amount due' })).toBeNull();
    const rows = screen.getAllByRole('row');
    expect(rows.findIndex((r) => within(r).queryByText('Olga Kuznetsova'))).toBeLessThan(
      rows.findIndex((r) => within(r).queryByText('Ivan Ivanov')),
    );
    const row = rows.find((r) => within(r).queryByText('Olga Kuznetsova')) as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Olga Kuznetsova' }).getAttribute('href')).toBe(
      `/subscribers/${A2}`,
    );
    expect(within(row).getByText('BL-B2000002')).toBeTruthy();
    expect(within(row).getByText('2026-10-01')).toBeTruthy();
    expect(within(row).getByText('Off-cycle')).toBeTruthy();
    expect(within(row).getByText('Unpaid')).toBeTruthy();
    expect(screen.getByText('Showing 2')).toBeTruthy();
  });

  it('filters by state and account on the server and by billing date client side', async () => {
    const { calls } = backend({
      [`GET ${BILL}?${PAGE}`]: () => json([BILL1, BILL2], { total: 2 }),
      [`GET ${BILL}?billingAccount.id=${A1}&${PAGE}`]: () => json([BILL1], { total: 1 }),
      [`GET ${BILL}?billingAccount.id=${A1}&state=partiallyPaid&${PAGE}`]: () =>
        json([BILL1], { total: 1 }),
      [`GET ${BILL}?state=partiallyPaid&${PAGE}`]: () => json([BILL1], { total: 1 }),
    });
    open(`/bills?account=${A1}`);
    const chip = await screen.findByText(/Account/, {
      selector: '[data-ui="ui.bills.accountChip"]',
    });
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    fireEvent.click(screen.getByRole('button', { name: 'partiallyPaid' }));
    await waitFor(() =>
      expect(calls).toContain(`GET ${BILL}?billingAccount.id=${A1}&state=partiallyPaid&${PAGE}`),
    );
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    const before = calls.length;
    fireEvent.change(screen.getByLabelText('Billing date'), { target: { value: '2026-10-01' } });
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Ivan Ivanov' })).toBeNull());
    expect(calls.length).toBe(before); // applied to the loaded rows
    fireEvent.change(screen.getByLabelText('Billing date'), { target: { value: '2026-09-01' } });
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));
    await waitFor(() => expect(calls).toContain(`GET ${BILL}?state=partiallyPaid&${PAGE}`));
    expect(window.location.search).toBe('');
  });

  it('opens the bill dialog with the cycle link and the rates table, and a rate', async () => {
    const { calls } = backend({
      [`GET ${BILL}?${PAGE}`]: () => json([BILL1], { total: 1 }),
      [`GET /internal/v1/customerBill/${B1}`]: () => json(DETAIL),
      [`GET ${RATE}?bill.id=${B1}&${PAGE}`]: () => json([RATE1], { total: 1 }),
      [`GET ${RATE}/${R1}`]: () => json(RATE1),
      [`GET ${CYCLE}/${K1}`]: () => json(CYCLE1),
    });
    open('/bills');
    fireEvent.click(await screen.findByText('BL-B1000001'));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B1000001' });
    await within(dialog).findByText('Related');
    expectUi('ui.bills.links');
    expect(calls.some((c) => c.includes('appliedCustomerBillingRate'))).toBe(false); // lazy

    fireEvent.click(within(dialog).getByRole('button', { name: 'Rates' }));
    const row = (await within(dialog).findAllByText('Mobile L'))[0]?.closest(
      '[role=row]',
    ) as HTMLElement;
    expect(within(row).getByText('2026-09-01 – 2026-10-01')).toBeTruthy();
    expect(within(row).getByText('250.00')).toBeTruthy();
    expect(within(row).getByText('50.00')).toBeTruthy();
    fireEvent.click(row);
    await screen.findByRole('dialog', { name: 'Rate D1000001' });
    expectUi('ui.bills.rateDetail');
    expect(calls).toContain(`GET ${RATE}/${R1}`);
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' }).at(-1) as HTMLElement);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Bill cycle' }));
    await screen.findByRole('dialog', { name: 'Bill cycle C1000001' });
    expectUi('ui.bills.cycleDetail');
    expect(calls).toContain(`GET ${CYCLE}/${K1}`);
  });

  it('has no cycle link for a bill without a bill cycle', async () => {
    backend({
      [`GET ${BILL}?${PAGE}`]: () => json([BILL2], { total: 1 }),
      [`GET /internal/v1/customerBill/${B2}`]: () =>
        json({ ...DETAIL, id: B2, billNo: 'BL-B2000002' }),
    });
    open('/bills');
    fireEvent.click(await screen.findByText('BL-B2000002'));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B2000002' });
    await within(dialog).findByText('Related');
    expect(within(dialog).queryByRole('button', { name: 'Bill cycle' })).toBeNull();
  });

  it('lists the bill cycles with a billing date filter and opens one', async () => {
    const { calls } = backend({
      [`GET ${BILL}?${PAGE}`]: () => json([], { total: 0 }),
      [`GET ${CYCLE}/${K1}`]: () => json(CYCLE1),
    });
    open('/bills');
    fireEvent.click(await screen.findByRole('button', { name: 'Bill cycles' }));
    const row = (await screen.findByText('Daily billing 2026-09-01')).closest(
      '[role=row]',
    ) as HTMLElement;
    expect(within(row).getByText('2026-09-01')).toBeTruthy();
    expect(within(row).getByText('2026-10-01')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Billing date'), { target: { value: '2026-09-02' } });
    await waitFor(() => expect(screen.queryByText('Daily billing 2026-09-01')).toBeNull());
    fireEvent.change(screen.getByLabelText('Billing date'), { target: { value: '2026-09-01' } });
    fireEvent.click(await screen.findByText('Daily billing 2026-09-01'));
    await screen.findByRole('dialog', { name: 'Bill cycle C1000001' });
    expect(calls).toContain(`GET ${CYCLE}/${K1}`);
  });

  it('shows the error with Retry', async () => {
    let fail = true;
    backend({
      [`GET ${BILL}?${PAGE}`]: () =>
        fail ? tmfError(503, 'Service Unavailable') : json([], { total: 0 }),
    });
    open('/bills');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load bills')).toBeTruthy();
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await screen.findByText('No bills');
  });
});
