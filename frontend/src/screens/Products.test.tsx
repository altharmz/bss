import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import {
  CONTEXT,
  json,
  NAV_RUNS,
  noRuns,
  pending,
  stubFetch,
  tmfError,
  type Handler,
} from '../test/fetch';

// The global Products screen (slice 21). Reads only: the query strings asserted here are
// replayed against the real API by tests/internal/test_ui_requests.py - change both together.

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
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: pending,
      [`GET /internal/v1/billingAccount/${A1}/products`]: pending,
      [`GET /internal/v1/network/command?product.id=${P1}&offset=0&limit=100`]: pending,
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
    // TEST-CHANGE (#458): a row opens the product's page (ProductPage.test.tsx), which
    // replaces the detail dialog with its order items, numbers, device and network commands
    fireEvent.click(await screen.findByText('Basic 10'));
    await waitFor(() => expect(window.location.pathname).toBe(`/products/${P1}`));
    expect(await screen.findByRole('heading', { name: 'Basic 10' })).toBeTruthy();
    expect(calls).toContain(`GET ${PRODUCTS}/${P1}`);
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

  it('says what it is for and finds a subscriber to filter by their account', async () => {
    // #455: the Subscribers list's own search; choosing a row sets ?account= and the chip
    const search = `GET /internal/v1/subscriber?q=olga&offset=0&limit=50`;
    const { calls } = backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second]),
      [search]: () =>
        json([
          {
            id: A1,
            name: 'Olga main',
            state: 'Active',
            ratingType: 'postpaid',
            customer: { id: 'p1', name: 'Olga Kuznetsova' },
            currency: 'USD',
            balance: { unit: 'USD', value: 0 },
            creditLimit: null,
            cycleDay: 1,
            financialAccount: null,
            activeProducts: 1,
            customerAccounts: 1,
          },
        ]),
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`]: () => json([first]),
    });
    open('/products');
    await screen.findByText('Plus 20');
    expect(
      screen.getByText(
        "Products across all billing accounts. A billing account's products are on its card.",
      ),
    ).toBeTruthy();
    const input = expectUi('ui.products.subscriberSearch').querySelector('input') as HTMLElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'olga' } });
    fireEvent.click(await screen.findByRole('option', { name: /Olga Kuznetsova · Olga main/ }));
    await screen.findByText(/Account Olga main/, {
      selector: '[data-ui="ui.products.accountChip"]',
    });
    expect(window.location.search).toBe(`?account=${A1}`);
    await waitFor(() => expect(screen.queryByText('Plus 20')).toBeNull());
    expect(calls).toContain(search);
    expect(calls).toContain(`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`);
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

// #498: Reset filters from an empty filtered page, and a row's account link
describe('products screen recovery', () => {
  it('resets the status, number and account filters from No matches', async () => {
    backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first, second], { total: 2 }),
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&status=suspended&${PAGE}`]: () =>
        json([], { total: 0 }),
      [`GET ${PRODUCTS}?billingAccount.id=${A1}&${PAGE}`]: () => json([first], { total: 1 }),
    });
    open(`/products?account=${A1}`);
    await screen.findByText('Basic 10');
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'suspended' } });
    expect(await screen.findByText('No matches')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(await screen.findByText('Plus 20')).toBeTruthy();
    expect((screen.getByLabelText('Status') as HTMLSelectElement).value).toBe('');
    expect((screen.getByLabelText('Filter by number') as HTMLInputElement).value).toBe('');
    expect(screen.queryByLabelText('Remove the account filter')).toBeNull();
    expect(window.location.search).toBe('');
  });

  it('opens the billing account from a row link without opening the product', async () => {
    backend({
      [`GET ${PRODUCTS}?${PAGE}`]: () => json([first], { total: 1 }),
      'GET /*': pending,
    });
    open('/products');
    const link = (await screen.findAllByRole('link', { name: 'Olga Kuznetsova' }))[0];
    fireEvent.click(link as HTMLElement);
    await waitFor(() => expect(window.location.pathname).toBe(`/billing-accounts/${A1}`));
    expect(window.location.pathname).not.toContain(P1);
  });
});
