import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../app/App';
import { itemText } from './Orders';
import { expectUi } from '../test/expectUi';
import { accountBody, NO_SETTLEMENT } from '../test/fixtures';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const ORDERS = '/tmf-api/productOrderingManagement/v5/productOrder';
const CANCELS = '/tmf-api/productOrderingManagement/v5/cancelProductOrder';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const O1 = 'c0000001-0000-4000-8000-000000000001';
const O2 = 'c0000002-0000-4000-8000-000000000002';
const P1 = 'd0000001-0000-4000-8000-000000000001';

const ACCOUNT = { id: A1, name: 'Ivan Ivanov', '@type': 'BillingAccountRef' };

/** A row of GET /internal/v1/subscriber (the subscriber search). */
const SUBSCRIBER_ROW = {
  state: 'Active',
  ratingType: 'postpaid',
  currency: 'USD',
  balance: { unit: 'USD', value: 0 },
  creditLimit: null,
  cycleDay: 1,
  financialAccount: null,
  activeProducts: 1,
  customerAccounts: 1,
};

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
  // Small fixture lists use the same response for the count probe and tail read.
  const pages: Record<string, Handler> = {};
  for (const [path, handler] of Object.entries(routes)) {
    if (path.startsWith(`GET ${ORDERS}?`) && path.endsWith('offset=0&limit=50')) {
      pages[path.replace('limit=50', 'limit=1&fields=id')] = handler;
      for (let limit = 1; limit <= 50; limit++) {
        pages[path.replace('limit=50', `limit=${limit}`)] = handler;
      }
    }
  }
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [`GET /tmf-api/productInventory/v5/product/${P1}`]: () =>
      json({ id: P1, name: 'Home internet' }),
    ...pages,
    ...routes,
  });
}

