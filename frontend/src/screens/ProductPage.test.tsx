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
import { accountBody } from '../test/fixtures';

// The product page (#458). The read query strings asserted here are replayed against the
// real API by tests/internal/test_ui_requests.py; the suspend body is the card's own
// (SubscriberCard.products.test.tsx) - change them together.

const PRODUCTS = '/tmf-api/productInventory/v5/product';
const NUMBERS = '/tmf-api/resourceInventory/v5/resource';
const RATES = '/tmf-api/customerBillManagement/v5/appliedCustomerBillingRate';
const ORDERS = '/tmf-api/productOrderingManagement/v5/productOrder';
const NET = '/internal/v1/network/command';
const P1 = 'e0000001-0000-4000-8000-000000000001';
const P2 = 'e0000002-0000-4000-8000-000000000002';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const C1 = 'c0000001-0000-4000-8000-000000000001';
const N1 = '10000000-0000-4000-8000-000000000001';
const IMSI = '20000000-0000-4000-8000-000000000002';
const OTHER = '30000000-0000-4000-8000-000000000003';
const ORD1 = 'f0000001-0000-4000-8000-000000000001';
const ORD2 = 'f0000002-0000-4000-8000-000000000002';
const B1 = 'b0000001-0000-4000-8000-000000000001';
const HISTORY = `/internal/v1/product/${P1}/history`;
const usd = (value: number) => ({ unit: 'USD', value });

const inventory = {
  id: P1,
  status: 'active',
  name: 'Basic 10',
  productOffering: { id: 'o1', name: 'Basic 10' },
  productSpecification: { id: 's1', name: 'Mobile Voice & Data' },
  billingAccount: { id: A1, name: 'Olga Kuznetsova', '@referredType': 'BillingAccount' },
  startDate: '2026-09-01T12:00:00+03:00',
  realizingResource: [{ id: N1, '@referredType': 'LogicalResource' }],
  productOrderItem: [{ orderId: ORD1, orderItemId: '1', role: 'creation', orderItemAction: 'add' }],
};

function line(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    href: `${PRODUCTS}/${id}`,
    status: 'active',
    startDate: '2026-09-01',
    terminationDate: null,
    productOffering: { id: 'o1', name: 'Basic 10', code: 'B10' },
    productSpecification: { id: 's1', name: 'Mobile Voice & Data' },
    price: usd(12.9),
    nextChargeDate: '2026-11-01',
    pendingChanges: [],
    msisdn: '12025550123',
    imsi: null,
    iccid: null,
    ...over,
  };
}

const ACCOUNT = accountBody(A1, { id: C1, name: 'Olga Kuznetsova' });

