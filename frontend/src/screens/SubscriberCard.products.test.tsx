import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';
import {
  A,
  C,
  O1,
  O2,
  P1,
  P2,
  P3,
  ORDERS,
  INTERNAL,
  usd,
  ACCOUNT,
  product,
  FIRST_CHARGE,
  backend,
  open,
  productMenu,
  productAction,
  productRow,
  BUCKET,
} from '../test/subscriberCardKit';

describe('product actions', () => {
  const CANCEL = '/tmf-api/productOrderingManagement/v5/cancelProductOrder';
  const CMD = '/internal/v1/prepaid/command';
  const ORDER_ID = 'a0000001-0000-4000-8000-0000000000a1';
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const bucket = () =>
    json(
      [
        {
          id: BUCKET,
          remainingValue: { amount: 100, units: 'USD' },
          reservedValue: { amount: 0, units: 'USD' },
        },
      ],
      { total: 1 },
    );
  const FAILED = `GET ${CMD}?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`;
  const completed = () =>
    json({ id: 'o1', state: 'completed', '@type': 'ProductOrder' }, { status: 201 });
  const only = (rows: object[]) => () => json(rows, { total: rows.length });
  const products = (rows: object[]) => ({ [`GET ${INTERNAL}/products`]: only(rows) });
  const posts = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
  const menuOf = (row: HTMLElement) => within(row).queryByRole('button', { name: 'Actions' });
  const openMenu = (row: HTMLElement) =>
    fireEvent.click(within(row).getByRole('button', { name: 'Actions' }));
  const items = (row: HTMLElement) =>
    within(productMenu(row))
      .queryAllByRole('menuitem')
      .map((b) => b.textContent);
  const prepaidBackend = (routes: Record<string, Handler>) =>
    backend({ [BUCKETS]: bucket, [FAILED]: only([]), ...routes }, PREPAID);

  it('offers the menu items each status and cause allows', async () => {
    backend(
      products([
        product(P1, {}),
        product(P2, { status: 'suspended', productOffering: { id: O2, name: 'Fiber 100' } }),
        product(P3, {
          pendingChanges: [
            {
              kind: 'status',
              date: '2026-11-03',
              status: 'suspended',
              productOrder: { id: 'ord-s', href: '/o' },
            },
          ],
          productOffering: { id: O2, name: 'Pending 1' },
        }),
        product('p0000004-0000-4000-8000-000000000004', {
          status: 'pendingTerminate',
          terminationDate: '2026-10-13',
          productOffering: { id: O2, name: 'Ending 1' },
        }),
      ]),
    );
    await open();
    const active = await waitFor(() => productRow('Mobile L'));
    expect(within(active).getByRole('button', { name: 'Actions' })).toBe(
      expectUi('ui.subscriber.productActions'),
    );
    openMenu(active);
    expect(items(active)).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Deactivate',
      'Suspend…',
      'Change offering…',
      'End on a date…',
    ]);
    expect(within(productMenu(active)).getByRole('menuitem', { name: 'Suspend…' })).toBe(
      expectUi('ui.subscriber.suspend'),
    );
    expect(within(productMenu(active)).getByRole('menuitem', { name: 'End on a date…' })).toBe(
      expectUi('ui.subscriber.endOnDate'),
    );
    expect(within(productMenu(active)).getByRole('menuitem', { name: 'Change offering…' })).toBe(
      expectUi('ui.subscriber.changeOffering'),
    );
    expect(
      within(productMenu(active)).getByRole('menuitem', { name: 'Number Transfer PIN…' }),
    ).toBe(expectUi('ui.subscriber.transferPinItem'));
    const suspended = productRow('Fiber 100');
    openMenu(suspended);
    expect(items(suspended)).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Deactivate',
      'Resume…',
      'Change offering…',
      'End on a date…',
    ]);
    expect(within(productMenu(suspended)).getByRole('menuitem', { name: 'Resume…' })).toBe(
      expectUi('ui.subscriber.resume'),
    );
    // a pending status change leaves no second one; an ending product only its Undo
    const pending = productRow('Pending 1');
    expect(expectUi('ui.subscriber.pendingChange').textContent).toContain('2026-11-03');
    openMenu(pending);
    expect(items(pending)).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Deactivate',
      'Change offering…',
      'End on a date…',
    ]);
    expect(menuOf(productRow('Ending 1'))).toBeNull();
  });

  it('offers no Resume for a funding or account suspension; funding gets a Top up', async () => {
    prepaidBackend(
      products([
        product(P1, {
          status: 'suspended',
          suspensionCause: 'funding',
          paidThrough: '2026-10-12',
          needs_topup: {
            required: 30,
            available: 12.25,
            shortfall: 17.75,
            observedAt: '2026-10-12T00:30:00Z',
          },
        }),
        product(P2, {
          status: 'suspended',
          suspensionCause: 'account',
          paidThrough: '2026-10-12',
          needs_topup: null,
          productOffering: { id: O2, name: 'Fiber 100' },
        }),
        product(P3, {
          status: 'suspended',
          suspensionCause: 'manual',
          paidThrough: '2026-10-12',
          needs_topup: null,
          productOffering: { id: O2, name: 'Manual 1' },
        }),
      ]),
    );
    await open();
    const funding = await waitFor(() => productRow('Mobile L'));
    openMenu(funding);
    expect(items(funding)).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Device…',
      'Top up',
      'Deactivate',
      'Change offering…',
      'End on a date…',
    ]);
    expect(within(productMenu(funding)).getByRole('menuitem', { name: 'Top up' })).toBe(
      expectUi('ui.subscriber.topUpProduct'),
    );
    expect(within(funding).getByText(/Waiting for funds: short 17\.75/)).toBeTruthy();
    openMenu(productRow('Fiber 100'));
    expect(items(productRow('Fiber 100'))).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Device…',
      'Deactivate',
      'Change offering…',
      'End on a date…',
    ]);
    openMenu(productRow('Manual 1'));
    expect(items(productRow('Manual 1'))).toEqual([
      'Subscriber number',
      'Number Transfer PIN…',
      'Device…',
      'Deactivate',
      'Resume…',
      'Change offering…',
      'End on a date…',
    ]);

    fireEvent.click(productAction(funding, 'Top up'));
    const form = await screen.findByRole('dialog', { name: /Top up/ });
    expect((within(form).getByLabelText('Amount') as HTMLInputElement).value).toBe('17.75');
  });

  it('suspends a postpaid product on a future date with the pinned order body', async () => {
    const { fetchMock } = backend({ [`POST ${ORDERS}`]: completed });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Suspend…' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBe(expectUi('ui.subscriber.productChange'));
    expect(
      within(dialog).getByText(/Billing does not change while a product is suspended/),
    ).toBeTruthy();
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect(date.value).toBe('2026-10-01');
    expect(date.readOnly).toBe(false);
    fireEvent.change(date, { target: { value: '2026-11-03' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Mobile L: suspend recorded for 2026-11-03',
    );
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
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
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('does not end a product on Enter from its date field', async () => {
    const { fetchMock } = backend({ [`POST ${ORDERS}`]: completed });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'End on a date…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.keyDown(within(dialog).getByLabelText('Date'), { key: 'Enter' });
    expect(posts(fetchMock)).toEqual([]);
    expect(screen.getByRole('dialog')).toBe(dialog);
  });

  it('ends a postpaid product on a date with a delete item and says how it is billed', async () => {
    const { fetchMock } = backend({ [`POST ${ORDERS}`]: completed });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'End on a date…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-03' } });
    expect(
      within(dialog).getByText('The product is billed up to the day before 2026-11-03.'),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'End on a date' }));
    await screen.findByRole('status');
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-11-03T12:00:00+03:00',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'delete',
          product: { id: P1, '@type': 'ProductRef' },
        },
      ],
    });
  });

  it('resumes a prepaid product today (date fixed) and follows the accepted order', async () => {
    const { fetchMock } = prepaidBackend({
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${O1}&startDate=2026-10-01`]: () =>
        json({ ...FIRST_CHARGE, shortfall: usd(0), required: usd(585), available: usd(900) }),
      ...products([
        product(P1, {
          status: 'suspended',
          suspensionCause: 'manual',
          paidThrough: '2026-09-20',
          needs_topup: null,
        }),
      ]),
      [`POST ${ORDERS}`]: () =>
        json({ id: ORDER_ID, state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json({
          id: ORDER_ID,
          kind: 'order',
          state: 'completed',
          outcome: 'funded',
          billingAccount: { id: A, name: 'Olga' },
          dueDate: '2026-10-01',
          createdAt: '2026-10-01T06:00:00Z',
          attempts: 1,
          attemptedAt: null,
          nextRetryAt: null,
          reason: null,
          error: null,
          needs_topup: null,
          productOrder: null,
          products: [],
          billingRun: null,
          bills: [],
        }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Resume…' }));
    const dialog = screen.getByRole('dialog');
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect([date.value, date.readOnly]).toEqual(['2026-10-01', false]);
    expect(
      within(dialog).getByText(/The paid interval has ended: resuming starts a new interval/),
    ).toBeTruthy();
    await within(dialog).findByTestId('prepaid-required');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));

    expect(await screen.findByText('Resume of Mobile L completed')).toBeTruthy();
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-01T12:00:00+03:00',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: { id: P1, '@type': 'ProductRef', status: 'active' },
        },
      ],
    });
  });

  it('suspends a prepaid product on a later date with the pinned order body', async () => {
    const { fetchMock } = prepaidBackend({
      ...products([product(P1, { paidThrough: '2026-10-20', needs_topup: null })]),
      [`POST ${ORDERS}`]: () =>
        json({ id: ORDER_ID, state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json({
          id: ORDER_ID,
          kind: 'order',
          state: 'completed',
          outcome: 'funded',
          billingAccount: { id: A, name: 'Olga' },
          dueDate: '2026-10-01',
          createdAt: '2026-10-01T06:00:00Z',
          attempts: 1,
          attemptedAt: null,
          nextRetryAt: null,
          reason: null,
          error: null,
          needs_topup: null,
          productOrder: null,
          products: [],
          billingRun: null,
          bills: [],
        }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Suspend…' }));
    const dialog = screen.getByRole('dialog');
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect([date.value, date.readOnly]).toEqual(['2026-10-01', false]);
    expect(within(dialog).getByText(/Paid days are not extended by a suspension\./)).toBeTruthy();
    fireEvent.change(date, { target: { value: '2026-10-12' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));

    expect(
      await screen.findByText(/suspend accepted, the billing worker completes it/),
    ).toBeTruthy();
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-12T12:00:00+03:00',
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

  it('ends a prepaid product on a date and says unused days are credited', async () => {
    const { fetchMock } = prepaidBackend({
      ...products([product(P1, { paidThrough: '2026-10-20', needs_topup: null })]),
      [`POST ${ORDERS}`]: () =>
        json({ id: ORDER_ID, state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
      [`GET ${CMD}/${ORDER_ID}`]: () => new Promise<Response>(() => {}),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'End on a date…' }));
    const dialog = screen.getByRole('dialog');
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).readOnly).toBe(false);
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-10-10' } });
    expect(
      within(dialog).getByText('Unused paid days from 2026-10-10 are credited to the balance.'),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'End on a date' }));
    expect(
      await screen.findByText('Mobile L: end on a date accepted, the billing worker completes it'),
    ).toBeTruthy();
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toMatchObject({
      requestedStartDate: '2026-10-10T12:00:00+03:00',
      productOrderItem: [{ action: 'delete', product: { id: P1, '@type': 'ProductRef' } }],
    });
  });

  it('shows a Resume refusal after Suspend completes and the card reloads', async () => {
    let suspended = false;
    const { fetchMock } = backend({
      [`GET ${INTERNAL}/products`]: () =>
        json([product(P1, { status: suspended ? 'suspended' : 'active' })], { total: 1 }),
      [`POST ${ORDERS}`]: () => {
        if (!suspended) {
          suspended = true;
          return completed();
        }
        return json(
          {
            id: 'r1',
            state: 'rejected',
            productOrderErrorMessage: [
              { code: 'openModifyItem', reason: 'another order already changes its status today' },
            ],
          },
          { status: 201 },
        );
      },
    });
    await open();
    let row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Suspend…' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Suspend' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => url === `${INTERNAL}/products`)).toHaveLength(
        2,
      ),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Resume…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Refused.');
    expect(alert.textContent).toContain(
      'Another order already changes this product. Undo that change first.',
    );
    expect(within(alert).getByText('openModifyItem').className).toContain('code');
    expect(alert.closest('.bss-write-actions')).toBeNull();
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).value).toBe('2026-10-01');
  });

  it.each([
    ['Suspend', 'active'],
    ['Resume', 'suspended'],
    ['End on a date', 'active'],
  ])('keeps %s inputs and maps a refused order above its footer', async (action, status) => {
    backend({
      ...products([product(P1, { status })]),
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'r1',
            state: 'rejected',
            productOrderErrorMessage: [{ code: 'openModifyItem', reason: 'server reason' }],
          },
          { status: 201 },
        ),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: `${action}…` }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-03' } });
    fireEvent.click(within(dialog).getByRole('button', { name: action }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain(
      'Another order already changes this product. Undo that change first.',
    );
    expect(alert.textContent).toContain('openModifyItem');
    expect(alert.closest('.bss-write-actions')).toBeNull();
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).value).toBe('2026-11-03');
  });

  it('keeps the dialog open and shows the reasons of a rejected order', async () => {
    backend({
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o1',
            state: 'rejected',
            productOrderErrorMessage: [{ code: 'x', reason: 'the product is not active' }],
            '@type': 'ProductOrder',
          },
          { status: 201 },
        ),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Suspend…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-03' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));
    expect(await within(dialog).findByText('the product is not active')).toBeTruthy();
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).value).toBe('2026-11-03');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows pending changes and undoes one by cancelling its order', async () => {
    const pendingChanges = [
      {
        kind: 'status',
        date: '2026-11-03',
        status: 'suspended',
        productOrder: { id: 'ord-s', href: '/o/ord-s' },
      },
      {
        kind: 'offering',
        date: '2026-11-01',
        productOffering: { id: O2, name: 'Basic 20' },
        productOrder: { id: 'ord-o', href: '/o/ord-o' },
      },
    ];
    const { fetchMock } = prepaidBackend({
      ...products([product(P1, { pendingChanges, paidThrough: '2026-10-20', needs_topup: null })]),
      [`POST ${CANCEL}`]: () =>
        json({ id: 'k1', state: 'done', '@type': 'CancelProductOrder' }, { status: 201 }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    const lines = within(row).getAllByText(/on 2026-1/);
    for (const line of lines) {
      const subrow = line.closest('.subscriber-pending-change');
      expect(subrow?.getAttribute('role')).toBe('row');
      expect(subrow?.parentElement).toBe(row);
      expect(within(subrow as HTMLElement).getByText('Pending change')).toBeTruthy();
    }
    expect(lines.map((l) => l.textContent?.replace(/Undo$/, '').trim())).toEqual([
      'Suspends on 2026-11-03',
      'Changes to Basic 20 on 2026-11-01',
    ]);
    const undo = within(row).getAllByRole('button', { name: 'Undo' });
    expect(undo[0]).toBe(expectUi('ui.subscriber.undoChange'));
    fireEvent.click(undo[0] as HTMLElement);
    await screen.findByRole('status');
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'CancelProductOrder',
      productOrder: { id: 'ord-s', '@type': 'ProductOrderRef' },
    });
  });

  it('says a prepaid offering change dated in the past waits for funds, and shows a 409', async () => {
    prepaidBackend({
      ...products([
        product(P1, {
          paidThrough: '2026-09-25',
          needs_topup: null,
          pendingChanges: [
            {
              kind: 'offering',
              date: '2026-09-25',
              productOffering: { id: O2, name: 'Basic 20' },
              productOrder: { id: 'ord-o', href: '/o/ord-o' },
            },
          ],
        }),
      ]),
      [`POST ${CANCEL}`]: () => tmfError(409, 'the change was already applied'),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(
      within(row).getByText(/Changes to Basic 20 on 2026-09-25 \(waits for funds\)/),
    ).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'Undo' }));
    expect(await within(row).findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining('the change was already applied'),
    );
  });
});

describe('change offering', () => {
  const CATALOG =
    'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100';
  const PRICES =
    'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100';
  const offering = (id: string, name: string, price: string) => ({
    id,
    name,
    externalIdentifier: [{ id: name.toUpperCase(), externalIdentifierType: 'offeringCode' }],
    productSpecification: { id: 'same-spec', name: 'Spec' },
    productOfferingPrice: [{ id: price }],
  });
  const catalog = {
    [`GET /tmf-api/productCatalogManagement/v5/productOffering/${O1}`]: () =>
      json({ ...offering(O1, 'Mobile L', 'pr1'), lifecycleStatus: 'Active' }),
    [CATALOG]: () =>
      json([offering(O1, 'Mobile L', 'pr1'), offering(O2, 'Fiber 100', 'pr2')], { total: 2 }),
    [PRICES]: () =>
      json(
        [
          { id: 'pr1', price: usd(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
          { id: 'pr2', price: usd(650), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
        ],
        { total: 2 },
      ),
  };
  const only = (rows: object[]) => () => json(rows, { total: rows.length });
  const posts = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
  const preview = (start: string) =>
    `GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=${start}`;
  const forProduct = (start: string) => `${preview(start)}&product.id=${P1}`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const prepaidRoutes = {
    [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
      () =>
        json(
          [
            {
              id: BUCKET,
              remainingValue: { amount: 100, units: 'USD' },
              reservedValue: { amount: 0, units: 'USD' },
            },
          ],
          { total: 1 },
        ),
    [`GET /internal/v1/prepaid/command?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`]:
      only([]),
  };
  const completed = () =>
    json({ id: 'o1', state: 'completed', '@type': 'ProductOrder' }, { status: 201 });

  async function openDialog() {
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(within(row).getByRole('button', { name: 'Actions' }));
    fireEvent.click(within(productMenu(row)).getByRole('menuitem', { name: 'Change offering…' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBe(expectUi('ui.subscriber.offeringChange'));
    return { row, dialog };
  }

  it('filters out offerings using another specification even when the current offering is retired', async () => {
    backend({
      ...catalog,
      [`GET /tmf-api/productCatalogManagement/v5/productOffering/${O1}`]: () =>
        json({ ...offering(O1, 'Mobile L', 'pr1'), lifecycleStatus: 'Retired', isSellable: false }),
      [CATALOG]: () =>
        json(
          [
            offering(O2, 'Fiber 100', 'pr2'),
            {
              ...offering('other', 'Other specification', 'pr2'),
              productSpecification: { id: 'other-spec' },
            },
          ],
          { total: 2 },
        ),
    });
    const { dialog } = await openDialog();
    expect(await within(dialog).findByRole('radio', { name: /Fiber 100/ })).toBeTruthy();
    expect(within(dialog).queryByRole('radio', { name: /Other specification/ })).toBeNull();
  });

  it('explains when no other offering uses the current specification', async () => {
    backend({
      ...catalog,
      [CATALOG]: () =>
        json(
          [
            {
              ...offering(O2, 'Other specification', 'pr2'),
              productSpecification: { id: 'other-spec' },
            },
          ],
          { total: 1 },
        ),
    });
    const { dialog } = await openDialog();
    expect(
      await within(dialog).findByText("No other offering uses this product's specification."),
    ).toBeTruthy();
    expect(within(dialog).queryByRole('radio')).toBeNull();
  });

  it('is not offered for an ending product, a pending offering change or an unstarted prepaid product', async () => {
    backend(
      {
        [`GET ${INTERNAL}/products`]: only([
          product(P1, {
            pendingChanges: [
              {
                kind: 'offering',
                date: '2026-11-01',
                productOffering: { id: O2, name: 'Fiber 100' },
                productOrder: { id: 'ord-o', href: '/o/ord-o' },
              },
            ],
          }),
          product(P2, {
            status: 'pendingTerminate',
            terminationDate: '2026-10-13',
            productOffering: { id: O2, name: 'Ending 1' },
          }),
        ]),
      },
      ACCOUNT,
    );
    await open();
    const pending = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(within(pending).getByRole('button', { name: 'Actions' }));
    expect(
      within(productMenu(pending)).queryByRole('menuitem', { name: 'Change offering…' }),
    ).toBeNull();
    expect(within(productRow('Ending 1')).queryByRole('button', { name: 'Actions' })).toBeNull();
  });

  it('hides the item for a prepaid product without paidThrough', async () => {
    backend(
      {
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: null, needs_topup: null, suspensionCause: null }),
        ]),
      },
      PREPAID,
    );
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(within(row).getByRole('button', { name: 'Actions' }));
    expect(
      within(productMenu(row)).queryByRole('menuitem', { name: 'Change offering…' }),
    ).toBeNull();
  });

  it('changes a postpaid offering on the cycle boundary with the pinned body and previews it', async () => {
    const { fetchMock } = backend({
      ...catalog,
      [preview('2026-10-28')]: () => json({ ...FIRST_CHARGE, startDate: '2026-10-28' }),
      [`POST ${ORDERS}`]: completed,
    });
    const { dialog } = await openDialog();
    // the current offering is not a choice
    await within(dialog).findByText('Fiber 100');
    expect(within(dialog).queryByText('Mobile L')).toBeNull();
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect(date.value).toBe('2026-10-28');
    expect(date.readOnly).toBe(false);
    expect(
      within(dialog).getByText(
        'The old offering is billed until 2026-10-27, the new one from 2026-10-28; a charge already billed for the period is adjusted by a prorated refund.',
      ),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    expect(await within(dialog).findByText(/3\. New offering from 2026-10-28/)).toBeTruthy();
    expect(await within(dialog).findByTestId('first-charge')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Mobile L: change to Fiber 100 recorded for 2026-10-28',
    );
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-28T12:00:00+03:00',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: { id: P1, '@type': 'ProductRef' },
          productOffering: {
            id: O2,
            href: `/tmf-api/productCatalogManagement/v5/productOffering/${O2}`,
            '@type': 'ProductOfferingRef',
          },
        },
      ],
    });
  });

  it('shows the price at the change date midnight without reloading offerings', async () => {
    const { calls } = backend({
      ...catalog,
      [CATALOG]: () =>
        json(
          [
            offering(O1, 'Mobile L', 'pr1'),
            {
              ...offering(O2, 'Fiber 100', 'pr2'),
              productOfferingPrice: [{ id: 'pr2' }, { id: 'pr2-next' }],
            },
          ],
          { total: 2 },
        ),
      [PRICES]: () =>
        json(
          [
            { id: 'pr1', price: usd(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
            {
              id: 'pr2',
              price: usd(650),
              validFor: {
                startDateTime: '2026-06-01T21:00:00Z',
                endDateTime: '2026-10-31T21:00:00Z',
              },
            },
            {
              id: 'pr2-next',
              price: usd(700),
              validFor: { startDateTime: '2026-10-31T21:00:00Z' },
            },
          ],
          { total: 3 },
        ),
    });
    const { dialog } = await openDialog();
    const fiber = await within(dialog).findByRole('radio', { name: /Fiber 100/ });
    expect(within(fiber).getByText('650.00 / month')).toBeTruthy();
    const catalogCalls = () => calls.filter((call) => call === CATALOG || call === PRICES).length;
    const loaded = catalogCalls();
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-01' } });
    expect(within(fiber).getByText('700.00 / month')).toBeTruthy();
    expect(catalogCalls()).toBe(loaded);
  });

  it('accepts another date from today and sends it', async () => {
    const { fetchMock } = backend({
      ...catalog,
      [preview('2026-10-05')]: () => json({ ...FIRST_CHARGE, startDate: '2026-10-05' }),
      [`POST ${ORDERS}`]: completed,
    });
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-10-05' } });
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    await within(dialog).findByTestId('first-charge');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await screen.findByRole('status');
    const body = JSON.parse(String(posts(fetchMock)[0]?.[1]?.body));
    expect(body.requestedStartDate).toBe('2026-10-05T12:00:00+03:00');
  });

  it('defaults the prepaid date to paidThrough, shows the funds preview and follows an accepted order', async () => {
    let finish: (() => void) | undefined;
    const { fetchMock } = backend(
      {
        ...catalog,
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: '2026-10-20', needs_topup: null, suspensionCause: null }),
        ]),
        [forProduct('2026-10-20')]: () =>
          json({
            ...FIRST_CHARGE,
            startDate: '2026-10-20',
            required: usd(780),
            available: usd(100),
            shortfall: usd(680),
            amount: usd(780),
            balanceAfter: usd(-680),
          }),
        [`POST ${ORDERS}`]: () =>
          json({ id: 'oa', state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
        'GET /internal/v1/prepaid/command/oa': () =>
          new Promise<Response>((resolve) => {
            finish = () =>
              resolve(
                json({
                  id: 'oa',
                  kind: 'order',
                  state: 'completed',
                  outcome: 'funded',
                  billingAccount: { id: A, name: 'Olga' },
                  dueDate: '2026-10-01',
                  createdAt: '2026-10-01T06:00:00Z',
                  attempts: 1,
                  attemptedAt: null,
                  nextRetryAt: null,
                  reason: null,
                  error: null,
                  needs_topup: null,
                  productOrder: null,
                  products: [],
                  billingRun: null,
                  bills: [],
                }),
              );
          }),
      },
      PREPAID,
    );
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect(date.value).toBe('2026-10-20');
    expect(date.readOnly).toBe(false);
    expect(date.max).toBe('2026-10-20');
    fireEvent.change(date, { target: { value: '2026-10-25' } });
    // after the paid-through date the order would be refused: the dialog says so
    expect(within(dialog).getByText('Enter a date from 2026-10-01 to 2026-10-20.')).toBeTruthy();
    fireEvent.change(date, { target: { value: '2026-10-20' } });
    expect(
      within(dialog).getByText(/The change applies at the next renewal \(2026-10-20\)/),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    expect(await within(dialog).findByTestId('prepaid-required')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(posts(fetchMock)[0]?.[1]?.body));
    expect(body.requestedStartDate).toBe('2026-10-20T12:00:00+03:00');
    expect(body.productOrderItem[0].productOffering.id).toBe(O2);
    expect(body.productOrderItem[0].product).toEqual({ id: P1, '@type': 'ProductRef' });
    const accepted = await screen.findByText(
      'Mobile L: change to Fiber 100 accepted, the billing worker completes it',
    );
    expect(accepted.closest('[role="status"]')).toBeTruthy();
    await waitFor(() => expect(finish).toBeDefined());
    finish?.();
    await screen.findByText('Change of Mobile L to Fiber 100 completed');
  });

  it('changes a prepaid offering inside the paid interval and shows the credit', async () => {
    const { fetchMock } = backend(
      {
        ...catalog,
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: '2026-10-20', needs_topup: null, suspensionCause: null }),
        ]),
        [forProduct('2026-10-10')]: () =>
          json({
            ...FIRST_CHARGE,
            startDate: '2026-10-10',
            required: usd(780),
            available: usd(900),
            shortfall: usd(0),
            amount: usd(780),
            credit: usd(300),
            balanceAfter: usd(-360),
          }),
        [`POST ${ORDERS}`]: () =>
          json({ id: 'ob', state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
        'GET /internal/v1/prepaid/command/ob': () =>
          json({
            id: 'ob',
            kind: 'order',
            state: 'pending',
            outcome: null,
            billingAccount: { id: A, name: 'Olga' },
            dueDate: '2026-10-10',
            createdAt: '2026-10-01T06:00:00Z',
            attempts: 0,
            attemptedAt: null,
            nextRetryAt: null,
            reason: null,
            error: null,
            needs_topup: null,
            productOrder: null,
            products: [],
            billingRun: null,
            bills: [],
          }),
      },
      PREPAID,
    );
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-10-10' } });
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    await within(dialog).findByTestId('prepaid-required');
    expect(within(dialog).getByText('Held now')).toBeTruthy();
    expect(within(dialog).getByText('Credit for unused days')).toBeTruthy();
    expect(within(dialog).getByText('300.00 USD')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(posts(fetchMock)[0]?.[1]?.body));
    expect(body.requestedStartDate).toBe('2026-10-10T12:00:00+03:00');
    expect(body.productOrderItem[0].product).toEqual({ id: P1, '@type': 'ProductRef' });
  });

  it('uses today for a prepaid product whose paid interval has ended', async () => {
    const { fetchMock } = backend(
      {
        ...catalog,
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: '2026-09-20', needs_topup: null, suspensionCause: null }),
        ]),
        [forProduct('2026-10-01')]: () => json(FIRST_CHARGE),
        [`POST ${ORDERS}`]: completed,
      },
      PREPAID,
    );
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).value).toBe('2026-10-01');
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    await within(dialog).findByTestId('first-charge');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await screen.findByRole('status');
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body)).requestedStartDate).toBe(
      '2026-10-01T12:00:00+03:00',
    );
  });

  it('shows the server rejection and a 422 and keeps the dialog open', async () => {
    let answer = 0;
    backend({
      ...catalog,
      [preview('2026-10-28')]: () => json({ ...FIRST_CHARGE, startDate: '2026-10-28' }),
      [`POST ${ORDERS}`]: () =>
        answer++ === 0
          ? json(
              {
                id: 'o2',
                state: 'rejected',
                '@type': 'ProductOrder',
                productOrderErrorMessage: [
                  {
                    code: 'offeringChangeNotAtRenewal',
                    reason: 'a prepaid offering changes at its renewal on 2026-10-20',
                  },
                ],
              },
              { status: 201 },
            )
          : tmfError(422, 'offeringChangeNotAtRenewal: a prepaid offering changes at its renewal'),
    });
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    fireEvent.click(within(dialog).getByRole('radio', { name: /Fiber 100/ }));
    await within(dialog).findByTestId('first-charge');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    expect(await within(dialog).findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining(
        'Choose a date from today to the prepaid product’s paid-through date for the offering change.',
      ),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await waitFor(() =>
      expect(within(dialog).getByRole('alert').textContent).toContain('offeringChangeNotAtRenewal'),
    );
    expect(screen.getByRole('dialog')).toBe(dialog);
  });
});

describe('product identities (#456)', () => {
  const identities = (name: string) =>
    productRow(name).querySelector('[data-ui="ui.subscriber.productNumber"]');

  it('shows each line number, its IMSI and SIM, and — for a product without them', async () => {
    const rows = [
      product(P1, {
        msisdn: '12025550101',
        imsi: '310260000000001',
        iccid: '8901260000000000001',
      }),
      product(P2, {
        msisdn: '12025550102',
        productOffering: { id: O2, name: 'Fiber 100', code: null },
      }),
      product(P3, { productOffering: { id: O2, name: 'No number', code: null } }),
    ];
    backend({ [`GET ${INTERNAL}/products`]: () => json(rows, { total: rows.length }) });
    await open();
    await waitFor(() => productRow('Mobile L'));
    expect(screen.getByRole('columnheader', { name: 'Number' })).toBeTruthy();

    const sim = identities('Mobile L');
    expect(sim?.textContent).toBe('12025550101IMSI 310260000000001SIM 8901260000000000001');
    expectUi('ui.subscriber.productNumber');
    expect(identities('Fiber 100')?.textContent).toBe('12025550102');
    expect(identities('No number')).toBeNull();
    expect(within(productRow('No number')).getByText('—')).toBeTruthy();
  });
});