// ?state=all: the tests that are not about the default Unfinished view (#455) read everything
function open(path = '/orders?state=all') {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('orders list', () => {
  it('names all six item actions using item snapshots', () => {
    const product = { '@type': 'Product' as const, id: P1, name: 'Home internet' };
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'modify',
        product: { ...product, status: 'suspended' },
      }),
    ).toBe('Suspend Home internet');
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'modify',
        product: { ...product, status: 'active' },
      }),
    ).toBe('Resume Home internet');
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'modify',
        product,
        productOffering: { '@type': 'ProductOfferingRef', id: 'offering', name: 'Fiber' },
      }),
    ).toBe('Change Home internet to Fiber');
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'modify',
        product: { ...product, realizingResource: [{ '@type': 'ResourceRef', id: 'number' }] },
      }),
    ).toBe('Change number of Home internet');
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'add',
        productOffering: { '@type': 'ProductOfferingRef', id: 'offering', name: 'Fiber' },
      }),
    ).toBe('Add Fiber');
    expect(itemText({ '@type': 'ProductOrderItem', action: 'delete', product })).toBe(
      'End Home internet',
    );
  });

  it('pages forward, newest first as served, without replacing rows with skeletons', async () => {
    // the API lists orders newest first and sends Link rel="next" while more follow (#312)
    let finish: (() => void) | undefined;
    const { calls } = backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () =>
        json(
          [
            PENDING,
            ADD,
            ...Array.from({ length: 48 }, (_, i) => ({
              ...ADD,
              id: `${(i + 100).toString(16).padStart(8, '0')}-0000-4000-8000-000000000001`,
            })),
          ],
          { more: true },
        ),
      [`GET ${ORDERS}?offset=50&limit=50`]: () =>
        new Promise((resolve) => {
          finish = () =>
            resolve(
              json(
                [
                  { ...ADD, id: O1.replace('c0000001', 'c0000003') },
                  { ...ADD, id: O1.replace('c0000001', 'c0000004') },
                ],
                { more: false },
              ),
            );
        }),
    });
    open();
    await screen.findByText('Showing 1–50');
    const rows = screen.getAllByRole('row');
    // shown in the order served: the newest first
    expect(
      screen.getByText('C0000002').compareDocumentPosition(screen.getByText('C0000001')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(calls.filter((call) => call.includes('productOrder?offset'))).toEqual([
      `GET ${ORDERS}?offset=0&limit=50`,
    ]);
    expect(screen.getByRole('button', { name: 'Previous' })).toBe(expectUi('ui.orders.previous'));
    expect(screen.getByRole('button', { name: 'Next' })).toBe(expectUi('ui.orders.next'));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    expect(screen.queryByLabelText('Loading')).toBeNull();
    expect(screen.getByText('C0000002')).toBeTruthy();
    expect(screen.getAllByRole('row').length).toBe(rows.length);
    if (!finish) throw new Error('Older page was not requested');
    finish();
    await screen.findByText('Showing 51–52');
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByText('Showing 1–50');
  });

  it('looks up each unnamed product only once in the dialog and shows its target status', async () => {
    const { calls } = backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () =>
        json(
          [
            {
              ...ADD,
              productOrderItem: [
                { id: '1', action: 'modify', product: { id: P1, status: 'suspended' } },
                { id: '2', action: 'delete', product: { id: P1 } },
                {
                  id: '4',
                  action: 'modify',
                  product: { id: P1 },
                  productOffering: { id: 'fiber', name: 'Fiber' },
                },
                {
                  id: '5',
                  action: 'modify',
                  product: { id: P1, realizingResource: [{ id: 'number' }] },
                },
                { id: '6', action: 'add', productOffering: { id: 'fiber', name: 'Fiber' } },
                {
                  id: '3',
                  action: 'modify',
                  product: {
                    id: P1.replace('d0000001', 'd0000002'),
                    name: 'Snapshot',
                    status: 'active',
                  },
                },
              ],
            },
          ],
          { total: 1 },
        ),
      [`GET ${CANCELS}?productOrder.id=${O1}&offset=0&limit=100`]: () => json([], { total: 0 }),
    });
    open();
    await screen.findByText('C0000001');
    expect(calls.filter((call) => call.includes('/productInventory/'))).toEqual([]);
    const dialog = await openOrder('C0000001');
    expect(await within(dialog).findByText('Suspend Home internet')).toBeTruthy();
    expect(within(dialog).getByText('End Home internet')).toBeTruthy();
    expect(within(dialog).getByText('Resume Snapshot')).toBeTruthy();
    expect(within(dialog).getByText('Change Home internet to Fiber')).toBeTruthy();
    expect(within(dialog).getByText('Change number of Home internet')).toBeTruthy();
    expect(within(dialog).getByText('Add Fiber')).toBeTruthy();
    expect(within(dialog).getByText('Target status')).toBeTruthy();
    expect(within(dialog).getByText('Suspended')).toBeTruthy();
    const none = await waitFor(() => expectUi('ui.orders.noCancellations'));
    expect(dialog.contains(none)).toBe(true);
    expect(calls.filter((call) => call.includes('/productInventory/'))).toEqual([
      `GET /tmf-api/productInventory/v5/product/${P1}`,
    ]);
  });

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
      `/billing-accounts/${A1}`,
    );
    expect(within(row).getByText('Completed').classList.contains('pill-live')).toBe(true);
    expect(within(row).getByText('ORD-')).toBeTruthy();
    expect(within(row).getByText('Add Basic 10')).toBeTruthy();
    expect(within(row).getByText('2026-10-05')).toBeTruthy();
    expect(screen.getByText('End product D0000001')).toBeTruthy();
    expect(screen.getByText('Showing 1–2')).toBeTruthy();
    expect(calls.filter((c) => c.includes('productOrder?offset'))).toEqual([
      `GET ${ORDERS}?offset=0&limit=50`,
    ]);
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
    open(`/orders?account=${A1}&state=all`);
    const chip = await screen.findByText(/Account/, {
      selector: '[data-ui="ui.orders.accountChip"]',
    });
    expect(chip).toBe(expectUi('ui.orders.accountChip'));
    await screen.findByText('C0000001');
    expect((await screen.findAllByRole('link', { name: 'Ivan Ivanov' })).length).toBe(2);
    expect(screen.getByRole('button', { name: 'In progress' }).getAttribute('title')).toBe(
      'inProgress',
    );
    fireEvent.click(screen.getByRole('button', { name: 'In progress' }));
    await screen.findByText('Showing 1–1');
    expect(screen.queryByText('C0000001')).toBeNull();
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));
    await screen.findByText('C0000002'); // still filtered by state, no longer by account
    expect(window.location.search).toBe('?state=inProgress');
    expect(calls).toContain(
      `GET ${ORDERS}?billingAccount.id=${A1}&state=inProgress&offset=0&limit=50`,
    );
  });

  it('opens on the unfinished orders, says what it is for, and keeps the choice in the URL', async () => {
    // #455: no ?state means Unfinished, one comma-separated state filter (OR on the server)
    const unfinished = `GET ${ORDERS}?state=draft,acknowledged,inProgress,failed&offset=0&limit=50`;
    const { calls } = backend({
      [unfinished]: () => json([PENDING]),
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([PENDING, ADD]),
      [`GET ${ORDERS}?state=completed&offset=0&limit=50`]: () => json([]),
    });
    open('/orders');
    await screen.findByText('C0000002');
    expect(screen.queryByText('C0000001')).toBeNull();
    expect(calls.filter((c) => c.startsWith(`GET ${ORDERS}?`))).toEqual([unfinished]);
    const preset = expectUi('ui.orders.unfinished');
    expect(preset.textContent).toBe('Unfinished');
    expect(preset.getAttribute('aria-pressed')).toBe('true');
    expect(preset.getAttribute('title')).toBe('draft, acknowledged, inProgress, failed');
    expect(
      screen.getByText(
        "Orders across all billing accounts. A billing account's own orders: the Orders link on its card.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    await screen.findByText('C0000001');
    expect(window.location.search).toBe('?state=all');
    // an empty filtered page is "no matches" with a reset, not "no orders"
    fireEvent.click(screen.getByRole('button', { name: 'Completed' }));
    expect(await screen.findByText('No matches')).toBeTruthy();
    expect(window.location.search).toBe('?state=completed');
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    await screen.findByText('C0000001');
    expect(window.location.search).toBe('?state=all');
  });

  it('keeps an unfinished-only page in the URL after a reload', async () => {
    backend({
      [`GET ${ORDERS}?state=failed&offset=0&limit=50`]: () =>
        json([{ ...PENDING, state: 'failed' }]),
    });
    open('/orders?state=failed');
    await screen.findByText('C0000002');
    expect(screen.getByRole('button', { name: 'Failed' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });

  it('finds a subscriber by name, BA id or number and filters by their account', async () => {
    const search = `GET /internal/v1/subscriber?q=777&offset=0&limit=50`;
    const filtered = `GET ${ORDERS}?billingAccount.id=${A1}&state=draft,acknowledged,inProgress,failed&offset=0&limit=50`;
    const { calls } = backend({
      [`GET ${ORDERS}?state=draft,acknowledged,inProgress,failed&offset=0&limit=50`]: () =>
        json([PENDING]),
      [search]: () =>
        json([
          {
            ...SUBSCRIBER_ROW,
            id: A1,
            name: 'Ivan main',
            customer: { id: 'p1', name: 'Ivan Ivanov' },
          },
        ]),
      [filtered]: () => json([]),
    });
    open('/orders');
    await screen.findByText('C0000002');
    const input = expectUi('ui.orders.subscriberSearch').querySelector('input') as HTMLElement;
    expect(input.getAttribute('placeholder')).toBe('Subscriber: name, BA id or number');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '777' } });
    // the server matched the number: the option shows although its label does not contain it
    fireEvent.click(await screen.findByRole('option', { name: /Ivan Ivanov · Ivan main/ }));
    const chip = await screen.findByText(/Account Ivan main/, {
      selector: '[data-ui="ui.orders.accountChip"]',
    });
    expect(chip).toBeTruthy();
    expect(window.location.search).toBe(`?account=${A1}`);
    await screen.findByText('No matches');
    expect(calls).toContain(search);
    expect(calls).toContain(filtered);
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
    fireEvent.click((await screen.findByText('C0000001')).closest('[role="row"]') as HTMLElement);
    const dialog = await screen.findByRole('dialog');
    expectUi('ui.orders.detail');
    expectUi('ui.orders.items');
    expect(within(dialog).getByText('12.00')).toBeTruthy();
    expect(await within(dialog).findByText('Customer changed their mind')).toBeTruthy();
    expectUi('ui.orders.cancellations');
    expect(within(dialog).queryByText(/Open its command/)).toBeNull(); // not inProgress
  });

  const NET = '/internal/v1/network/command';
  const netCommand = (id: string, state: string, extra: Record<string, unknown> = {}) => ({
    id,
    kind: 'activate',
    state,
    productOrder: { id: O2, href: `${ORDERS}/${O2}` },
    productOrderItem: { id: 'f0000001-0000-4000-8000-000000000001' },
    product: { id: P1 },
    dueDate: '2026-10-05',
    profile: { dataAllowanceMb: 5000 },
    request: null,
    attempts: 0,
    attemptedAt: null,
    nextAttemptAt: null,
    lastError: null,
    refusalCode: null,
    confirmedAt: null,
    createdAt: '2026-10-01T10:00:00Z',
    ...extra,
  });
  const NETWORK_A = 'b0000001-0000-4000-8000-000000000001';
  const NETWORK_B = 'b0000002-0000-4000-8000-000000000002';

  it('shows the network step of an order and retries an exhausted command with no body', async () => {
    let state = 'exhausted';
    const { fetchMock } = backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([PENDING], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O2}&offset=0&limit=100`]: () => json([], { total: 0 }),
      [`GET /internal/v1/prepaid/command?billingAccount.id=${A1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${NET}?productOrder.id=${O2}&offset=0&limit=100`]: () =>
        json(
          [
            state === 'exhausted'
              ? netCommand(NETWORK_A, 'exhausted', {
                  attempts: 5,
                  attemptedAt: '2026-10-01T12:00:00Z',
                  lastError: 'connection reset',
                })
              : netCommand(NETWORK_A, 'pending', { attempts: 5 }),
            netCommand(NETWORK_B, 'refused', {
              kind: 'suspend',
              refusalCode: 'LINE_BARRED',
              lastError: 'line is barred',
            }),
          ],
          { total: 2 },
        ),
      [`POST ${NET}/${NETWORK_A}/retry`]: () => {
        state = 'pending';
        return json(netCommand(NETWORK_A, 'pending', { attempts: 5 }), { status: 202 });
      },
    });
    open();
    fireEvent.click((await screen.findByText('C0000002')).closest('[role="row"]') as HTMLElement);
    await screen.findByRole('dialog');
    expectUi('ui.network.commands');
    const rows = await screen.findAllByText('connection reset');
    expect(rows).toHaveLength(1);
    expect(screen.getByText('LINE_BARRED')).toBeTruthy();
    // a refusal is final: its note, and exactly one Retry (the exhausted one)
    expect(screen.getByText(/A refusal is final: place a new order/)).toBeTruthy();
    expectUi('ui.network.refused');
    const retry = screen.getAllByRole('button', { name: 'Retry' });
    expect(retry).toHaveLength(1);
    fireEvent.click(retry[0] as HTMLElement);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull());
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${NET}/${NETWORK_A}/retry`);
    expect(post?.[1]?.body).toBeUndefined();
    expect(await screen.findByText('Pending')).toBeTruthy();
  });

  it('shows a 409 from Retry and keeps the button', async () => {
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([PENDING], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O2}&offset=0&limit=100`]: () => json([], { total: 0 }),
      [`GET /internal/v1/prepaid/command?billingAccount.id=${A1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${NET}?productOrder.id=${O2}&offset=0&limit=100`]: () =>
        json([netCommand(NETWORK_A, 'exhausted', { attempts: 5 })], { total: 1 }),
      [`POST ${NET}/${NETWORK_A}/retry`]: () => tmfError(409, 'only an exhausted command'),
    });
    open();
    fireEvent.click((await screen.findByText('C0000002')).closest('[role="row"]') as HTMLElement);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/only an exhausted command/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('links an in-progress order to its prepaid command', async () => {
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([PENDING], { total: 1 }),
      [`GET ${CANCELS}?productOrder.id=${O2}&offset=0&limit=100`]: () => json([], { total: 0 }),
      [`GET /internal/v1/prepaid/command?billingAccount.id=${A1}&offset=0&limit=100`]: () =>
        json([{ id: O2, productOrder: { id: O2 } }], { total: 1 }),
    });
    open();
    fireEvent.click((await screen.findByText('C0000002')).closest('[role="row"]') as HTMLElement);
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
    fireEvent.click((await screen.findByText('C0000001')).closest('[role="row"]') as HTMLElement);
    expect(await screen.findByText('The offering is retired')).toBeTruthy();
    expect(expectUi('ui.orders.errors').textContent).toContain('The offering is retired');
  });
});

// --- slice 10: the dialog's writes ---

const DRAFT = {
  id: O1,
  state: 'draft',
  billingAccount: ACCOUNT,
  creationDate: '2026-10-01T09:00:00Z',
  requestedStartDate: '2026-10-05T09:00:00Z',
  productOrderItem: [{ id: '1', action: 'add', productOffering: { id: 'o1', name: 'Basic 10' } }],
};
const REJECTED = { ...ADD, state: 'rejected' };

function writes(order: object, extra: Record<string, Handler> = {}) {
  const id = (order as { id: string }).id;
  return backend({
    [`GET ${ORDERS}?offset=0&limit=50`]: () => json([order], { total: 1 }),
    [`GET ${ORDERS}/${id}`]: () => json(order),
    [`GET ${CANCELS}?productOrder.id=${id}&offset=0&limit=100`]: () => json([], { total: 0 }),
    [`GET /internal/v1/prepaid/command?billingAccount.id=${A1}&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    ...extra,
  });
}

/** A TMF Error whose message differs from its reason, as the server's refusals do. */
const refusal = (status: number, message: string) =>
  json(
    { '@type': 'Error', code: String(status), reason: 'Conflict', message, status: String(status) },
    { status },
  );

/** The bodies of the write requests (not GETs), as "METHOD path" and the body text. */
function sent(fetchMock: ReturnType<typeof backend>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([url, init]) => ({
      call: `${init?.method} ${String(url)}`,
      body: init?.body === undefined ? undefined : String(init.body),
      headers: init?.headers as Record<string, string>,
    }));
}

async function openOrder(shortId: string) {
  fireEvent.click((await screen.findByText(shortId)).closest('[role="row"]') as HTMLElement);
  return screen.findByRole('dialog');
}

const press = (dialog: HTMLElement, name: string) =>
  fireEvent.click(within(dialog).getByRole('button', { name }));

describe('order writes', () => {
  it('reloads only the changed order, keeps the dialog and resets saved fields', async () => {
    let release: (() => void) | undefined;
    const updated = { ...DRAFT, description: 'Saved description' };
    const { calls } = writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () => json(updated),
      [`GET ${ORDERS}/${O1}`]: () =>
        new Promise((resolve) => {
          release = () => resolve(json(updated));
        }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit details');
    fireEvent.change(within(dialog).getByLabelText('Order description'), {
      target: { value: 'Saved description' },
    });
    const before = calls.filter((call) => call.startsWith(`GET ${ORDERS}?offset`));
    press(dialog, 'Save details');
    await waitFor(() => expect(release).toBeTypeOf('function'));
    expect(screen.queryByLabelText('Loading')).toBeNull();
    expect(screen.getByRole('dialog')).toBe(dialog);
    if (!release) throw new Error('Changed order was not requested');
    release();
    const saved = await within(dialog).findByText('Details saved');
    expectUi('ui.orders.detailsSaved');
    expect(saved.closest('footer')).toBeTruthy();
    expect(calls.filter((call) => call === `GET ${ORDERS}/${O1}`)).toHaveLength(1);
    expect(calls.filter((call) => call.startsWith(`GET ${ORDERS}?offset`))).toEqual(before);
    expect((within(dialog).getByLabelText('Order description') as HTMLInputElement).value).toBe(
      'Saved description',
    );
    expect(
      (within(dialog).getByRole('button', { name: 'Save details' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('submits only the metadata form from Enter and keeps its primary in the fixed footer', async () => {
    const { fetchMock } = writes(DRAFT, { [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1 }) });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit details');
    const save = within(dialog).getByRole('button', { name: 'Save details' });
    expect(save.closest('footer')?.parentElement).toBe(dialog);
    expect(
      within(dialog).getByRole('button', { name: 'Submit' }).classList.contains('secondary'),
    ).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Order description'), {
      target: { value: 'New text' },
    });
    fireEvent.keyDown(within(dialog).getByLabelText('Order description'), { key: 'Enter' });
    expect(await within(dialog).findByText('Details saved')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect(sent(fetchMock).map(({ call, body }) => ({ call, body }))).toEqual([
      { call: `PATCH ${ORDERS}/${O1}`, body: JSON.stringify({ description: 'New text' }) },
    ]);
  });

  it('does not submit a draft or cancel it on Enter from a cancellation reason', async () => {
    const { fetchMock } = writes(DRAFT);
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Cancel order');
    const reason = within(dialog).getByLabelText('Cancellation reason');
    fireEvent.change(reason, { target: { value: 'Customer left' } });
    fireEvent.keyDown(reason, { key: 'Enter' });
    expect(sent(fetchMock)).toEqual([]);
    expect(screen.getByRole('dialog')).toBe(dialog);
  });

  it('offers each action by state', async () => {
    const offered = async (order: object, shortId: string) => {
      const { unmount } = (() => {
        writes(order);
        window.history.replaceState(null, '', '/orders?state=all');
        return render(<App />);
      })();
      const dialog = await openOrder(shortId);
      const names = ['Edit details', 'Cancel order', 'Edit draft', 'Submit', 'Delete'].filter(
        (n) => within(dialog).queryByRole('button', { name: n }) !== null,
      );
      unmount();
      return names;
    };
    expect(await offered(ADD, 'C0000001')).toEqual(['Edit details', 'Cancel order']);
    expect(await offered(DRAFT, 'C0000001')).toEqual([
      'Edit details',
      'Cancel order',
      'Edit draft',
      'Submit',
      'Delete',
    ]);
    expect(await offered(REJECTED, 'C0000001')).toEqual(['Edit details', 'Delete']);
    expect(await offered(PENDING, 'C0000002')).toEqual(['Edit details', 'Cancel order']);
    expect(await offered({ ...ADD, state: 'cancelled' }, 'C0000001')).toEqual(['Edit details']);
  });

  it('cancels an order with only the order and the reason, and shows a 409 keeping the reason', async () => {
    let refuse = true;
    const { fetchMock } = writes(ADD, {
      [`POST ${CANCELS}`]: () =>
        refuse
          ? refusal(409, 'The product was already billed')
          : json({ id: 'x' }, { status: 201 }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Cancel order');
    expectUi('ui.orders.cancel');
    fireEvent.change(within(dialog).getByLabelText('Cancellation reason'), {
      target: { value: ' Customer left ' },
    });
    press(dialog, 'Cancel this order');
    expect(await within(dialog).findByText('The product was already billed')).toBeTruthy();
    expect((within(dialog).getByLabelText('Cancellation reason') as HTMLInputElement).value).toBe(
      ' Customer left ',
    );
    refuse = false;
    press(dialog, 'Cancel this order');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(sent(fetchMock).at(-1)?.call).toBe(`POST ${CANCELS}`);
    expect(JSON.parse(sent(fetchMock).at(-1)?.body ?? '')).toEqual({
      '@type': 'CancelProductOrder',
      productOrder: { id: O1, '@type': 'ProductOrderRef' },
      cancellationReason: 'Customer left',
    });
  });

  it('cancels without a reason: the reason is not sent', async () => {
    const { fetchMock } = writes(DRAFT, {
      [`POST ${CANCELS}`]: () => json({ id: 'x' }, { status: 201 }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Cancel order');
    press(dialog, 'Cancel this order');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(JSON.parse(sent(fetchMock)[0]?.body ?? '')).toEqual({
      '@type': 'CancelProductOrder',
      productOrder: { id: O1, '@type': 'ProductOrderRef' },
    });
  });

  it('edits details: only the changed fields, a cleared one removed', async () => {
    const { fetchMock } = writes(
      { ...ADD, description: 'Old', category: 'retail', priority: '2' },
      { [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1 }) },
    );
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit details');
    expectUi('ui.orders.editDetails');
    expect(
      (within(dialog).getByRole('button', { name: 'Save details' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Order description'), {
      target: { value: 'New text' },
    });
    fireEvent.change(within(dialog).getByLabelText('Order category'), { target: { value: '' } });
    fireEvent.change(within(dialog).getByLabelText('Order priority'), { target: { value: '0' } });
    press(dialog, 'Save details');
    expect(await within(dialog).findByText('Details saved')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect(sent(fetchMock)[0]?.call).toBe(`PATCH ${ORDERS}/${O1}`);
    expect(JSON.parse(sent(fetchMock)[0]?.body ?? '')).toEqual({
      description: 'New text',
      category: null,
      priority: '0',
    });
  });

  it('says it is unsure after a server error and offers Refresh', async () => {
    writes(ADD, { [`PATCH ${ORDERS}/${O1}`]: () => tmfError(503, 'Service Unavailable') });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit details');
    fireEvent.change(within(dialog).getByLabelText('Order description'), {
      target: { value: 'x' },
    });
    press(dialog, 'Save details');
    expect(await within(dialog).findByText(/Unsure whether it was saved/)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Refresh' })).toBeTruthy();
  });

  it('submits a draft with state acknowledged; a rejection shows its reasons', async () => {
    const { fetchMock } = writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () =>
        json({
          id: O1,
          state: 'rejected',
          productOrderErrorMessage: [
            { code: 'offeringNotSellable', reason: 'The offering is retired' },
          ],
        }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Submit');
    expectUi('ui.orders.submitDraft');
    const refusal = await within(dialog).findByRole('alert');
    expect(refusal.textContent).toContain('Refused.');
    expect(refusal.textContent).toContain('offeringNotSellable');
    expect(refusal.closest('.bss-write-actions')).toBeNull();
    expect(
      await within(dialog).findByText('This offering is no longer available for sale.'),
    ).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Submit' })).toBeNull(); // no longer a draft
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(sent(fetchMock)[0]?.call).toBe(`PATCH ${ORDERS}/${O1}`);
    expect(JSON.parse(sent(fetchMock)[0]?.body ?? '')).toEqual({ state: 'acknowledged' });
  });

  it('follows a submitted prepaid draft that is in progress', async () => {
    writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1, state: 'inProgress' }),
      [`GET /internal/v1/prepaid/command/${O1}`]: () =>
        json({ id: O1, state: 'pending', needs_topup: null, dueDate: '2026-10-05' }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Submit');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByText(/scheduled for 2026-10-05/)).toBeTruthy();
  });

  it('deletes a draft or a rejected order with DELETE', async () => {
    for (const order of [DRAFT, REJECTED]) {
      const { fetchMock } = writes(order, {
        [`DELETE ${ORDERS}/${O1}`]: () => new Response(null, { status: 204 }),
      });
      const view = (() => {
        window.history.replaceState(null, '', '/orders?state=all');
        return render(<App />);
      })();
      const dialog = await openOrder('C0000001');
      press(dialog, 'Delete');
      expectUi('ui.orders.deleteDraft');
      press(dialog, 'Delete order');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(await screen.findByText('No orders')).toBeTruthy();
      expect(sent(fetchMock)[0]).toEqual({
        call: `DELETE ${ORDERS}/${O1}`,
        body: undefined,
        headers: expect.anything(),
      });
      view.unmount();
    }
  });

  it('shows a 409 when deleting', async () => {
    writes(DRAFT, {
      [`DELETE ${ORDERS}/${O1}`]: () => refusal(409, 'It was processed meanwhile'),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Delete');
    press(dialog, 'Delete order');
    expect(await within(dialog).findByText('It was processed meanwhile')).toBeTruthy();
  });

  it('sends a draft of another shape to the API but still submits or deletes it', async () => {
    writes({
      ...DRAFT,
      productOrderItem: [
        ...DRAFT.productOrderItem,
        { id: '2', action: 'delete', product: { id: P1 } },
      ],
    });
    open();
    const dialog = await openOrder('C0000001');
    expect(within(dialog).queryByRole('button', { name: 'Edit draft' })).toBeNull();
    expectUi('ui.orders.draftViaApi');
    expect(within(dialog).getByRole('button', { name: 'Submit' })).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('edits a draft: Add product reopens prefilled and PATCHes item and start', async () => {
    const OFFERINGS = [
      {
        id: 'o1',
        name: 'Basic 10',
        productSpecification: { name: 'Home Internet' },
        productOfferingPrice: [{ id: 'pr1' }],
      },
      {
        id: 'o2',
        name: 'Fiber 100',
        productSpecification: { name: 'Home Internet' },
        productOfferingPrice: [{ id: 'pr2' }],
      },
    ];
    const FIRST = (id: string, start: string) => ({
      billingAccount: { id: A1 },
      productOffering: { id },
      startDate: start,
      period: { start, end: '2026-11-01' },
      cyclePeriod: { start, end: '2026-11-01' },
      billableDays: 27,
      periodDays: 30,
      prorated: false,
      price: { unit: 'USD', value: 12 },
      amount: { unit: 'USD', value: 12 },
      chargeRunDate: start,
      billDate: '2026-11-01',
      next: null,
      balance: { unit: 'USD', value: 0 },
      balanceAfter: { unit: 'USD', value: -12 },
      creditLimit: null,
      exceedsCreditLimit: false,
    });
    let settlementFails = false;
    const { fetchMock } = writes(DRAFT, {
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: () =>
        json(accountBody(A1, { id: 'cust1', name: 'Ivan Ivanov' })),
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () => json(OFFERINGS, { total: 2 }),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
        () =>
          json(
            [
              {
                id: 'pr1',
                price: { unit: 'USD', value: 12 },
                validFor: { startDateTime: '2026-06-01T21:00:00Z' },
              },
              {
                id: 'pr2',
                price: { unit: 'USD', value: 40 },
                validFor: { startDateTime: '2026-06-01T21:00:00Z' },
              },
            ],
            { total: 2 },
          ),
      [`GET /internal/v1/billingAccount/${A1}/firstCharge?productOffering.id=o1&startDate=2026-10-05`]:
        () => json(FIRST('o1', '2026-10-05')),
      [`GET /internal/v1/billingAccount/${A1}/firstCharge?productOffering.id=o2&startDate=2026-10-05`]:
        () => json(FIRST('o2', '2026-10-05')),
      // B9: owes 30 on bills, 10 unallocated credit: (10 - 30) - 12 = -32.00
      [`GET /internal/v1/billingAccount/${A1}/settlement`]: () =>
        settlementFails
          ? tmfError(500, 'settlement is down')
          : json({
              ...NO_SETTLEMENT,
              outstanding: { unit: 'USD', value: 30 },
              unallocatedCredit: { unit: 'USD', value: 10 },
            }),
      [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1, state: 'draft' }),
    });
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    open();
    const dialog = await openOrder('C0000001');
    expectUi('ui.orders.editDraft');
    press(dialog, 'Edit draft');
    const options = await screen.findAllByRole('radio'); // the account loaded, the modal swapped
    const edit = screen.getByRole('dialog', { name: 'Edit draft' });
    expectUi('ui.addProduct.saveDraftChanges');
    expect(options[0]?.getAttribute('aria-checked')).toBe('true'); // prefilled
    expect((within(edit).getByLabelText('Start date') as HTMLInputElement).value).toBe(
      '2026-10-05',
    );
    expect(within(edit).queryByRole('button', { name: /^Add / })).toBeNull();
    // B22: the draft's offering row scrolled into view once, not again on a re-render
    await within(edit).findByTestId('first-charge');
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' });
    expect(scrolled.mock.contexts[0]).toBe(options[0]);
    const balanceAfter = () =>
      within(edit).getByText('Balance after unbilled charges and this one').nextElementSibling;
    await waitFor(() => expect(balanceAfter()?.textContent).toMatch(/32\.00USD/));
    settlementFails = true;
    fireEvent.click(options[1] as HTMLElement);
    await within(edit).findByTestId('first-charge');
    await waitFor(() => expect(balanceAfter()?.textContent).toBe('—'));
    expect(scrolled).toHaveBeenCalledTimes(1);
    press(edit, 'Save draft');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(sent(fetchMock)[0]?.call).toBe(`PATCH ${ORDERS}/${O1}`);
    expect(JSON.parse(sent(fetchMock)[0]?.body ?? '')).toEqual({
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'add',
          productOffering: { id: 'o2', '@type': 'ProductOfferingRef' },
        },
      ],
      requestedStartDate: '2026-10-05T12:00:00+03:00',
    });
  });
});

// #487: what the list does around a write, a followed order and the paging edge
describe('orders around a write', () => {
  it('names a modify item that changes nothing it can name as a change of the product', () => {
    expect(
      itemText({
        '@type': 'ProductOrderItem',
        action: 'modify',
        product: { '@type': 'Product', id: P1, name: 'Home internet' },
      }),
    ).toBe('Change Home internet');
  });

  it('opens the account of a row from its link without opening the order', async () => {
    backend({
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json([ADD], { total: 1 }),
      'GET /*': () => new Promise<Response>(() => {}),
    });
    open();
    fireEvent.click(await screen.findByRole('link', { name: 'Ivan Ivanov' }));
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('closes the dialog after a submitted draft completes and shows the completed row', async () => {
    let completed = false;
    const { fetchMock } = writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () => {
        completed = true;
        return json({ id: O1, state: 'completed' });
      },
      [`GET ${ORDERS}/${O1}`]: () => json(completed ? { ...DRAFT, state: 'completed' } : DRAFT),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Submit');
    expect(await screen.findByText('Draft submitted: the order is completed')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    const row = screen.getByText('C0000001').closest('[role="row"]') as HTMLElement;
    expect(within(row).getByText('Completed')).toBeTruthy();
    expect(sent(fetchMock).map((s) => s.call)).toEqual([`PATCH ${ORDERS}/${O1}`]);
  });

  it('refreshes the followed row when the worker is done and retries a failed refresh', async () => {
    let reads = 0;
    writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1, state: 'inProgress' }),
      [`GET /internal/v1/prepaid/command/${O1}`]: () =>
        json({
          id: O1,
          state: 'completed',
          outcome: 'funded',
          needs_topup: null,
          dueDate: '2026-10-01',
          reason: null,
        }),
      // 1: the dialog's own reload after Submit; 2: the refresh after the worker, refused;
      // 3: Retry
      [`GET ${ORDERS}/${O1}`]: () => {
        reads += 1;
        if (reads === 2) return tmfError(503, 'Service Unavailable');
        return json(
          reads === 1 ? { ...DRAFT, state: 'inProgress' } : { ...DRAFT, state: 'completed' },
        );
      },
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Submit');
    expect(await screen.findByText('Order C0000001 completed')).toBeTruthy();
    expect(await screen.findByText('Failed to refresh order')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('Failed to refresh order')).toBeNull());
    const row = screen.getByText('C0000001').closest('[role="row"]') as HTMLElement;
    expect(within(row).getByText('Completed')).toBeTruthy();
    expect(reads).toBe(3);
  });

  it('dismisses a followed order without touching it', async () => {
    const { fetchMock } = writes(DRAFT, {
      [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1, state: 'inProgress' }),
      [`GET /internal/v1/prepaid/command/${O1}`]: () =>
        json({ id: O1, state: 'pending', needs_topup: null, dueDate: '2026-10-05' }),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Submit');
    await screen.findByText(/scheduled for 2026-10-05/);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText(/scheduled for 2026-10-05/)).toBeNull();
    expect(sent(fetchMock)).toHaveLength(1);
  });

  it('closes the dialog on Refresh after an unknown outcome and reloads the row', async () => {
    let reads = 0;
    writes(ADD, {
      [`PATCH ${ORDERS}/${O1}`]: () => tmfError(503, 'Service Unavailable'),
      [`GET ${ORDERS}/${O1}`]: () => {
        reads += 1;
        return json({ ...ADD, description: reads > 1 ? 'Saved after all' : undefined });
      },
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit details');
    fireEvent.change(within(dialog).getByLabelText('Order description'), {
      target: { value: 'Saved after all' },
    });
    press(dialog, 'Save details');
    await within(dialog).findByText(/Unsure whether it was saved/);
    const before = reads;
    press(dialog, 'Refresh');
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(reads).toBe(before + 1));
  });

  it('goes back from Edit draft to the order with Close, sending nothing', async () => {
    const { fetchMock } = writes(DRAFT, {
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: () =>
        new Promise<Response>(() => {}),
    });
    open();
    const dialog = await openOrder('C0000001');
    press(dialog, 'Edit draft');
    const edit = await screen.findByRole('dialog', { name: 'Edit draft' });
    expect(within(edit).getByText('Loading…')).toBeTruthy();
    fireEvent.click(within(edit).getAllByRole('button', { name: 'Close' }).at(-1) as HTMLElement);
    expect(await screen.findByRole('dialog', { name: 'Order C0000001' })).toBeTruthy();
    expect(sent(fetchMock)).toEqual([]);
  });

  it('offers Previous when the only order of a later page is deleted', async () => {
    const page = Array.from({ length: 50 }, (_, i) => ({
      ...ADD,
      id: `${(i + 100).toString(16).padStart(8, '0')}-0000-4000-8000-000000000001`,
    }));
    const { calls } = writes(DRAFT, {
      [`GET ${ORDERS}?offset=0&limit=50`]: () => json(page, { more: true }),
      [`GET ${ORDERS}?offset=50&limit=50`]: () => json([DRAFT], { more: false }),
      [`DELETE ${ORDERS}/${O1}`]: () => new Response(null, { status: 204 }),
    });
    open();
    await screen.findByText('Showing 1–50');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    const dialog = await openOrder('C0000001');
    press(dialog, 'Delete');
    press(dialog, 'Delete order');
    await screen.findByText('Order deleted');
    fireEvent.click(await screen.findByRole('button', { name: 'Previous' }));
    await screen.findByText('Showing 1–50');
    expect(calls.filter((c) => c === `GET ${ORDERS}?offset=0&limit=50`).length).toBeGreaterThan(1);
  });
});
