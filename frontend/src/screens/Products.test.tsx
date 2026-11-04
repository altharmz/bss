import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// The global Products screen (slice 21). Reads only: the query strings asserted here are
// replayed against the real API by tests/test_ui_requests.py - change both together.

const PRODUCTS = '/tmf-api/productInventory/v5/product';
const NUMBERS = '/tmf-api/resourceInventory/v5/resource';
const P1 = 'e0000001-0000-4000-8000-000000000001';
const P2 = 'e0000002-0000-4000-8000-000000000002';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const N1 = '10000000-0000-4000-8000-000000000001';
const PAGE = 'offset=0&limit=100';

const first = {
  id: P1,
  status: 'active',
  name: 'Basic 10',
  productOffering: { id: 'o1', name: 'Basic 10' },
  billingAccount: { id: A1, name: 'Olga Kuznetsova', '@referredType': 'BillingAccount' },
  startDate: '2026-09-01T12:00:00+03:00',
  realizingResource: [{ id: N1, '@referredType': 'LogicalResource' }],
  productOrderItem: [
    {
      orderId: 'f0000001-0000-4000-8000-000000000001',
      orderItemId: '1',
      role: 'creation',
      orderItemAction: 'add',
    },
  ],
};
const second = {
  id: P2,
  status: 'terminated',
  productOffering: { id: 'o2', name: 'Plus 20' },
  billingAccount: { id: A1, name: 'Olga Kuznetsova', '@referredType': 'BillingAccount' },
  startDate: '2026-08-01T12:00:00+03:00',
  terminationDate: '2026-09-01T12:00:00+03:00',
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
    [`GET ${NUMBERS}?${PAGE}`]: () =>
      json(
        [{ id: N1, '@type': 'LogicalResource', name: 'MSISDN 12025550123', value: '12025550123' }],
        {
          total: 1,
        },
      ),
    ...routes,
  });
}

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('products screen', () => {
  it('lists every status, filters on the server and client, and opens the detail', async () => {
    const { calls } = backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}?status=terminated&${PAGE}`]: () => json([second], { total: 1 }),
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`]: () =>
        json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}/${P1}`]: () => json(first),
    });
    open('/products');
    expect(await screen.findByText('Plus 20')).toBeTruthy();
    expectUi('ui.products');
    expect(await screen.findByText('12025550123')).toBeTruthy();
    // client side: offering and number
    fireEvent.change(screen.getByLabelText('Filter by offering'), { target: { value: 'plus' } });
    expect(screen.queryByText('Basic 10')).toBeNull();
    fireEvent.change(screen.getByLabelText('Filter by offering'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Filter by number'), { target: { value: '0123' } });
    expect(screen.queryByText('Plus 20')).toBeNull();
    fireEvent.change(screen.getByLabelText('Filter by number'), { target: { value: '' } });
    // server side: status
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'terminated' } });
    await waitFor(() => expect(calls).toContain(`GET ${PRODUCTS}?status=terminated&${PAGE}`));
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    fireEvent.click(await screen.findByText('Basic 10'));
    const detail = await screen.findByRole('dialog');
    expectUi('ui.products.detail');
    expect(await within(detail).findByText(/creation · add/)).toBeTruthy();
    expectUi('ui.products.orderItems');
    expectUi('ui.products.resource');
    expect(calls).toContain(`GET ${PRODUCTS}/${P1}`);
    expect(within(detail).getByRole('link', { name: 'Open in Orders' }).getAttribute('href')).toBe(
      `/orders?account=${A1}`,
    );
  });

  it('maps product states to their pill families and shows PRD- ids', async () => {
    backend({ [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }) });
    open('/products');
    await screen.findByText('Plus 20');
    const rows = screen.getAllByRole('row');
    const row = (name: string) => rows.find((r) => within(r).queryByText(name)) as HTMLElement;
    expect(within(row('Basic 10')).getByText('Active').classList.contains('pill-live')).toBe(true);
    expect(within(row('Plus 20')).getByText('Terminated').classList.contains('pill-ended')).toBe(
      true,
    );
    expect(within(row('Basic 10')).getByText('PRD-')).toBeTruthy();
    expect(within(row('Basic 10')).getByText('E0000001')).toBeTruthy();
  });

  it('filters on the account chip from the URL and removes it', async () => {
    const { calls } = backend({
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`]: () => json([first], { total: 1 }),
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }),
    });
    open(`/products?account=${A1}`);
    expect(await screen.findByText('Basic 10')).toBeTruthy();
    expectUi('ui.products.accountChip');
    expect(screen.queryByText('Plus 20')).toBeNull();
    fireEvent.click(screen.getByLabelText('Remove the account filter'));
    expect(await screen.findByText('Plus 20')).toBeTruthy();
    expect(calls).toContain(`GET ${PRODUCTS}?${PAGE}`);
  });

  it('offers retry on a failed read', async () => {
    let failed = true;
    backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () =>
        failed ? tmfError(503, 'Unavailable') : json([], { total: 0 }),
    });
    open('/products');
    expect(await screen.findByText('Failed to load products')).toBeTruthy();
    failed = false;
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByText('No products')).toBeTruthy();
  });
});
