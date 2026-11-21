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
const PAGE = 'offset=0&limit=50';

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
    [`GET ${NUMBERS}/${N1}`]: () =>
      json({
        id: N1,
        '@type': 'LogicalResource',
        name: 'MSISDN 12025550123',
        value: '12025550123',
      }),
    ...routes,
  });
}

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('products screen', () => {
  it('lists every status, filters on the server, and opens the detail', async () => {
    const { calls } = backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}?status=terminated&${PAGE}`]: () => json([second], { total: 1 }),
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`]: () =>
        json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}/${P1}`]: () => json(first),
      [`GET ${NUMBERS}?value=12025550123&limit=1`]: () => json([{ id: N1 }]),
      [`GET ${PRODUCTS}?realizingResource.id=${N1}&${PAGE}`]: () => json([first]),
      [`GET ${NUMBERS}?value=1999&limit=1`]: () => json([]),
    });
    open('/products');
    expect(await screen.findByText('Plus 20')).toBeTruthy();
    expectUi('ui.products');
    expect(await screen.findByText('12025550123')).toBeTruthy();
    // TEST-CHANGE (#314): the number is an exact server filter; the offering text filter
    // is gone (the API has no offering-name filter, and a page cannot be filtered locally)
    expect(screen.queryByLabelText('Filter by offering')).toBeNull();
    fireEvent.change(screen.getByLabelText('Filter by number'), {
      target: { value: '+1 202 555 0123' },
    });
    await waitFor(() => {
      expect(calls).toContain(`GET ${NUMBERS}?value=12025550123&limit=1`);
      expect(calls).toContain(`GET ${PRODUCTS}?realizingResource.id=${N1}&${PAGE}`);
      expect(screen.queryByText('Plus 20')).toBeNull();
    });
    fireEvent.change(screen.getByLabelText('Filter by number'), { target: { value: '1999' } });
    expect(await screen.findByText('No matches')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Filter by number'), { target: { value: '' } });
    // server side: status
    expect(screen.getByLabelText('Status')).toBe(expectUi('ui.products.status'));
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

  it('shows the network commands of a product, without a Retry for a confirmed one', async () => {
    const NET = '/internal/v1/network/command';
    backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}/${P1}`]: () => json(first),
      [`GET ${NET}?product.id=${P1}&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'b0000001-0000-4000-8000-000000000001',
              kind: 'activate',
              state: 'confirmed',
              productOrder: { id: 'c0000001-0000-4000-8000-000000000001' },
              productOrderItem: { id: 'f0000001-0000-4000-8000-000000000001' },
              product: { id: P1 },
              dueDate: '2026-08-01',
              profile: {},
              request: null,
              attempts: 1,
              attemptedAt: '2026-08-01T09:00:00Z',
              nextAttemptAt: null,
              lastError: null,
              refusalCode: null,
              confirmedAt: '2026-08-01T09:00:01Z',
              createdAt: '2026-08-01T08:00:00Z',
            },
          ],
          { total: 1 },
        ),
    });
    open('/products');
    fireEvent.click(await screen.findByText('Basic 10'));
    const detail = await screen.findByRole('dialog');
    expect((await within(detail).findAllByText('Confirmed')).length).toBeGreaterThan(0);
    expectUi('ui.network.commands');
    expect(within(detail).getByText('Activate')).toBeTruthy();
    expect(within(detail).queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('shows the current device from the product characteristics', async () => {
    const NET = '/internal/v1/network/command';
    const withDevice = {
      ...first,
      productCharacteristic: [
        { name: 'imei', value: '356938035643809', '@type': 'StringCharacteristic' },
        { name: 'deviceCompatibility', value: 'unknown', '@type': 'StringCharacteristic' },
        {
          name: 'deviceReasons',
          value: 'not in the device database',
          '@type': 'StringCharacteristic',
        },
      ],
    };
    backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([withDevice, second], { total: 2 }),
      [`GET ${PRODUCTS}/${P1}`]: () => json(withDevice),
      [`GET ${NET}?product.id=${P1}&offset=0&limit=100`]: () => json([], { total: 0 }),
    });
    open('/products');
    fireEvent.click(await screen.findByText('Basic 10'));
    const detail = await screen.findByRole('dialog');
    const device = await waitFor(() => expectUi('ui.products.device'));
    expect(within(device).getByText(/not confirmed by the network/)).toBeTruthy();
    expect(within(device).getByText('not in the device database')).toBeTruthy();
    expect(within(detail).getByText('356938035643809')).toBeTruthy();
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

  it('pages with Previous and Next, reading only the numbers of the page', async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      ...second,
      id: `e${String(i).padStart(7, '0')}-0000-4000-8000-000000000000`,
      productOffering: { id: 'o2', name: `Offer ${i}` },
    }));
    const { calls } = backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json(many.slice(0, 50), { more: true }),
      [`GET ${PRODUCTS}?offset=50&limit=50`]: () => json([first, ...many.slice(50)]),
    });
    open('/products');
    expect(await screen.findByText('Offer 0')).toBeTruthy();
    expect(screen.getByText('Showing 1–50')).toBeTruthy();
    expectUi('ui.products.previous');
    expectUi('ui.products.next');
    expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(calls.filter((c) => c.startsWith(`GET ${NUMBERS}`))).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 51–52')).toBeTruthy();
    expect(await screen.findByText('12025550123')).toBeTruthy();
    expect(calls.filter((c) => c.startsWith(`GET ${NUMBERS}`))).toEqual([`GET ${NUMBERS}/${N1}`]);
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
