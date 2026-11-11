import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, tmfError } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { pickOption } from '../test/combobox';
import {
  A,
  O1,
  O2,
  P1,
  P2,
  ORDERS,
  INTERNAL,
  usd,
  ACCOUNT,
  product,
  CHARGES,
  TRANSACTIONS,
  FIRST_CHARGE,
  backend,
  open,
  selectTab,
  productMenu,
  productAction,
  panel,
  productRow,
} from '../test/subscriberCardKit';

describe('product lifecycle projection', () => {
  it.each([
    ['active', null, null],
    ['grace', { action: 'suspend', date: '2026-10-08' }, 'Suspend on 2026-10-08'],
    ['suspended', { action: 'deactivate', date: '2026-11-07' }, 'Deactivate on 2026-11-07'],
  ])(
    'shows server lifecycle %s and its next action/date',
    async (state, nextTransition, caption) => {
      backend({
        [`GET ${INTERNAL}/products`]: () =>
          json([
            product(P1, {
              lifecycle: { state, nextTransition },
              suspensionCause: state === 'suspended' ? 'lifecycle' : null,
            }),
          ]),
      });
      await open();
      expect(await screen.findByText(`Lifecycle: ${state}`, { exact: false })).toBeTruthy();
      expectUi('ui.subscriber.productLifecycle');
      if (caption) expect(screen.getByText(caption, { exact: false })).toBeTruthy();
      else expect(screen.queryByText(/Suspend on|Deactivate on/)).toBeNull();
      if (state === 'suspended') {
        expect(screen.getByText('Suspension: lifecycle')).toBeTruthy();
        expectUi('ui.subscriber.lifecycleSuspension');
      }
    },
  );

  it('omits lifecycle for null and absent projections', async () => {
    backend({
      [`GET ${INTERNAL}/products`]: () => json([product(P1, { lifecycle: null }), product(P2, {})]),
    });
    await open();
    await screen.findAllByText('MOB-L · Mobile Voice & Data');
    expect(screen.queryByText(/Lifecycle:/)).toBeNull();
  });
});

describe('Ledger compact products', () => {
  it('uses separate product/status/since columns and one ellipsis menu', async () => {
    backend();
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(row.querySelector('.bss-row')?.className).toContain('density-compact');
    expect(screen.getByRole('columnheader', { name: 'Product' })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'Number' })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'Since' })).toBeTruthy();
    expect(within(row).getByText('Active').closest('[role="cell"]')).not.toBe(
      within(row).getByText('Mobile L').closest('[role="cell"]'),
    );
    expect(within(row).queryByRole('button', { name: 'Deactivate' })).toBeNull();
    const trigger = within(row).getByRole('button', { name: 'Actions ▾' });
    expect(trigger.textContent).not.toContain('Actions');
    fireEvent.click(trigger);
    expect(within(productMenu(row)).getByRole('menuitem', { name: 'Deactivate' })).toBeTruthy();
    expect(
      within(productMenu(row)).getByRole('menuitem', { name: 'Subscriber number' }),
    ).toBeTruthy();
    expect(screen.queryByText('In progress')).toBeNull();
  });
});

