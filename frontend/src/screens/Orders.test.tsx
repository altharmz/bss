import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';
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
  fireEvent.click(await screen.findByText(shortId));
  return screen.findByRole('dialog');
}

const press = (dialog: HTMLElement, name: string) =>
  fireEvent.click(within(dialog).getByRole('button', { name }));

describe('order writes', () => {
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
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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
        window.history.replaceState(null, '', '/orders');
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
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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
        window.history.replaceState(null, '', '/orders');
        return render(<App />);
      })();
      const dialog = await openOrder('C0000001');
      press(dialog, 'Delete');
      expectUi('ui.orders.deleteDraft');
      press(dialog, 'Delete order');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
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
      [`PATCH ${ORDERS}/${O1}`]: () => json({ id: O1, state: 'draft' }),
    });
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
    fireEvent.click(options[1] as HTMLElement);
    await within(edit).findByTestId('first-charge');
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
