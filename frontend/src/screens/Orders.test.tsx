import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const ORDERS = '/tmf-api/productOrderingManagement/v5/productOrder';
const CANCELS = '/tmf-api/productOrderingManagement/v5/cancelProductOrder';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const O1 = 'c0000001-0000-4000-8000-000000000001';
const O2 = 'c0000002-0000-4000-8000-000000000002';
const P1 = 'd0000001-0000-4000-8000-000000000001';

const ACCOUNT = { id: A1, name: 'Ivan Ivanov', '@type': 'BillingAccountRef' };

const ADD = {
  id: O1,
  state: 'completed',
  billingAccount: ACCOUNT,
  creationDate: '2026-10-01T09:00:00Z',
  completionDate: '2026-10-01T09:00:01Z',
  requestedStartDate: '2026-10-05T09:00:00Z',
  productOrderItem: [
    {
      id: '1',
      action: 'add',
      state: 'completed',
      productOffering: { id: 'o1', name: 'Basic 10' },
      product: { id: P1 },
      itemTotalPrice: [{ price: { taxIncludedAmount: { unit: 'USD', value: 12 } } }],
    },
  ],
};
const PENDING = {
  id: O2,
  state: 'inProgress',
  billingAccount: ACCOUNT,
  creationDate: '2026-10-01T10:00:00Z',
  productOrderItem: [{ id: '1', action: 'delete', product: { id: P1 } }],
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
    ...routes,
  });
}

function open(path = '/orders') {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('orders list', () => {
  it('lists every order with its subscriber, state and items, one page request', async () => {
    const { calls } = backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([ADD, PENDING], { total: 2 }),
    });
    open();
    await screen.findByText('C0000001');
    expectUi('ui.orders');
    const row = screen
      .getAllByRole('row')
      .find((r) => within(r).queryByText('C0000001')) as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Ivan Ivanov' }).getAttribute('href')).toBe(
      `/subscribers/${A1}`,
    );
    expect(within(row).getByText('completed')).toBeTruthy();
    expect(within(row).getByText('add Basic 10')).toBeTruthy();
    expect(within(row).getByText('2026-10-05')).toBeTruthy();
    expect(screen.getByText('delete product D0000001')).toBeTruthy();
    expect(screen.getByText('Showing 1–2 of 2')).toBeTruthy();
    expect(calls.filter((c) => c.includes('productOrder?offset'))).toHaveLength(1);
  });

  it('filters by state and by the account in the URL, with a removable chip', async () => {
    const { calls } = backend({
      [`GET ${ORDERS}?billingAccount.id=${A1}&offset=0&limit=50`]: () =>
        json([ADD, PENDING], { total: 2 }),
      [`GET ${ORDERS}?billingAccount.id=${A1}&state=inProgress&offset=0&limit=50`]: () =>
        json([PENDING], { total: 1 }),
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([ADD], { total: 1 }),
      [`GET ${ORDERS}?state=inProgress&offset=0&limit=50`]: () => json([PENDING], { total: 1 }),
    });
    open(`/orders?account=${A1}`);
    const chip = await screen.findByText(/Account/, {
      selector: '[data-ui="ui.orders.accountChip"]',
    });
    await screen.findByText('C0000001');
    expect((await screen.findAllByRole('link', { name: 'Ivan Ivanov' })).length).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'inProgress' }));
    await screen.findByText('Showing 1–1 of 1');
    expect(screen.queryByText('C0000001')).toBeNull();
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));
    await screen.findByText('C0000002'); // still filtered by state, no longer by account
    expect(window.location.search).toBe('');
    expect(calls).toContain(
      `GET ${ORDERS}?billingAccount.id=${A1}&state=inProgress&offset=0&limit=50`,
    );
  });

  it('shows the error with Retry, and the empty state', async () => {
    let fail = true;
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () =>
        fail ? tmfError(503, 'Service Unavailable') : json([], { total: 0 }),
    });
    open();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load orders')).toBeTruthy();
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No orders')).toBeTruthy();
  });

  it('opens the dialog with items, price and cancellations', async () => {
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([ADD], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O1}&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'e1',
              state: 'done',
              creationDate: '2026-10-02T09:00:00Z',
              cancellationReason: 'Customer changed their mind',
            },
          ],
          { total: 1 },
        ),
    });
    open();
    fireEvent.click(await screen.findByText('C0000001'));
    const dialog = await screen.findByRole('dialog');
    expectUi('ui.orders.detail');
    expectUi('ui.orders.items');
    expect(within(dialog).getByText('12.00 USD')).toBeTruthy();
    expect(await within(dialog).findByText('Customer changed their mind')).toBeTruthy();
    expectUi('ui.orders.cancellations');
    expect(within(dialog).queryByText(/Open its command/)).toBeNull(); // not inProgress
  });

  it('links an in-progress order to its prepaid command', async () => {
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([PENDING], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O2}&offset=0&limit=100`]: () => json([], { total: 0 }),
      [`GET /internal/v1/prepaid/command?billingAccount.id=${A1}&offset=0&limit=100`]: () =>
        json([{ id: O2, productOrder: { id: O2 } }], { total: 1 }),
    });
    open();
    fireEvent.click(await screen.findByText('C0000002'));
    const link = await screen.findByRole('link', { name: 'Open its command' });
    expect(link.getAttribute('href')).toBe(`/prepaid-commands?account=${A1}`);
    expectUi('ui.orders.command');
    expect(await screen.findByText('No cancellations.')).toBeTruthy();
  });

  it('shows the rejection reasons of a rejected order', async () => {
    const rejected = {
      ...ADD,
      state: 'rejected',
      productOrderErrorMessage: [{ code: 'x', reason: 'The offering is retired' }],
    };
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([rejected], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O1}&offset=0&limit=100`]: () => json([], { total: 0 }),
    });
    open();
    fireEvent.click(await screen.findByText('C0000001'));
    expect(await screen.findByText('The offering is retired')).toBeTruthy();
  });
});