describe('Ledger recent activity', () => {
  it('reuses the first eight loaded movements with signed amounts and balance after', async () => {
    const movements = Array.from({ length: 20 }, (_, i) => ({
      ...TRANSACTIONS[0],
      id: `movement-${i}`,
      note: `Movement ${i}`,
      amount: usd(i === 0 ? -1 : 2),
      balanceAfter: usd(100 - i),
    }));
    const api = backend({
      [`GET ${INTERNAL}/transactions?limit=20`]: () => json(movements, { total: 20 }),
    });
    await open();
    const rail = screen.getByRole('complementary', { name: 'Recent activity' });
    expect(expectUi('ui.subscriber.activity')).toBe(rail);
    await within(rail).findByText('Movement 0');
    expect(within(rail).getAllByRole('listitem')).toHaveLength(8);
    expect(within(rail).getByText('Movement 7')).toBeTruthy();
    expect(within(rail).queryByText('Movement 8')).toBeNull();
    const first = within(rail).getAllByRole('listitem')[0];
    if (!first) throw new Error('Expected a first activity movement');
    expect(within(first).getByText('−1.00')).toBeTruthy();
    expect(within(first).getByText('100.00')).toBeTruthy();
    expect(within(first).getByText('2026-12-20')).toBeTruthy();
    expect(within(rail).getAllByText('+2.00')).toHaveLength(7);
    const calls = [...api.calls];
    fireEvent.click(within(rail).getByRole('link', { name: 'All transactions' }));
    expect(window.location.hash).toBe('#transactions');
    expect(
      screen.getByRole('tab', { name: 'Transactions 20+' }).getAttribute('aria-selected'),
    ).toBe('true');
    expect(await screen.findByRole('heading', { name: 'Transaction history' })).toBeTruthy();
    expect(api.calls).toEqual(calls);
    expect(rail.textContent).not.toContain('Holds change Reserved');
  });

  it('shows a payment method label, not its code, in the rail and the transactions table', async () => {
    backend({
      [`GET ${INTERNAL}/transactions?limit=20`]: () =>
        json([{ ...TRANSACTIONS[1], note: 'cash' }], { total: 1 }),
    });
    await open();
    const rail = screen.getByRole('complementary', { name: 'Recent activity' });
    await within(rail).findByText('Cash');
    expect(rail.textContent).not.toContain('cash');
  });

  it('shows an empty rail and explains prepaid ledger movements', async () => {
    backend(
      {
        [`GET ${INTERNAL}/transactions?limit=20`]: () => json([], { total: 0 }),
        [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
          () => json([], { total: 0 }),
      },
      { ...ACCOUNT, ratingType: 'prepaid' },
    );
    await open();
    const rail = screen.getByRole('complementary', { name: 'Recent activity' });
    expect(await within(rail).findByText('No movements yet.')).toBeTruthy();
    expect(
      within(rail).getByText(
        'Ledger balance after each movement. Holds change Reserved, not Ledger.',
      ),
    ).toBeTruthy();
  });
});

describe('subscriber card', () => {
  it.each([
    [-500.01, 500, 'Over by 0.01'],
    [-500, 500, null],
    [-499.99, 500, null],
    [-1, null, null],
  ])('shows the postpaid over-limit boundary for %s and %s', async (amount, limit, caption) => {
    backend(
      {},
      {
        ...ACCOUNT,
        accountBalance: [
          { balanceType: 'currentBalance', amount: usd(amount), '@type': 'AccountBalance' },
        ],
        creditLimit: limit === null ? null : usd(limit),
      },
    );
    await open();
    const strip = expectUi('ui.subscriber.moneyStrip');
    if (caption) expect(within(strip).getByText(caption)).toBeTruthy();
    else expect(within(strip).queryByText(/^Over by/)).toBeNull();
    expect(within(strip).getByText('next bill 2026-10-28')).toBeTruthy();
    expect(within(strip).getByRole('button', { name: 'Record payment' }).className).toContain(
      'primary',
    );
    expect(within(strip).queryByText('Reserved')).toBeNull();
  });

  it('renders the account summary as definition pairs and retains the financial-account link', async () => {
    backend(
      {},
      {
        ...ACCOUNT,
        financialAccount: {
          id: 'fa',
          name: 'Receivables',
          '@type': 'FinancialAccountRef',
          '@referredType': 'FinancialAccount',
        },
      },
    );
    await open();
    selectTab('Account');
    const summary = expectUi('ui.subscriber.account');
    expect(summary.querySelector('dl')?.className).toBe('bss-key-value');
    expect(Array.from(summary.querySelectorAll('dt')).map((el) => el.textContent)).toEqual([
      'Currency',
      'Billing cycle day',
      'Next cycle',
      'Credit limit',
      'Available incl. credit',
      'Due on bills',
      'Unused credit',
      'Financial account',
    ]);
    expect(within(summary).getByRole('link', { name: 'Receivables' }).getAttribute('href')).toBe(
      '/financial-accounts',
    );
  });

  it('links the account to its orders', async () => {
    backend({}, ACCOUNT);
    await open();
    const link = expectUi('ui.subscriber.orders');
    expect(link.getAttribute('href')).toBe(`/orders?account=${ACCOUNT.id}`);
  });

  it('links the account to its financial account', async () => {
    backend(
      {},
      {
        ...ACCOUNT,
        financialAccount: {
          id: 'f0000001-0000-4000-8000-000000000001',
          name: 'Group receivables',
          '@type': 'FinancialAccountRef',
          '@referredType': 'FinancialAccount',
        },
      },
    );
    await open();
    const link = within(expectUi('ui.subscriber.financialAccount')).getByRole('link', {
      name: 'Group receivables',
    });
    expect(link.getAttribute('href')).toBe('/financial-accounts');
  });

  it('shows no financial account when the account has none', async () => {
    backend();
    await open();
    expect(document.querySelector('[data-ui="ui.subscriber.financialAccount"]')).toBeNull();
  });

  it('shows the account: state, ids, customer status and the KPIs', async () => {
    backend();
    await open();
    const heading = screen.getByRole('heading', { name: /Olga Kuznetsova/ });
    expectUi('ui.subscriber.card');
    expect(within(heading.parentElement as HTMLElement).getByText('Active').className).toContain(
      'status-pill',
    );
    expect(screen.getByText('CCCC2222')).toBeTruthy(); // subscriber no.
    expect(screen.getAllByText('BA-AAAA1111').length).toBeGreaterThan(0);
    expect(
      await screen.findByText('Active', { selector: '.subscriber-meta .status-pill' }),
    ).toBeTruthy();

    const strip = expectUi('ui.subscriber.moneyStrip');
    const balance = within(strip).getByText('−5,784.19');
    expect(balance.closest('.bss-amount')?.className).toContain('negative');
    expect(within(strip).getByText('In arrears')).toBeTruthy();
    const kpis = expectUi('ui.subscriber.account');
    expect(within(kpis).getByText('USD')).toBeTruthy();
    expect(within(kpis).getByText('Day 28')).toBeTruthy();
    expect(within(kpis).getByText('2026-10-28')).toBeTruthy();
    expect(within(kpis).getByText('500.00')).toBeTruthy();
    // available incl. credit = -5,784.19 + 500.00
    expect(within(kpis).getByText('−5,284.19').closest('.bss-amount')?.className).toContain(
      'negative',
    );
    // breadcrumbs end in the customer's name
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumbs' });
    expect(await within(crumbs).findByText('Olga Kuznetsova')).toBeTruthy();
  });

  it('cannot add products to a suspended account', async () => {
    backend({}, { ...ACCOUNT, state: 'Suspended' });
    await open();
    const add = screen.getByRole('button', { name: 'Add product' }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(screen.getByText('Unavailable for suspended accounts')).toBeTruthy();
  });

  it('lists products with code, specification, next charge, price and their status', async () => {
    backend();
    await open();
    const active = await waitFor(() => productRow('Mobile L'));
    expect(within(active).getByText('MOB-L · Mobile Voice & Data')).toBeTruthy();
    expect(within(active).getByText('2026-10-28')).toBeTruthy();
    expect(within(active).getByText('1,290.00')).toBeTruthy();
    expect(within(active).getByText('/ mo')).toBeTruthy();
    expect(productAction(active, 'Deactivate')).toBeTruthy();

    const ending = productRow('Fiber 100');
    expect(within(ending).getByText('ends 2026-10-13')).toBeTruthy();
    expect(within(ending).getByText('Home Internet')).toBeTruthy(); // no code: spec only
    expect(within(ending).queryByRole('button', { name: 'Deactivate' })).toBeNull();
    expect(within(ending).getByText('—')).toBeTruthy(); // no next charge

    expect(screen.getByText('starts 2026-10-06')).toBeTruthy();
  });

  it('deactivates through a TMF622 delete order, confirming first', async () => {
    let completed = 0;
    const { fetchMock } = backend({
      [`POST ${ORDERS}`]: () => {
        completed++;
        return json({ id: 'o1', state: 'completed', '@type': 'ProductOrder' }, { status: 201 });
      },
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(productAction(row, 'Deactivate')).toBe(expectUi('ui.subscriber.deactivate'));
    fireEvent.click(productAction(row, 'Deactivate'));
    expect(within(screen.getByRole('dialog')).getByLabelText('Date')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(completed).toBe(0);
    fireEvent.click(productAction(row, 'Deactivate'));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'End on a date' }),
    );

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Mobile L deactivated as of 2026-10-01',
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'delete',
          product: { id: P1, '@type': 'ProductRef' },
        },
      ],
    });
    // the card reloads: products asked for a second time
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => url === `${INTERNAL}/products`)).toHaveLength(
        2,
      ),
    );
  });

  it('keeps a rejected deactivation in its dialog', async () => {
    backend({
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o1',
            state: 'rejected',
            productOrderErrorMessage: [
              { code: 'accountNotActive', reason: 'the billing account is Suspended' },
            ],
            '@type': 'ProductOrder',
          },
          { status: 201 },
        ),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(productAction(row, 'Deactivate'));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'End on a date' }),
    );
    expect(
      await within(screen.getByRole('dialog')).findByText(
        'The billing account must be active for this order.',
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull(); // no success toast
  });

  const CANCEL = '/tmf-api/productOrderingManagement/v5/cancelProductOrder';
  const ENDING_PRODUCT = {
    id: P2,
    status: 'pendingTerminate',
    productOrderItem: [
      { orderId: 'order-add', role: 'creation', orderItemAction: 'add' },
      { orderId: 'order-end', role: 'termination', orderItemAction: 'delete' },
    ],
    '@type': 'Product',
  };

  it('undoes a pending deactivation by cancelling the order that ends the product', async () => {
    const { fetchMock } = backend({
      [`GET /tmf-api/productInventory/v5/product/${P2}`]: () => json(ENDING_PRODUCT),
      [`POST ${CANCEL}`]: () =>
        json({ id: 'k1', state: 'done', '@type': 'CancelProductOrder' }, { status: 201 }),
    });
    await open();
    const row = await waitFor(() => productRow('Fiber 100'));
    const undo = within(row).getByRole('button', { name: 'Undo deactivation' });
    expect(undo).toBe(expectUi('ui.subscriber.undoDeactivation'));
    expect(
      within(productRow('Mobile L')).queryByRole('button', { name: 'Undo deactivation' }),
    ).toBeNull();
    fireEvent.click(undo);

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Fiber 100 stays active: the deactivation was cancelled',
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(CANCEL);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'CancelProductOrder',
      productOrder: { id: 'order-end', '@type': 'ProductOrderRef' },
    });
  });

  it('shows why a deactivation cannot be undone any more', async () => {
    backend({
      [`GET /tmf-api/productInventory/v5/product/${P2}`]: () => json(ENDING_PRODUCT),
      [`POST ${CANCEL}`]: () => tmfError(409, 'billing already charged its product'),
    });
    await open();
    const row = await waitFor(() => productRow('Fiber 100'));
    fireEvent.click(within(row).getByRole('button', { name: 'Undo deactivation' }));
    expect(await within(row).findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining('billing already charged its product'),
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows the charge history with periods, proration and signs', async () => {
    backend();
    await open();
    const charges = panel('Charge history');
    expect(await within(charges).findByText('2026-12-20 – 2026-12-31')).toBeTruthy(); // inclusive
    const days = within(charges).getByText('12 of 31');
    expect(days.className).toBe('bss-prorated');
    expect(within(charges).getByText('−116.13')).toBeTruthy();
    expect(within(charges).getAllByText('—').length).toBe(1); // the full-period charge
  });

  it('shows transactions signed, with the balance after each', async () => {
    backend();
    await open();
    const tx = panel('Transaction history');
    expect(await within(tx).findByText('+116.13')).toBeTruthy();
    expect(within(tx).getByText('+116.13').parentElement?.className).not.toContain('positive');
    expect(within(tx).getAllByText('−300.00')).toHaveLength(2); // amount and balance after
    expect(within(tx).getByText('−83.87')).toBeTruthy();
    expect(within(tx).getByText('Refund')).toBeTruthy();
    expect(within(tx).getByText('Bank card')).toBeTruthy();
  });

  it('shows the usage-price name in rated charges and transactions', async () => {
    backend({
      [`GET ${INTERNAL}/charges?limit=20`]: () =>
        json(
          [
            {
              ...CHARGES[1],
              productOffering: { id: O1, name: 'Metered data usage' },
            },
          ],
          { total: 1 },
        ),
      [`GET ${INTERNAL}/transactions?limit=20`]: () =>
        json([{ ...TRANSACTIONS[2], note: 'Metered data usage' }], { total: 1 }),
    });
    await open();
    expect(await within(panel('Charge history')).findByText('Metered data usage')).toBeTruthy();
    expect(
      await within(panel('Transaction history')).findByText('Metered data usage'),
    ).toBeTruthy();
  });

  it('shows a failing panel without taking the card down', async () => {
    backend({ [`GET ${INTERNAL}/charges?limit=20`]: () => tmfError(500, 'Internal Server Error') });
    await open();
    expect(await within(panel('Charge history')).findByText('Failed to load charges')).toBeTruthy();
    expect(await within(panel('Transaction history')).findByText('+116.13')).toBeTruthy();
  });
});