function backend(routes: Record<string, Handler> = {}, product: object = inventory) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    [`GET ${PRODUCTS}/${P1}`]: () => json(product),
    [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: () => json(ACCOUNT),
    [`GET /internal/v1/billingAccount/${A1}/products`]: () => json([line(P1)]),
    [`GET ${NUMBERS}/${N1}`]: () =>
      json({ id: N1, '@type': 'LogicalResource', name: 'MSISDN', value: '12025550123' }),
    [`GET ${NET}?product.id=${P1}&offset=0&limit=100`]: () => json([], { total: 0 }),
    ...routes,
  });
}

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('product page', () => {
  it('opens from a Products row: header, crumbs and the overview', async () => {
    backend({ [`GET ${PRODUCTS}?offset=0&limit=50`]: () => json([inventory], { total: 1 }) });
    open('/products');
    fireEvent.click(await screen.findByText('Basic 10'));
    await waitFor(() => expect(window.location.pathname).toBe(`/products/${P1}`));
    expect(await screen.findByRole('heading', { name: 'Basic 10' })).toBeTruthy();
    expectUi('ui.product');
    const header = expectUi('ui.product.header');
    expect(await within(header).findByText('12025550123')).toBeTruthy();
    expect(within(header).getByText('Active')).toBeTruthy();
    expect(within(header).getByText(/Starts 2026-09-01/)).toBeTruthy();
    expect(within(header).getByRole('link', { name: 'Olga Kuznetsova' }).getAttribute('href')).toBe(
      `/billing-accounts/${A1}`,
    );
    // the sidebar keeps Products active; the crumb names the product
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Products' }).getAttribute('aria-current')).toBe(
      'page',
    );
    const overview = expectUi('ui.product.overview');
    expect(await within(overview).findByText('Mobile Voice & Data')).toBeTruthy();
    expect(within(overview).getByText('2026-11-01')).toBeTruthy();
    const msisdn = await within(overview).findByRole('link', { name: '12025550123' });
    expect(msisdn.getAttribute('href')).toBe('/numbers?value=12025550123&kind=msisdn');
    expectUi('ui.products.resource');
    // money stays on the billing account: no balance on the page
    expect(within(expectUi('ui.product')).queryByText(/Balance/)).toBeNull();
  });

  it('shows "—" for a product without a number and no actions for an ended one', async () => {
    const ended = {
      ...inventory,
      status: 'terminated',
      terminationDate: '2026-09-20T12:00:00+03:00',
      realizingResource: [],
    };
    backend({ [`GET /internal/v1/billingAccount/${A1}/products`]: () => json([]) }, ended);
    open(`/products/${P1}`);
    const header = await waitFor(() => expectUi('ui.product.header'));
    expect(within(header).getByText('—')).toBeTruthy();
    expect(within(header).getByText(/Ends 2026-09-20/)).toBeTruthy();
    expect(await screen.findByText('None.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Actions' })).toBeNull();
  });

  it('shows the IMSI and SIM of a product and an ordinary resource as it is (#477)', async () => {
    const both = {
      ...inventory,
      realizingResource: [
        { id: N1, '@referredType': 'LogicalResource' },
        { id: IMSI, '@referredType': 'LogicalResource' },
        { id: OTHER, '@referredType': 'Resource' },
      ],
    };
    backend(
      {
        [`GET ${NUMBERS}/${IMSI}`]: () =>
          json({
            id: IMSI,
            value: '250019876543210',
            resourceSpecification: { id: 'imsi' },
            resourceCharacteristic: [{ name: 'iccid', value: '8970199123456789012' }],
          }),
        [`GET ${NUMBERS}/${OTHER}`]: () => tmfError(404, 'Not Found'),
      },
      both,
    );
    open(`/products/${P1}`);
    const resources = await waitFor(() => expectUi('ui.products.resource'));
    const imsi = await within(resources).findByRole('link', { name: '250019876543210' });
    expect(imsi.getAttribute('href')).toBe('/numbers?value=250019876543210&kind=imsi');
    expect(within(resources).getByText('8970199123456789012')).toBeTruthy();
    expect(within(resources).getByText(/^Resource 30000000/)).toBeTruthy();
  });

  it('shows the network commands of a product, without a Retry for a confirmed one', async () => {
    backend({
      [`GET ${NET}?product.id=${P1}&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'b0000001-0000-4000-8000-000000000001',
              kind: 'activate',
              state: 'confirmed',
              productOrder: { id: ORD1 },
              productOrderItem: { id: ORD1 },
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
    open(`/products/${P1}`);
    expect((await screen.findAllByText('Confirmed')).length).toBeGreaterThan(0);
    const commands = expectUi('ui.network.commands');
    expect(within(commands).getByText('Activate')).toBeTruthy();
    expect(within(commands).queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('shows the current device from the product characteristics', async () => {
    backend(
      {},
      {
        ...inventory,
        productCharacteristic: [
          { name: 'imei', value: '356938035643809', '@type': 'StringCharacteristic' },
          { name: 'deviceCompatibility', value: 'unknown', '@type': 'StringCharacteristic' },
          {
            name: 'deviceReasons',
            value: 'not in the device database',
            '@type': 'StringCharacteristic',
          },
        ],
      },
    );
    open(`/products/${P1}`);
    const device = await waitFor(() => expectUi('ui.products.device'));
    expect(within(device).getByText(/not confirmed by the network/)).toBeTruthy();
    expect(within(device).getByText('356938035643809')).toBeTruthy();
    expect(screen.getByText('not in the device database')).toBeTruthy();
  });

  it('links an add-on pack to its plan and the plan to its packs', async () => {
    backend({
      [`GET /internal/v1/billingAccount/${A1}/products`]: () =>
        json([
          line(P1, { offeringKind: 'plan' }),
          line(P2, {
            offeringKind: 'addOn',
            parentProduct: { id: P1, href: '' },
            productOffering: { id: 'o2', name: 'Roaming 1 GB', code: null },
            terminationDate: '2026-10-08',
          }),
        ]),
    });
    open(`/products/${P1}`);
    const related = await waitFor(() => expectUi('ui.product.related'));
    expect(within(related).getByRole('link', { name: 'Roaming 1 GB' }).getAttribute('href')).toBe(
      `/products/${P2}`,
    );
    expect(within(related).getByText(/ends 2026-10-08/)).toBeTruthy();
  });

  it('lists the charges newest first and opens the bill of a billed one', async () => {
    const rate = (id: string, start: string, over: Record<string, unknown> = {}) => ({
      id,
      name: 'Basic 10 monthly',
      date: `${start}T00:30:00Z`,
      appliedBillingRateType: 'recurring',
      taxExcludedAmount: usd(12.9),
      taxIncludedAmount: usd(14.06),
      periodCoverage: { startDateTime: `${start}T00:00:00+03:00` },
      product: { id: P1, name: 'Basic 10' },
      ...over,
    });
    const { calls } = backend({
      // the server lists them oldest first; the page shows the newest first
      [`GET ${RATES}?product.id=${P1}&offset=0&limit=100`]: () =>
        json(
          [
            rate('r1', '2026-09-01', { bill: { id: B1, name: 'B-0001' } }),
            rate('r2', '2026-10-01'),
          ],
          { total: 2 },
        ),
      [`GET /internal/v1/customerBill/${B1}`]: pending,
    });
    open(`/products/${P1}`);
    fireEvent.click(await screen.findByRole('tab', { name: 'Charges' }));
    expect(window.location.hash).toBe('#charges');
    const charges = await waitFor(() => expectUi('ui.product.charges'));
    await within(charges).findByText('Not billed');
    const dates = within(charges)
      .getAllByRole('row')
      .map((r) => r.textContent ?? '')
      .filter((t) => t.includes('Basic 10 monthly'));
    expect(dates[0]).toContain('2026-10-01');
    expect(dates[1]).toContain('2026-09-01');
    expect(within(charges).getAllByText('14.06')).toHaveLength(2);
    fireEvent.click(within(charges).getByRole('button', { name: 'B-0001' }));
    expectUi('ui.product.charges.bill');
    await waitFor(() => expect(calls).toContain(`GET /internal/v1/customerBill/${B1}`));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('shows the usage summary and records of this line only', async () => {
    backend({
      [`GET /internal/v1/usage/summary?product.id=${P1}`]: () =>
        json({
          product: { id: P1, href: '' },
          date: '2026-10-01',
          period: { startDate: '2026-09-20', endDate: '2026-10-20' },
          allowances: [
            {
              kind: 'data',
              zone: 'domestic',
              unit: 'bytes',
              used: 850 * 1024 ** 2,
              included: 1000 * 1024 ** 2,
              remaining: 150 * 1024 ** 2,
              percent: 85,
              thresholds: [80],
              pooled: false,
              pool: null,
            },
          ],
          throttled: false,
          payAsYouGo: null,
        }),
      [`GET /tmf-api/productUsageManagement/v5/productUsage?product.id=${P1}&offset=0&limit=25`]:
        () => json([], { total: 0 }),
    });
    open(`/products/${P1}#usage`);
    const usage = await waitFor(() => expectUi('ui.product.usage'));
    expect(await within(usage).findByText(/850.0 MB of 1000.0 MB/)).toBeTruthy();
    expect(within(usage).getByText('2026-09-20 – 2026-10-20 (end excluded)')).toBeTruthy();
    expect(await within(usage).findByText('No usage')).toBeTruthy();
  });

  it('lists the order items and the changes, newest first and paged', async () => {
    const change = (over: Record<string, unknown>) => ({
      endDate: null,
      status: null,
      cause: null,
      productOffering: null,
      resource: null,
      productOrder: { id: ORD2, href: '' },
      ...over,
    });
    const { calls } = backend({
      [`GET ${HISTORY}?offset=0&limit=20`]: () =>
        json(
          [
            change({
              kind: 'offeringChange',
              date: '2026-09-25',
              productOffering: { id: 'o2', href: '', name: 'Plus 20' },
            }),
            change({
              kind: 'statusChange',
              date: '2026-09-15',
              status: 'suspended',
              cause: 'lifecycle',
            }),
            change({
              kind: 'numberAssignment',
              date: '2026-09-01',
              endDate: '2026-09-10',
              resource: { id: N1, href: '', kind: 'msisdn', value: '12025550100', iccid: null },
              productOrder: { id: ORD1, href: '' },
            }),
          ],
          { more: true },
        ),
      [`GET ${HISTORY}?offset=20&limit=20`]: () =>
        json(
          [
            change({
              kind: 'numberAssignment',
              date: '2026-09-01',
              resource: {
                id: IMSI,
                href: '',
                kind: 'imsi',
                value: '250019876543210',
                iccid: '8970199123456789012',
              },
            }),
          ],
          { total: 1 },
        ),
    });
    open(`/products/${P1}#history`);
    const history = await waitFor(() => expectUi('ui.product.history'));
    expect(await within(history).findByText(/creation · add/)).toBeTruthy();
    expectUi('ui.products.orderItems');
    expect(within(history).getByRole('link', { name: 'Open in Orders' }).getAttribute('href')).toBe(
      `/orders?account=${A1}&state=all`,
    );
    expect(await within(history).findByText('Offering changed to Plus 20')).toBeTruthy();
    expect(within(history).getByText('Suspended by the prepaid lifecycle')).toBeTruthy();
    expect(within(history).getByText('Number 12025550100 held until 2026-09-10')).toBeTruthy();
    fireEvent.click(within(history).getByRole('button', { name: 'Next' }));
    expect(
      await within(history).findByText(
        'SIM: IMSI 250019876543210, ICCID 8970199123456789012 assigned',
      ),
    ).toBeTruthy();
    expect(calls).toContain(`GET ${HISTORY}?offset=20&limit=20`);
  });

  it('offers the card row actions and suspends with the card request', async () => {
    const { fetchMock } = backend({
      [`POST ${ORDERS}`]: () =>
        json({ id: 'o1', state: 'completed', '@type': 'ProductOrder' }, { status: 201 }),
    });
    open(`/products/${P1}`);
    const trigger = await screen.findByRole('button', { name: 'Actions' });
    expect(trigger).toBe(expectUi('ui.subscriber.productActions'));
    fireEvent.click(trigger);
    const menu = document.getElementById(
      trigger.getAttribute('aria-controls') ?? '',
    ) as HTMLElement;
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Deactivate',
      'Suspend…',
      'Change offering…',
      'End on a date…',
    ]);
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Suspend…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-03' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Basic 10: suspend recorded for 2026-11-03',
    );
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A1, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-11-03T12:00:00+03:00',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: { id: P1, '@type': 'ProductRef', status: 'suspended' },
        },
      ],
    });
  });

  it('offers retry when the product cannot be read', async () => {
    let fail = true;
    backend({
      [`GET ${PRODUCTS}/${P1}`]: () =>
        fail ? tmfError(503, 'Service Unavailable') : json(inventory),
    });
    open(`/products/${P1}`);
    expect(await screen.findByText('Failed to load the product')).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByRole('heading', { name: 'Basic 10' })).toBeTruthy();
  });
});