describe('add product', () => {
  const OFFERINGS = [
    {
      id: O1,
      name: 'Mobile L',
      externalIdentifier: [{ id: 'MOB-L', externalIdentifierType: 'offeringCode' }],
      productSpecification: { name: 'Mobile Voice & Data' },
      productOfferingPrice: [{ id: 'pr1' }],
    },
    {
      id: O2,
      name: 'Fiber 100',
      externalIdentifier: [{ id: 'NET-100', externalIdentifierType: 'offeringCode' }],
      productSpecification: { name: 'Home Internet' },
      productOfferingPrice: [{ id: 'pr2' }],
    },
  ];
  const PRICES = [
    { id: 'pr1', price: usd(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
    { id: 'pr2', price: usd(650), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
  ];
  const catalog = {
    'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
      () => json(OFFERINGS, { total: 2 }),
    'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
      () => json(PRICES, { total: 2 }),
  };
  const preview = (start: string) =>
    `GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=${start}`;

  async function openModal() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    return screen.getByRole('dialog', { name: 'Add product' });
  }

  it('requires explicit number selection for an msisdn offering', async () => {
    const numberId = '10000000-0000-4000-8000-000000000001';
    const { fetchMock } = backend({
      ...catalog,
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () =>
          json(
            OFFERINGS.map((o) => ({
              ...o,
              productSpecification: { ...o.productSpecification, id: 'number-spec' },
            })),
            { total: 2 },
          ),
      'GET /tmf-api/productCatalogManagement/v5/productSpecification/number-spec': () =>
        json({ resourceSpecification: [{ id: 'msisdn' }] }),
      'GET /tmf-api/resourceInventory/v5/resource?resourceSpecification.id=msisdn&administrativeState=unlocked&allocationStatus=available&offset=0&limit=50':
        () =>
          json(
            [
              {
                id: numberId,
                value: '12025550123',
                administrativeState: 'unlocked',
                allocationStatus: 'available',
              },
            ],
            { total: 1 },
          ),
      [preview('2026-10-01')]: () => json(FIRST_CHARGE),
      [`POST ${ORDERS}`]: () =>
        json({
          state: 'rejected',
          productOrderErrorMessage: [{ reason: 'Number no longer available' }],
        }),
    });
    const dialog = await openModal();
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    await within(dialog).findByTestId('first-charge');
    const picker = within(dialog).getByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(picker.hasAttribute('disabled')).toBe(false));
    expect(
      within(dialog).getByRole('button', { name: 'Add Fiber 100' }).hasAttribute('disabled'),
    ).toBe(true);
    pickOption(picker, '12025550123');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add Fiber 100' }));
    expect(await within(dialog).findByText('Number no longer available')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body)).productOrderItem[0].product).toEqual({
      '@type': 'ProductRefOrValue',
      realizingResource: [
        { id: numberId, '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
      ],
    });
  });

  it('ports a number in: creates the port request, then orders with the number it made (once)', async () => {
    const portedId = '10000000-0000-4000-8000-000000000009';
    let orders = 0;
    const { fetchMock } = backend({
      ...catalog,
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () =>
          json(
            OFFERINGS.map((o) => ({
              ...o,
              productSpecification: { ...o.productSpecification, id: 'number-spec' },
            })),
            { total: 2 },
          ),
      'GET /tmf-api/productCatalogManagement/v5/productSpecification/number-spec': () =>
        json({ resourceSpecification: [{ id: 'msisdn' }] }),
      'GET /tmf-api/resourceInventory/v5/resource?resourceSpecification.id=msisdn&administrativeState=unlocked&allocationStatus=available&offset=0&limit=50':
        () => json([], { total: 0 }),
      [preview('2026-10-01')]: () => json(FIRST_CHARGE),
      'POST /internal/v1/port/in': () =>
        json({ id: 'port1', resource: { id: portedId }, state: 'submitted' }, { status: 201 }),
      [`POST ${ORDERS}`]: () => {
        orders += 1;
        return orders === 1
          ? json({ state: 'rejected', productOrderErrorMessage: [{ reason: 'Try again' }] })
          : json({ id: 'o1', state: 'completed' });
      },
    });
    const dialog = await openModal();
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    await within(dialog).findByTestId('first-charge');
    fireEvent.change(await within(dialog).findByLabelText('Number source'), {
      target: { value: 'port' },
    });
    const add = within(dialog).getByRole('button', { name: 'Add Fiber 100' });
    expect(add.hasAttribute('disabled')).toBe(true);
    const type = (label: string, value: string) =>
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
    type('Number to port', '15551234567');
    type('Carrier account number', 'ACC-9');
    type('Number Transfer PIN', '123456');
    type('Billing ZIP', '12345');
    type('First name', 'Ada');
    type('Last name', 'Lovelace');
    fireEvent.click(add);
    expect(await within(dialog).findByText('Try again')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add Fiber 100' }));
    await waitFor(() => expect(orders).toBe(2));
    const ports = fetchMock.mock.calls.filter(([url]) => url === '/internal/v1/port/in');
    expect(ports).toHaveLength(1);
    expect(JSON.parse(String(ports[0]?.[1]?.body))).toEqual({
      number: '15551234567',
      carrierAccountNumber: 'ACC-9',
      transferPin: '123456',
      billingZip: '12345',
      firstName: 'Ada',
      lastName: 'Lovelace',
    });
    const post = fetchMock.mock.calls.find(([url]) => url === ORDERS);
    expect(JSON.parse(String(post?.[1]?.body)).productOrderItem[0].product).toEqual({
      '@type': 'ProductRefOrValue',
      realizingResource: [
        { id: portedId, '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
      ],
    });
  });

  it('saves the choice as a draft: requestedInitialState draft under an Idempotency-Key, a toast links to Orders', async () => {
    let saved = false;
    const { fetchMock } = backend({
      ...catalog,
      [preview('2026-10-01')]: () => json(FIRST_CHARGE),
      [`GET ${ORDERS}?billingAccount.id=${A}&state=draft&offset=0&limit=50`]: () =>
        json(
          saved
            ? [{ id: 'a0000000-0000-4000-8000-000000000001', state: 'draft', productOrderItem: [] }]
            : [],
          {
            total: saved ? 1 : 0,
          },
        ),
      [`POST ${ORDERS}`]: () => {
        saved = true;
        return json(
          { id: 'a0000000-0000-4000-8000-000000000001', state: 'draft', '@type': 'ProductOrder' },
          { status: 201 },
        );
      },
    });
    const dialog = await openModal();
    const save = within(dialog).getByRole('button', { name: 'Save as draft' });
    expect(expectUi('ui.addProduct.saveDraft')).toBe(save);
    expect((save as HTMLButtonElement).disabled).toBe(true); // no offering yet
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(save);

    const toast = await screen.findByRole('status');
    expect(toast.textContent).toBe('Draft saved · Open in Orders');
    expect(within(toast).getByRole('link').getAttribute('href')).toBe(`/orders?account=${A}`);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(
      await within(expectUi('ui.subscriber.unfinishedOrders')).findByRole('button', {
        name: 'Open',
      }),
    ).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-01T12:00:00+03:00',
      requestedInitialState: 'draft',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'add',
          productOffering: { id: O2, '@type': 'ProductOfferingRef' },
        },
      ],
    });
    expect((post?.[1]?.headers as Record<string, string>)['Idempotency-Key']).toMatch(
      /^[\x21-\x7e]{8,}$/,
    );
  });

  it('lists radio offerings, marks those already active, and previews from the backend', async () => {
    backend({ ...catalog, [preview('2026-10-01')]: () => json(FIRST_CHARGE) });
    const dialog = await openModal();
    expect(within(dialog).getByText('Olga Kuznetsova · BA-AAAA1111 · postpaid')).toBeTruthy();
    expect(within(dialog).getByText('Select an offering to see the calculation')).toBeTruthy();
    const options = await within(dialog).findAllByRole('radio');
    expect(options).toHaveLength(2);
    expect(within(options[0] as HTMLElement).getByText('already active')).toBeTruthy();
    expect(within(options[1] as HTMLElement).getByText('650.00 / month')).toBeTruthy();
    expect(
      (within(dialog).getByRole('button', { name: 'Add product' }) as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.click(options[1] as HTMLElement);
    expect(options[0]?.getAttribute('aria-checked')).toBe('false');
    expect(options[1]?.getAttribute('aria-checked')).toBe('true');
    expect(within(dialog).getByRole('button', { name: 'Add Fiber 100' })).toBeTruthy();
    expect(await within(dialog).findByTestId('first-charge')).toHaveProperty(
      'textContent',
      '585.00 USD',
    );
    expect(within(dialog).getByText('2026-10-01 – 2026-10-27')).toBeTruthy();
    expect(within(dialog).getByText('27 of 30 (prorated)')).toBeTruthy();
    expect(within(dialog).getByText('650.00 × 27 / 30')).toBeTruthy();
    expect(
      within(dialog).getByText('in the run of 2026-10-02 · on the bill of 2026-10-28'),
    ).toBeTruthy();
    expect(within(dialog).getByText('Then from 2026-10-28')).toBeTruthy();
    expect(
      within(dialog)
        .getByText(/Balance will exceed the credit limit/)
        .closest('[role="note"]')?.textContent,
    ).toContain('Balance will exceed the credit limit of 500.00 USD');
    expect(
      within(dialog).getByText(/today · the charge is written by the billing run of 2026-10-02/),
    ).toBeTruthy();
  });

  it('shows the price at the selected start midnight without reloading the catalog', async () => {
    const scheduled = {
      ...catalog,
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () =>
          json(
            OFFERINGS.map((offering) =>
              offering.id === O2
                ? { ...offering, productOfferingPrice: [{ id: 'pr2' }, { id: 'pr2-next' }] }
                : offering,
            ),
            { total: 2 },
          ),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
        () =>
          json(
            [
              ...PRICES.map((price) =>
                price.id === 'pr2'
                  ? {
                      ...price,
                      validFor: {
                        ...price.validFor,
                        endDateTime: '2026-10-05T21:00:00Z',
                      },
                    }
                  : price,
              ),
              {
                id: 'pr2-next',
                price: usd(700),
                validFor: { startDateTime: '2026-10-05T21:00:00Z' },
              },
            ],
            { total: 3 },
          ),
    };
    const { calls } = backend(scheduled);
    const dialog = await openModal();
    const fiber = await within(dialog).findByRole('radio', { name: /Fiber 100/ });
    expect(within(fiber).getByText('650.00 / month')).toBeTruthy();
    const catalogCalls = () =>
      calls.filter((call) => call.startsWith('GET /tmf-api/productCatalogManagement/')).length;
    const loaded = catalogCalls();
    fireEvent.change(within(dialog).getByLabelText('Start date'), {
      target: { value: '2026-10-06' },
    });
    expect(within(fiber).getByText('700.00 / month')).toBeTruthy();
    expect(catalogCalls()).toBe(loaded);
  });

  it('orders the offering with the start date and closes', async () => {
    const { fetchMock } = backend({
      ...catalog,
      [preview('2026-10-01')]: () => json(FIRST_CHARGE), // the default start, before the change
      [preview('2026-10-06')]: () =>
        json({ ...FIRST_CHARGE, startDate: '2026-10-06', chargeRunDate: '2026-10-06' }),
      [`POST ${ORDERS}`]: () =>
        json({ id: 'o', state: 'completed', '@type': 'ProductOrder' }, { status: 201 }),
    });
    const dialog = await openModal();
    expect(dialog).toBe(expectUi('ui.subscriber.addProduct'));
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    fireEvent.change(within(dialog).getByLabelText('Start date'), {
      target: { value: '2026-10-06' },
    });
    const activate = await within(dialog).findByRole('button', {
      name: 'Add Fiber 100',
    });
    await waitFor(() => expect((activate as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(activate);

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Fiber 100 ordered · first charge 585.00 USD in the run of 2026-10-06',
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-06T12:00:00+03:00', // noon in BILLING_TZ: the right date
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'add',
          productOffering: { id: O2, '@type': 'ProductOfferingRef' },
        },
      ],
    });
  });

  it('keeps the dialog open with the reasons when the order is rejected', async () => {
    backend({
      ...catalog,
      [preview('2026-10-01')]: () => json(FIRST_CHARGE),
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o',
            state: 'rejected',
            productOrderErrorMessage: [
              { code: 'offeringNotSellable', reason: 'productOffering Fiber 100 is not for sale' },
            ],
            '@type': 'ProductOrder',
          },
          { status: 201 },
        ),
    });
    const dialog = await openModal();
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    await within(dialog).findByTestId('first-charge');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add Fiber 100' }));
    expect(
      await within(dialog).findByText('This offering is no longer available for sale.'),
    ).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it.each(['Add Fiber 100', 'Save as draft'])(
    'keeps a refused %s submission and offering selection',
    async (action) => {
      backend({
        ...catalog,
        [preview('2026-10-01')]: () => json(FIRST_CHARGE),
        [`POST ${ORDERS}`]: () =>
          json(
            {
              id: 'r1',
              state: 'rejected',
              productOrderErrorMessage: [{ code: 'openModifyItem', reason: 'server detail' }],
            },
            { status: 201 },
          ),
      });
      const dialog = await openModal();
      const choice = (await within(dialog).findAllByRole('radio'))[1] as HTMLElement;
      fireEvent.click(choice);
      await within(dialog).findByTestId('first-charge');
      fireEvent.click(within(dialog).getByRole('button', { name: action }));
      const alert = await within(dialog).findByRole('alert');
      expect(alert.textContent).toContain('Refused.');
      expect(alert.textContent).toContain(
        'Another order already changes this product. Undo that change first.',
      );
      expect(alert.textContent).toContain('openModifyItem');
      expect(alert.closest('.bss-write-actions')).toBeNull();
      expect(choice.getAttribute('aria-checked')).toBe('true');
      expect(screen.getByRole('dialog')).toBe(dialog);
    },
  );

  it('shows why there is no preview (the server message of a 422)', async () => {
    backend({
      ...catalog,
      [preview('2026-10-01')]: () =>
        json(
          {
            '@type': 'Error',
            code: '422',
            reason: 'Unprocessable Entity',
            message: 'productOffering Fiber 100 has no recurring price in USD valid on 2026-10-01',
            status: '422',
          },
          { status: 422 },
        ),
    });
    const dialog = await openModal();
    fireEvent.click((await within(dialog).findAllByRole('radio'))[1] as HTMLElement);
    expect(
      await within(dialog).findByText(
        'productOffering Fiber 100 has no recurring price in USD valid on 2026-10-01',
      ),
    ).toBeTruthy();
    expect(
      (within(dialog).getByRole('button', { name: 'Add Fiber 100' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
