import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody, billsUrl, NO_SETTLEMENT } from '../test/fixtures';

const A = 'aaaa1111-0000-4000-8000-0000000000a1';
const C = 'cccc2222-0000-4000-8000-0000000000c2';
const O1 = 'o0000001-0000-4000-8000-000000000001';
const O2 = 'o0000002-0000-4000-8000-000000000002';
const P1 = 'p0000001-0000-4000-8000-000000000001';
const P2 = 'p0000002-0000-4000-8000-000000000002';
const P3 = 'p0000003-0000-4000-8000-000000000003';
const ORDERS = '/tmf-api/productOrderingManagement/v5/productOrder';
const INTERNAL = `/internal/v1/billingAccount/${A}`;
const HISTORY = '/tmf-api/prepayBalanceManagement/v4/balanceActionHistory';
const usd = (value: number) => ({ unit: 'USD', value });

const ACCOUNT = accountBody(
  A,
  { id: C, name: 'Olga Kuznetsova' },
  {
    accountBalance: [
      { balanceType: 'currentBalance', amount: usd(-5784.19), '@type': 'AccountBalance' },
    ],
    creditLimit: usd(500),
    billCycleDay: 28,
  },
);

function product(id: string, over: Record<string, unknown>) {
  return {
    id,
    href: `/tmf-api/productInventory/v5/product/${id}`,
    status: 'active',
    startDate: '2026-06-03',
    terminationDate: null,
    productOffering: { id: O1, name: 'Mobile L', code: 'MOB-L' },
    productSpecification: { id: 's1', name: 'Mobile Voice & Data' },
    price: usd(1290),
    nextChargeDate: '2026-10-28',
    pendingChanges: [],
    ...over,
  };
}

const PRODUCTS = [
  product(P1, {}),
  product(P2, {
    status: 'pendingTerminate',
    terminationDate: '2026-10-13',
    nextChargeDate: null,
    productOffering: { id: O2, name: 'Fiber 100', code: null },
    productSpecification: { id: 's2', name: 'Home Internet' },
    price: usd(650),
  }),
  product(P3, { status: 'created', startDate: '2026-10-06', nextChargeDate: '2026-10-06' }),
];

const CHARGES = [
  {
    id: 'c2',
    href: '/x/c2',
    kind: 'refund',
    runDate: '2026-12-20',
    period: { start: '2026-12-20', end: '2027-01-01' },
    billableDays: 12,
    periodDays: 31,
    prorated: true,
    amount: usd(-116.13),
    product: { id: P1, href: '/p' },
    productOffering: { id: O1, name: 'Mobile L' },
    bill: null,
  },
  {
    id: 'c1',
    href: '/x/c1',
    kind: 'charge',
    runDate: '2026-12-01',
    period: { start: '2026-12-01', end: '2027-01-01' },
    billableDays: 31,
    periodDays: 31,
    prorated: false,
    amount: usd(300),
    product: { id: P1, href: '/p' },
    productOffering: { id: O1, name: 'Mobile L' },
    bill: { id: 'b1', href: '/b1' },
  },
];

const TRANSACTIONS = [
  {
    id: 't3',
    href: '/t3',
    type: 'refund',
    date: '2026-12-20',
    note: 'Mobile L',
    period: null,
    amount: usd(116.13),
    balanceAfter: usd(-83.87),
  },
  {
    id: 't2',
    href: '/t2',
    type: 'payment',
    date: '2026-12-05',
    note: 'Bank card',
    period: null,
    amount: usd(100),
    balanceAfter: usd(-200),
  },
  {
    id: 't1',
    href: '/t1',
    type: 'charge',
    date: '2026-12-01',
    note: 'Mobile L',
    period: null,
    amount: usd(-300),
    balanceAfter: usd(-300),
  },
];

const FIRST_CHARGE = {
  billingAccount: { id: A },
  productOffering: { id: O2, name: 'Fiber 100' },
  startDate: '2026-10-01',
  period: { start: '2026-10-01', end: '2026-10-28' },
  cyclePeriod: { start: '2026-09-28', end: '2026-10-28' },
  billableDays: 27,
  periodDays: 30,
  prorated: true,
  price: usd(650),
  amount: usd(585),
  chargeRunDate: '2026-10-02',
  billDate: '2026-10-28',
  next: { start: '2026-10-28', amount: usd(650) },
  balance: usd(-5784.19),
  balanceAfter: usd(-6369.19),
  creditLimit: usd(500),
  exceedsCreditLimit: true,
};

function backend(routes: Record<string, Handler> = {}, account: object = ACCOUNT) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () => json(account),
    [`GET /tmf-api/customer/v5/customer/${C}`]: () =>
      json({ id: C, name: 'Olga Kuznetsova', status: 'Active', '@type': 'Customer' }),
    [`GET ${INTERNAL}/products`]: () => json(PRODUCTS, { total: PRODUCTS.length }),
    [`GET ${INTERNAL}/charges?limit=20`]: () => json(CHARGES, { total: 2 }),
    [`GET ${INTERNAL}/transactions?limit=20`]: () => json(TRANSACTIONS, { total: 3 }),
    [`GET ${INTERNAL}/settlement`]: () => json(NO_SETTLEMENT),
    // asked only of a prepaid account: no command failed technically
    [`GET /internal/v1/prepaid/command?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`]:
      () => json([], { total: 0 }),
    // asked only of a prepaid account: no balance actions yet
    [`GET ${HISTORY}?partyAccount.id=${A}&offset=0&limit=100`]: () => json([], { total: 0 }),
    [`GET ${HISTORY}?receiverBucket.id=${BUCKET}&offset=0&limit=100`]: () => json([], { total: 0 }),
    [billsUrl(A)]: () => json([], { total: 0 }),
    [`GET /tmf-api/payment/v4/refund?account.id=${A}&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    [`GET /tmf-api/customerBillManagement/v5/customerBillOnDemand?billingAccount.id=${A}&offset=0&limit=100`]:
      () => json([], { total: 0 }),
    ...routes,
  });
}

async function open() {
  window.history.replaceState(null, '', `/subscribers/${A}`);
  render(<App />);
  await screen.findByRole('heading', { name: /Olga Kuznetsova/ });
}

const panel = (title: string) =>
  screen
    .getByText(title, { selector: '.bss-panel-header .title' })
    .closest('section') as HTMLElement;

function productRow(name: string): HTMLElement {
  const row = within(panel('Active products'))
    .getAllByRole('row')
    .find((r) => within(r).queryByText(name));
  if (!row) throw new Error(`no product row ${name} yet`); // lets waitFor retry
  return row;
}

describe('subscriber card', () => {
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
    expect(within(heading).getByText('Active').className).toContain('bss-pill');
    expect(screen.getByText('CCCC2222')).toBeTruthy(); // subscriber no.
    expect(screen.getAllByText('BA-AAAA1111').length).toBeGreaterThan(0);
    expect(await screen.findByText('Active', { selector: '.bss-meta .value' })).toBeTruthy();

    const kpis = panel('Billing account');
    const balance = within(kpis).getByText('−5,784.19');
    expect(balance.className).toContain('negative');
    expect(within(kpis).getByText('In arrears')).toBeTruthy();
    expect(within(kpis).getByText('USD')).toBeTruthy();
    expect(within(kpis).getByText('Day 28')).toBeTruthy();
    expect(within(kpis).getByText('next cycle 2026-10-28')).toBeTruthy();
    expect(within(kpis).getByText('500.00')).toBeTruthy();
    // available incl. credit = -5,784.19 + 500.00
    expect(within(kpis).getByText('−5,284.19').className).toContain('negative');
    // breadcrumbs end in the customer's name
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumbs' });
    expect(await within(crumbs).findByText('Olga Kuznetsova')).toBeTruthy();
  });

  it('cannot add products to a suspended account', async () => {
    backend({}, { ...ACCOUNT, state: 'Suspended' });
    await open();
    const add = screen.getByRole('button', { name: '+ Add product' }) as HTMLButtonElement;
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
    expect(within(active).getByRole('button', { name: 'Deactivate' })).toBeTruthy();

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
    expect(within(row).getByRole('button', { name: 'Deactivate' })).toBe(
      expectUi('ui.subscriber.deactivate'),
    );
    fireEvent.click(within(row).getByRole('button', { name: 'Deactivate' }));
    expect(within(row).getByText('Deactivate?')).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'No' }));
    expect(completed).toBe(0);
    fireEvent.click(within(row).getByRole('button', { name: 'Deactivate' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Yes' }));

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

  it('shows the reasons of a rejected order in the row', async () => {
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
    fireEvent.click(within(row).getByRole('button', { name: 'Deactivate' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Yes' }));
    expect(await within(row).findByText('Rejected: the billing account is Suspended')).toBeTruthy();
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
    expect(within(tx).getByText('+116.13').parentElement?.className).toContain('positive');
    expect(within(tx).getAllByText('−300.00')).toHaveLength(2); // amount and balance after
    expect(within(tx).getByText('−83.87')).toBeTruthy();
    expect(within(tx).getByText('Refund')).toBeTruthy();
    expect(within(tx).getByText('Bank card')).toBeTruthy();
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
    fireEvent.click(screen.getByRole('button', { name: '+ Add product' }));
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
      'GET /tmf-api/resourceInventory/v5/resource?offset=0&limit=100': () =>
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
    await within(dialog).findByRole('option', { name: '12025550123' });
    expect(within(dialog).getByRole('button', { name: 'Activate' }).hasAttribute('disabled')).toBe(
      true,
    );
    fireEvent.change(within(dialog).getByLabelText('Subscriber number'), {
      target: { value: numberId },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }));
    expect(await within(dialog).findByText('Number no longer available')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body)).productOrderItem[0].product).toEqual({
      '@type': 'ProductRefOrValue',
      realizingResource: [
        { id: numberId, '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
      ],
    });
  });

  it('lists sellable offerings, marks those already active, and previews from the backend', async () => {
    backend({ ...catalog, [preview('2026-10-01')]: () => json(FIRST_CHARGE) });
    const dialog = await openModal();
    expect(within(dialog).getByText('Olga Kuznetsova · BA-AAAA1111 · billing day 28')).toBeTruthy();
    expect(within(dialog).getByText('Select an offering to see the calculation')).toBeTruthy();
    const options = await within(dialog).findAllByRole('radio');
    expect(options).toHaveLength(2);
    expect(within(options[0] as HTMLElement).getByText('already active')).toBeTruthy();
    expect(within(options[1] as HTMLElement).getByText('650.00 / month')).toBeTruthy();
    expect(
      (within(dialog).getByRole('button', { name: 'Activate' }) as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.click(options[1] as HTMLElement);
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
    expect(within(dialog).getByRole('note').textContent).toContain(
      'Balance will exceed the credit limit of 500.00 USD',
    );
    expect(
      within(dialog).getByText(/today · the charge is written by the billing run of 2026-10-02/),
    ).toBeTruthy();
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
      name: 'Activate from 2026-10-06',
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }));
    expect(
      await within(dialog).findByText('productOffering Fiber 100 is not for sale'),
    ).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

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
      (within(dialog).getByRole('button', { name: 'Activate' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

// Edit subscriber: only what changed is patched, party -> customer -> account. The bodies
// are replayed against the real API by tests/test_ui_requests.py.
const PARTY_ID = 'dddd4444-0000-4000-8000-000000000004';
const PARTY_URL = `/tmf-api/partyManagement/v5/individual/${PARTY_ID}`;
const CUSTOMER_URL = `/tmf-api/customer/v5/customer/${C}`;
const ACCOUNT_URL = `/tmf-api/accountManagement/v5/billingAccount/${A}`;

function editBackend(routes: Record<string, Handler> = {}) {
  return backend({
    [`GET ${CUSTOMER_URL}`]: () =>
      json({
        id: C,
        name: 'Olga Kuznetsova',
        status: 'Active',
        engagedParty: { id: PARTY_ID, '@type': 'PartyRef', '@referredType': 'Individual' },
        '@type': 'Customer',
      }),
    [`GET ${PARTY_URL}`]: () =>
      json({ id: PARTY_ID, givenName: 'Olga', familyName: 'Kuznetsova', '@type': 'Individual' }),
    ...routes,
  });
}

async function openEdit() {
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const dialog = await screen.findByRole('dialog', { name: 'Edit subscriber' });
  await within(dialog).findByDisplayValue('Kuznetsova');
  return dialog;
}

function patches(fetchMock: ReturnType<typeof stubFetch>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([url, init]) => [String(url), String(init?.body)]);
}

describe('edit subscriber', () => {
  it('patches only what changed: the party and the account', async () => {
    const { fetchMock } = editBackend({
      [`PATCH ${PARTY_URL}`]: () => json({ id: PARTY_ID }),
      [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT),
    });
    const dialog = await openEdit();
    expectUi('ui.subscriber.edit');
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    expect(within(dialog).getByDisplayValue('500.00')).toBeTruthy(); // the credit limit

    fireEvent.change(within(dialog).getByLabelText('Family name'), {
      target: { value: 'Kuznetsova-Petrova' },
    });
    fireEvent.click(
      within(within(dialog).getByRole('group', { name: 'Account state' })).getByRole('button', {
        name: 'Suspended',
      }),
    );
    expect(within(dialog).getByText(/takes no new orders/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '' },
    });
    fireEvent.click(save);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Saved: party, billing account',
    );
    expect(patches(fetchMock)).toEqual([
      [PARTY_URL, '{"familyName":"Kuznetsova-Petrova"}'],
      [ACCOUNT_URL, '{"state":"Suspended","creditLimit":null}'],
    ]);
  });

  it('sends a new credit limit as an exact token, and the customer status', async () => {
    const { fetchMock } = editBackend({
      [`PATCH ${CUSTOMER_URL}`]: () => json({ id: C }),
      [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT),
    });
    const dialog = await openEdit();
    fireEvent.click(
      within(within(dialog).getByRole('group', { name: 'Customer status' })).getByRole('button', {
        name: 'Inactive',
      }),
    );
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '2,000.1' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([
      [CUSTOMER_URL, '{"status":"Inactive"}'],
      [ACCOUNT_URL, '{"creditLimit":{"unit":"USD","value":2000.10}}'],
    ]);
  });

  it('says what was already saved when a later step fails', async () => {
    editBackend({
      [`PATCH ${PARTY_URL}`]: () => json({ id: PARTY_ID }),
      [`PATCH ${ACCOUNT_URL}`]: () => tmfError(422, 'Unprocessable Entity'),
    });
    const dialog = await openEdit();
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olya' } });
    fireEvent.change(within(dialog).getByLabelText('Account name'), { target: { value: 'Main' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Saving the billing account failed');
    expect(alert.textContent).toContain('Already saved: party.');
  });

  it('cannot save an empty name or a bad credit limit', async () => {
    editBackend();
    const dialog = await openEdit();
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    fireEvent.change(within(dialog).getByLabelText('Customer name'), { target: { value: ' ' } });
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Customer name'), {
      target: { value: 'Olga K' },
    });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '1.234' },
    });
    expect(within(dialog).getByText('Enter a credit limit with at most 2 decimals.')).toBeTruthy();
    expect(save.disabled).toBe(true);
  });
});

const BUCKET = 'bbbb3333-0000-4000-8000-0000000000b3';

describe('prepaid card', () => {
  const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    {
      ratingType: 'prepaid',
      accountBalance: [
        { balanceType: 'currentBalance', amount: usd(120), '@type': 'AccountBalance' },
      ],
    },
  );
  const bucket = (remaining: number, reserved: number) => () =>
    json(
      [
        {
          id: BUCKET,
          remainingValue: { amount: remaining, units: 'USD' },
          reservedValue: { amount: reserved, units: 'USD' },
        },
      ],
      { total: 1 },
    );
  const prepaidProduct = (id: string, over: Record<string, unknown>) =>
    product(id, { paidThrough: '2026-10-20', suspensionCause: null, needs_topup: null, ...over });

  it('keeps ledger, available, reserved, paid-through and shortfall distinct', async () => {
    backend(
      {
        [BUCKETS]: bucket(100.5, 19.5),
        [`GET ${INTERNAL}/products`]: () =>
          json(
            [
              prepaidProduct(P1, { nextChargeDate: '2026-10-20' }),
              prepaidProduct(P3, {
                paidThrough: '2026-10-12',
                suspensionCause: 'funding',
                nextChargeDate: '2026-10-12',
                needs_topup: {
                  required: 30,
                  available: 12.25,
                  shortfall: 17.75,
                  observedAt: '2026-10-12T00:30:00Z',
                },
              }),
            ],
            { total: 2 },
          ),
      },
      PREPAID,
    );
    await open();
    const kpis = panel('Billing account');
    const figure = (label: string) =>
      (within(kpis).getByText(label).parentElement as HTMLElement).textContent;
    expect(await within(kpis).findByText('100.50')).toBeTruthy();
    expect(figure('Ledger')).toContain('120.00');
    expect(figure('Available')).toContain('100.50');
    expect(figure('Reserved')).toContain('19.50');
    expect(await within(kpis).findByText('2026-10-12')).toBeTruthy(); // the earliest
    expect(figure('Paid through')).toContain('2026-10-12');
    expect(figure('Shortfall')).toContain('17.75');
    expect(figure('Shortfall')).toContain('needs top-up: renewal needs 30.00, available 12.25');
    expect(figure('Shortfall')).toContain('Suspension: funding ran out');
    // none of the postpaid-only figures
    expect(within(kpis).queryByText('Credit limit')).toBeNull();
    expect(within(kpis).queryByText('Available incl. credit')).toBeNull();
    expect(within(kpis).queryByText('In arrears')).toBeNull();
  });

  it('shows no shortfall for a funded account, and a debt as negative available', async () => {
    backend(
      {
        [BUCKETS]: bucket(-5, 0),
        [`GET ${INTERNAL}/products`]: () => json([prepaidProduct(P1, {})], { total: 1 }),
      },
      PREPAID,
    );
    await open();
    const kpis = panel('Billing account');
    expect((await within(kpis).findByText('−5.00')).className).toContain('negative');
    expect(await within(kpis).findByText('None')).toBeTruthy();
    expect(within(kpis).getByText('2026-10-20')).toBeTruthy();
  });

  it('reports a failing bucket without taking the card down', async () => {
    backend({ [BUCKETS]: () => tmfError(503, 'Unavailable') }, PREPAID);
    await open();
    expect(await within(panel('Billing account')).findByRole('alert')).toBeTruthy();
    expect(within(panel('Billing account')).getByText('Ledger')).toBeTruthy();
  });

  it('asks nothing of TMF654 for a postpaid account', async () => {
    const { fetchMock } = backend();
    await open();
    await within(panel('Billing account')).findByText('Credit limit');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('prepayBalance'))).toBe(false);
  });
});

describe('prepaid money forms', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const BUCKETS = `GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    {
      ratingType: 'prepaid',
      accountBalance: [
        { balanceType: 'currentBalance', amount: usd(120), '@type': 'AccountBalance' },
      ],
    },
  );
  const bucket = () =>
    json(
      [
        {
          id: BUCKET,
          remainingValue: { amount: 100.5, units: 'USD' },
          reservedValue: { amount: 19.5, units: 'USD' },
        },
      ],
      { total: 1 },
    );
  const stored = (status: string, reason?: string) => () =>
    json({ id: 'x1', status, ...(reason ? { reason } : {}) }, { status: 201 });
  const posts = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([url, init]) => ({
        url: String(url),
        body: String(init?.body),
        key: (init?.headers as Record<string, string>)['Idempotency-Key'] as string,
      }));
  const owner = `"partyAccount":{"id":"${A}","@referredType":"BillingAccount"}`;
  const quantity = (value: string) => `"amount":{"amount":${value},"units":"USD"}`;

  async function prepaid(routes: Record<string, Handler> = {}) {
    const stub = backend({ [BUCKETS]: bucket, ...routes }, PREPAID);
    await open();
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Top up' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    return stub;
  }
  const dialog = (name: string) => screen.findByRole('dialog', { name });
  const press = (scope: HTMLElement, name: string) =>
    fireEvent.click(within(scope).getByRole('button', { name }));
  const openForm = async (label: string) => {
    await waitFor(() =>
      expect((screen.getByRole('button', { name: label }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    press(document.body, label);
  };
  const type = (scope: HTMLElement, label: string, value: string) =>
    fireEvent.change(within(scope).getByLabelText(label), { target: { value } });

  it('tops up with the exact body and one Idempotency-Key, then reloads the card', async () => {
    const { fetchMock } = await prepaid({ [`POST ${PREPAY}/topupBalance`]: stored('completed') });
    press(document.body, 'Top up');
    const form = await dialog('Top up');
    expectUi('ui.subscriber.prepaid.topup');
    type(form, 'Amount', '1,250.5');
    type(form, 'Reason', ' Cash at the desk ');
    press(form, 'cash');
    press(form, 'Top up');
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Top up: 1,250.50 USD recorded',
    );
    const [sent, ...rest] = posts(fetchMock);
    expect(rest).toEqual([]);
    expect(sent?.url).toBe(`${PREPAY}/topupBalance`);
    expect(sent?.body).toBe(
      `{"@type":"TopupBalance",${quantity('1250.50')},"usageType":"monetary",` +
        `"bucket":{"id":"${BUCKET}"},${owner},"paymentMethod":{"id":"cash","name":"cash"},` +
        '"reason":"Cash at the desk"}',
    );
    expect(sent?.key).toMatch(/^[\x21-\x7e]{1,255}$/);
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/bucket?'))).toHaveLength(
        2,
      ),
    );
  });

  it('adjusts: a credit is positive, a debit is the same amount negative, no partyAccount', async () => {
    const { fetchMock } = await prepaid({ [`POST ${PREPAY}/adjustBalance`]: stored('completed') });
    press(document.body, 'Adjust');
    let form = await dialog('Adjust balance');
    expectUi('ui.subscriber.prepaid.adjust');
    type(form, 'Amount', '12.5');
    press(form, 'Adjust balance');
    await screen.findByRole('status');
    await openForm('Adjust');
    form = await dialog('Adjust balance');
    press(form, 'Debit');
    type(form, 'Amount', '12.50');
    press(form, 'Adjust balance');
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(2));
    const [credit, debit] = posts(fetchMock);
    const head = (value: string) =>
      `{"@type":"AdjustBalance",${quantity(value)},"usageType":"monetary","bucket":{"id":"${BUCKET}"}}`;
    expect(credit?.body).toBe(head('12.50'));
    expect(debit?.body).toBe(head('-12.50'));
    expect(credit?.key).not.toBe(debit?.key); // two submissions, two keys
  });

  it("holds funds, optionally through a business date (the end is that date's close)", async () => {
    const { fetchMock } = await prepaid({ [`POST ${PREPAY}/reserveBalance`]: stored('created') });
    press(document.body, 'Hold funds');
    const form = await dialog('Hold funds');
    expectUi('ui.subscriber.prepaid.hold');
    type(form, 'Amount', '20');
    type(form, 'Hold expires on', '2026-09-30');
    expect(within(form).getByText('Enter today or a later business date.')).toBeTruthy();
    expect(
      (within(form).getByRole('button', { name: 'Hold funds' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    type(form, 'Hold expires on', '2026-10-05');
    press(form, 'Hold funds');
    await screen.findByRole('status');
    expect(posts(fetchMock)[0]?.body).toBe(
      `{"@type":"ReserveBalance",${quantity('20.00')},"usageType":"monetary",` +
        `"bucket":{"id":"${BUCKET}"},${owner},` +
        '"validFor":{"endDateTime":"2026-10-06T00:00:00+03:00"}}',
    );
  });

  it('refuses a debit or hold above the available funds before sending anything', async () => {
    const { fetchMock } = await prepaid();
    press(document.body, 'Hold funds');
    const form = await dialog('Hold funds');
    type(form, 'Amount', '100.51');
    expect(within(form).getByText('Enter at most the available 100.50.')).toBeTruthy();
    expect(
      (within(form).getByRole('button', { name: 'Hold funds' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    type(form, 'Amount', '100.50');
    expect(
      (within(form).getByRole('button', { name: 'Hold funds' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(posts(fetchMock)).toEqual([]);
  });

  it('retries an uncertain outcome with the same key, and a changed form with a new one', async () => {
    let calls = 0;
    const { fetchMock } = await prepaid({
      [`POST ${PREPAY}/topupBalance`]: () =>
        ++calls < 3 ? tmfError(503, 'Unavailable') : stored('completed')(),
    });
    press(document.body, 'Top up');
    const form = await dialog('Top up');
    type(form, 'Amount', '10');
    press(form, 'Top up');
    const alert = await within(form).findByRole('alert');
    expect(alert.textContent).toContain('Unsure whether it was recorded');
    press(form, 'Retry'); // unchanged: the same submission again
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(2));
    await within(form).findByRole('alert');
    type(form, 'Amount', '11'); // a different request must not reuse the key
    press(form, 'Retry');
    await screen.findByRole('status');
    const [first, second, third] = posts(fetchMock);
    expect(second?.key).toBe(first?.key);
    expect(second?.body).toBe(first?.body);
    expect(third?.key).not.toBe(first?.key);
    expect(third?.body).toContain('11.00');
  });

  it('treats a network failure as uncertain too', async () => {
    let calls = 0;
    const { fetchMock } = await prepaid({
      [`POST ${PREPAY}/topupBalance`]: () => {
        if (++calls === 1) throw new TypeError('Failed to fetch');
        return stored('completed')();
      },
    });
    press(document.body, 'Top up');
    const form = await dialog('Top up');
    type(form, 'Amount', '10');
    press(form, 'Top up');
    await within(form).findByRole('alert');
    press(form, 'Retry');
    await screen.findByRole('status');
    const [first, second] = posts(fetchMock);
    expect(second?.key).toBe(first?.key);
  });

  it('shows a stored refusal as the reason and sends the next submission under a new key', async () => {
    let calls = 0;
    const { fetchMock } = await prepaid({
      [`POST ${PREPAY}/adjustBalance`]: () =>
        ++calls === 1 ? stored('failed', 'insufficient spendable funds')() : stored('completed')(),
    });
    press(document.body, 'Adjust');
    const form = await dialog('Adjust balance');
    press(form, 'Debit');
    type(form, 'Amount', '50');
    press(form, 'Adjust balance');
    expect((await within(form).findByRole('alert')).textContent).toBe(
      'insufficient spendable funds',
    );
    expect(screen.queryByRole('status')).toBeNull(); // nothing was recorded
    press(form, 'Adjust balance');
    await screen.findByRole('status');
    const [first, second] = posts(fetchMock);
    expect(second?.key).not.toBe(first?.key);
  });

  it('shows a definitive refusal and does not offer a blind retry', async () => {
    await prepaid({ [`POST ${PREPAY}/topupBalance`]: () => tmfError(422, 'bad amount') });
    press(document.body, 'Top up');
    const form = await dialog('Top up');
    type(form, 'Amount', '10');
    press(form, 'Top up');
    expect((await within(form).findByRole('alert')).textContent).toContain('bad amount');
    expect(within(form).queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('offers no money forms on a postpaid account', async () => {
    backend();
    await open();
    await within(panel('Billing account')).findByText('Credit limit');
    expect(screen.queryByRole('button', { name: 'Top up' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hold funds' })).toBeNull();
  });
});

describe('prepaid transfer', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const RESOURCES = '/tmf-api/resourceInventory/v5/resource';
  const PRODUCT_API = '/tmf-api/productInventory/v5/product';
  const BILLING = '/tmf-api/accountManagement/v5/billingAccount';
  const N1 = 'n0000001-0000-4000-8000-000000000001';
  const N2 = 'n0000002-0000-4000-8000-000000000002';
  const B = 'bbbb7777-0000-4000-8000-0000000000b7';
  const RX_BUCKET = 'bbbb6666-0000-4000-8000-0000000000b6';
  const TRANSFER = 'f0000001-0000-4000-8000-000000000001';
  const SENDER = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const receiverAccount = (over: Record<string, unknown> = {}) =>
    accountBody(B, { id: 'cust-b', name: 'Ivan Sidorov' }, { ratingType: 'prepaid', ...over });
  const bucketOf = (id: string, remaining: number, reserved: number) => () =>
    json(
      [
        {
          id,
          remainingValue: { amount: remaining, units: 'USD' },
          reservedValue: { amount: reserved, units: 'USD' },
        },
      ],
      { total: 1 },
    );
  const stored =
    (status: string, extra: Record<string, unknown> = {}) =>
    () =>
      json({ id: TRANSFER, status, ...extra }, { status: 201 });
  const posts = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([url, init]) => ({
        url: String(url),
        body: String(init?.body),
        key: (init?.headers as Record<string, string>)['Idempotency-Key'] as string,
      }));

  const routes = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
    [`GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`]: bucketOf(BUCKET, 100.5, 19.5),
    [`GET ${PRODUCT_API}?billingAccount.id=${A}&status=active&offset=0&limit=100`]: () =>
      json([{ id: P1, realizingResource: [{ id: N1 }] }], { total: 1 }),
    [`GET ${RESOURCES}/${N1}`]: () => json({ id: N1, value: '79991234000' }),
    [`GET ${RESOURCES}?value=79991234001&offset=0&limit=100`]: () =>
      json([{ id: N2, value: '79991234001' }], { total: 1 }),
    [`GET ${PRODUCT_API}?realizingResource.id=${N2}&offset=0&limit=100`]: () =>
      json([{ id: 'px', status: 'active', billingAccount: { id: B } }], { total: 1 }),
    [`GET ${BILLING}/${B}`]: () => json(receiverAccount()),
    [`GET ${PREPAY}/bucket?partyAccount.id=${B}&offset=0&limit=100`]: bucketOf(RX_BUCKET, 8, 0),
    ...over,
  });

  async function transferForm(extra: Record<string, Handler> = {}) {
    const stub = backend(routes(extra), SENDER);
    await open();
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Transfer' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Transfer' }));
    const form = await screen.findByRole('dialog', { name: 'Transfer funds' });
    expectUi('ui.subscriber.prepaid.transfer');
    await within(form).findByRole('button', { name: '79991234000' });
    return { ...stub, form };
  }
  const press = (scope: HTMLElement, name: string) =>
    fireEvent.click(within(scope).getByRole('button', { name }));
  const type = (scope: HTMLElement, label: string, value: string) =>
    fireEvent.change(within(scope).getByLabelText(label), { target: { value } });
  async function fill(form: HTMLElement, amount = '30', digits = '79991234001') {
    type(form, 'Receiver number', digits);
    press(form, 'Find');
    await within(form).findByText(/Ivan Sidorov/);
    type(form, 'Amount', amount);
    type(form, 'Reason', ' Customer asked ');
  }
  const review = (form: HTMLElement) =>
    within(form).getByRole('button', { name: 'Review transfer' }) as HTMLButtonElement;

  it('is offered on a prepaid account only', async () => {
    backend();
    await open();
    expect(screen.queryByRole('button', { name: 'Transfer' })).toBeNull();
  });

  it('confirms finality, sends the exact body under one key and follows it to completion', async () => {
    const { fetchMock, form } = await transferForm({
      [`POST ${PREPAY}/transferBalance`]: stored('created'),
      [`GET ${PREPAY}/transferBalance/${TRANSFER}`]: () =>
        json({ id: TRANSFER, status: 'completed' }),
    });
    await fill(form);
    press(form, 'Review transfer');
    expect(within(form).getByText(/A transfer is final/).textContent).toContain(
      'cannot be cancelled',
    );
    expect(posts(fetchMock)).toEqual([]); // nothing is sent before the confirmation
    press(form, 'Confirm transfer');
    await screen.findByText('Transfer of 30.00 USD to 79991234001 completed');
    const [sent, ...rest] = posts(fetchMock);
    expect(rest).toEqual([]);
    expect(sent?.url).toBe(`${PREPAY}/transferBalance`);
    expect(sent?.body).toBe(
      '{"@type":"TransferBalance","reason":"Customer asked",' +
        '"channel":{"id":"admin-ui","name":"Admin UI"},' +
        `"logicalResource":[{"id":"${N1}"}],"receiverLogicalResource":{"id":"${N2}"},` +
        '"amount":{"amount":30.00,"units":"USD"},"usageType":"monetary",' +
        `"bucket":{"id":"${BUCKET}"},"receiverBucket":{"id":"${RX_BUCKET}"},` +
        '"receiverBucketUsageType":"monetary"}',
    );
    expect(sent?.key).toMatch(/^[\x21-\x7e]{1,255}$/);
  });

  it('shows a stored refusal by its description, not by the operator’s own reason', async () => {
    const { form } = await transferForm({
      [`POST ${PREPAY}/transferBalance`]: stored('failed', {
        reason: 'Customer asked',
        description: 'the receiver account is closed',
      }),
    });
    await fill(form);
    press(form, 'Review transfer');
    press(form, 'Confirm transfer');
    expect((await within(form).findByRole('alert')).textContent).toBe(
      'the receiver account is closed',
    );
    expect(screen.queryByText(/accepted, waiting/)).toBeNull();
  });

  it('retries an uncertain outcome with the same key', async () => {
    let calls = 0;
    const { fetchMock, form } = await transferForm({
      [`POST ${PREPAY}/transferBalance`]: () =>
        ++calls === 1 ? tmfError(503, 'Unavailable') : stored('created')(),
      [`GET ${PREPAY}/transferBalance/${TRANSFER}`]: () =>
        json({ id: TRANSFER, status: 'completed' }),
    });
    await fill(form);
    press(form, 'Review transfer');
    press(form, 'Confirm transfer');
    expect((await within(form).findByRole('alert')).textContent).toContain(
      'Unsure whether it was recorded',
    );
    press(form, 'Retry');
    await screen.findByText('Transfer of 30.00 USD to 79991234001 completed');
    const [first, second] = posts(fetchMock);
    expect(second?.key).toBe(first?.key);
    expect(second?.body).toBe(first?.body);
  });

  it('refuses an unknown number before sending anything', async () => {
    const { fetchMock, form } = await transferForm({
      [`GET ${RESOURCES}?value=79990000000&offset=0&limit=100`]: () => json([], { total: 0 }),
    });
    type(form, 'Receiver number', '79990000000');
    press(form, 'Find');
    await within(form).findByText(/79990000000 is not in the number inventory/);
    type(form, 'Amount', '10');
    type(form, 'Reason', 'x');
    expect(review(form).disabled).toBe(true);
    expect(posts(fetchMock)).toEqual([]);
  });

  it('refuses a postpaid receiver', async () => {
    const { form } = await transferForm({
      [`GET ${BILLING}/${B}`]: () => json(receiverAccount({ ratingType: 'postpaid' })),
      [`GET ${PREPAY}/bucket?partyAccount.id=${B}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
    });
    await fill(form);
    await within(form).findByText(/not prepaid/);
    expect(review(form).disabled).toBe(true);
  });

  it('refuses an amount above the available funds and needs a reason', async () => {
    const { form } = await transferForm();
    await fill(form, '100.51'); // available is the bucket's remaining 100.50
    expect(within(form).getByText(/at most the available 100\.50/)).toBeTruthy();
    expect(review(form).disabled).toBe(true);
    type(form, 'Amount', '100.50');
    expect(review(form).disabled).toBe(false);
    type(form, 'Reason', ' ');
    expect(review(form).disabled).toBe(true);
  });

  it('refuses the account’s own number and another currency', async () => {
    const { form } = await transferForm({
      [`GET ${BILLING}/${B}`]: () => json(receiverAccount({ currency: 'EUR' })),
      [`GET ${RESOURCES}?value=79991234000&offset=0&limit=100`]: () =>
        json([{ id: N1, value: '79991234000' }], { total: 1 }),
      [`GET ${PRODUCT_API}?realizingResource.id=${N1}&offset=0&limit=100`]: () =>
        json([{ id: P1, status: 'active', billingAccount: { id: A } }], { total: 1 }),
    });
    await fill(form);
    await within(form).findByText(/currency is EUR, not USD/);
    expect(review(form).disabled).toBe(true);
    // the sender's own number resolves to the sender's account
    const own = '79991234000';
    type(form, 'Receiver number', own);
    press(form, 'Find');
    await within(form).findByText(/belongs to this account/);
    expect(review(form).disabled).toBe(true);
  });
});

describe('prepaid orders and commands', () => {
  const CMD = '/internal/v1/prepaid/command';
  const ORDER_ID = 'a0000001-0000-4000-8000-0000000000a1';
  const FAILED = `GET ${CMD}?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const command = (over: Record<string, unknown> = {}) => ({
    id: ORDER_ID,
    kind: 'order',
    state: 'pending',
    billingAccount: { id: A, name: 'Olga' },
    dueDate: '2026-10-01',
    createdAt: '2026-10-01T06:00:00Z',
    attempts: 0,
    attemptedAt: null,
    nextRetryAt: null,
    outcome: null,
    reason: null,
    error: null,
    needs_topup: null,
    productOrder: null,
    products: [],
    billingRun: null,
    bills: [],
    ...over,
  });
  const inProgress = () =>
    json({ id: ORDER_ID, state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 });
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
  const base = (routes: Record<string, Handler>) =>
    backend(
      {
        [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
          bucket,
        ...routes,
      },
      PREPAID,
    );

  /** Deactivate Mobile L: the prepaid account answers inProgress, the worker ends it. */
  async function deactivate(routes: Record<string, Handler>) {
    const stub = base({ [`POST ${ORDERS}`]: inProgress, ...routes });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(within(row).getByRole('button', { name: 'Deactivate' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Yes' }));
    return stub;
  }

  it('does not report an accepted prepaid order as a failure, and settles when the worker is done', async () => {
    await deactivate({
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json(command({ state: 'completed', outcome: 'terminated' })),
    });
    expect(await screen.findByText('Deactivation of Mobile L ended')).toBeTruthy();
    expect(screen.queryByText(/Rejected/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says a funding wait needs a top-up, apart from a technical failure', async () => {
    await deactivate({
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json(
          command({
            needs_topup: {
              required: 650,
              available: 100,
              shortfall: 550,
              observedAt: '2026-10-01T06:00:00Z',
            },
          }),
        ),
    });
    const note = await screen.findByText(/needs a top-up/);
    expectUi('ui.subscriber.prepaid.orderWatch');
    expect(note.parentElement?.textContent).toContain('requires 650.00');
    expect(note.parentElement?.textContent).toContain('short 550.00');
    expect(screen.queryByText(/technical/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Retry now' })).toBeNull();
  });

  it('says a future-dated order is scheduled instead of waiting for the worker', async () => {
    await deactivate({
      [`GET ${CMD}/${ORDER_ID}`]: () => json(command({ dueDate: '2026-10-06' })),
    });
    expect(await screen.findByText(/is scheduled for 2026-10-06/)).toBeTruthy();
    expect(screen.queryByText(/waiting for the billing worker/)).toBeNull();
  });

  it('shows a technical failure with its error and retries it without a body', async () => {
    let state = 'failedTechnical';
    const { fetchMock } = await deactivate({
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json(
          state === 'failedTechnical'
            ? command({
                state,
                error: 'connection reset',
                attempts: 1,
                nextRetryAt: '2026-10-01T06:01:00Z',
              })
            : command(),
        ),
      [`POST ${CMD}/${ORDER_ID}/retry`]: () => {
        state = 'pending';
        return json(command(), { status: 202 });
      },
    });
    await screen.findByText(/connection reset/);
    expect(expectUi('ui.subscriber.prepaid.orderWatch').textContent).toContain(
      'retries by itself after 2026-10-01 09:01',
    );
    expect(screen.queryByText(/needs a top-up/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry now' }));
    await screen.findByText(/waiting for the billing worker/);
    const retry = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/retry'));
    expect(retry?.[1]?.method).toBe('POST');
    expect(retry?.[1]?.body).toBeUndefined();
  });

  it('looks again, without an error, when a retry finds the command already retried', async () => {
    let calls = 0;
    await deactivate({
      [`GET ${CMD}/${ORDER_ID}`]: () =>
        json(++calls === 1 ? command({ state: 'failedTechnical', error: 'boom' }) : command()),
      [`POST ${CMD}/${ORDER_ID}/retry`]: () => tmfError(409, 'not failedTechnical'),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Retry now' }));
    await screen.findByText(/waiting for the billing worker/);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('lists the account’s technically failed commands and retries one', async () => {
    let failed = [
      command({
        id: 'c0000001-0000-4000-8000-0000000000c1',
        kind: 'renewal',
        dueDate: '2026-09-28',
        state: 'failedTechnical',
        error: 'deadlock detected',
      }),
    ];
    const { fetchMock } = base({
      [FAILED]: () => json(failed, { total: failed.length }),
      [`POST ${CMD}/c0000001-0000-4000-8000-0000000000c1/retry`]: () => {
        failed = [];
        return json(command(), { status: 202 });
      },
    });
    await open();
    const strip = await waitFor(() => expectUi('ui.subscriber.prepaid.failedCommands'));
    expect(strip.textContent).toContain('The renewal due 2026-09-28');
    expect(strip.textContent).toContain('deadlock detected');
    fireEvent.click(within(strip).getByRole('button', { name: 'Retry now' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes(FAILED.slice(4))),
      ).toHaveLength(2),
    );
    await waitFor(() =>
      expect(document.querySelector('[data-ui="ui.subscriber.prepaid.failedCommands"]')).toBeNull(),
    );
  });

  it('shows no commands panel when nothing failed, and asks nothing of a postpaid account', async () => {
    base({});
    await open();
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Top up' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    expect(document.querySelector('[data-ui="ui.subscriber.prepaid.failedCommands"]')).toBeNull();
    expect(screen.queryByText('Needs attention')).toBeNull();
  });

  it('accepts an inProgress add: closes the dialog and follows the order instead of showing a failure', async () => {
    const offerings = [
      {
        id: O2,
        name: 'Fiber 100',
        externalIdentifier: [{ id: 'NET-100', externalIdentifierType: 'offeringCode' }],
        productSpecification: { name: 'Home Internet' },
        productOfferingPrice: [{ id: 'pr2' }],
      },
    ];
    base({
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () => json(offerings, { total: 1 }),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
        () =>
          json(
            [{ id: 'pr2', price: usd(650), validFor: { startDateTime: '2026-06-01T21:00:00Z' } }],
            {
              total: 1,
            },
          ),
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=2026-10-01`]: () =>
        json(FIRST_CHARGE),
      [`POST ${ORDERS}`]: inProgress,
      [`GET ${CMD}/${ORDER_ID}`]: () => json(command({ state: 'completed', outcome: 'funded' })),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: '+ Add product' }));
    const dialog = screen.getByRole('dialog', { name: 'Add product' });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    const activate = await within(dialog).findByRole('button', { name: /^Activate/ });
    await waitFor(() => expect((activate as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(activate);
    expect(await screen.findByText('Order for Fiber 100 completed')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText(/The order was/)).toBeNull();
  });
});

describe('prepaid first-charge preview in Add product', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const BUCKETS = `GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const OFFER = {
    id: O2,
    name: 'Fiber 100',
    externalIdentifier: [{ id: 'NET-100', externalIdentifierType: 'offeringCode' }],
    productSpecification: { name: 'Home Internet' },
    productOfferingPrice: [{ id: 'pr2' }],
  };
  const firstCharge = (over: Record<string, unknown> = {}) => ({
    ...FIRST_CHARGE,
    period: { start: '2026-10-01', end: '2026-11-01' },
    prorated: false,
    billableDays: 31,
    periodDays: 31,
    amount: usd(780),
    next: null,
    balance: usd(120),
    balanceAfter: usd(-660),
    creditLimit: null,
    exceedsCreditLimit: true,
    required: usd(780),
    available: usd(120),
    shortfall: usd(660),
    ...over,
  });
  const urlOf = (start: string) =>
    `GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=${start}`;

  function stubbed(routes: Record<string, Handler>) {
    return backend(
      {
        [BUCKETS]: () =>
          json(
            [
              {
                id: BUCKET,
                remainingValue: { amount: 120, units: 'USD' },
                reservedValue: { amount: 0, units: 'USD' },
              },
            ],
            { total: 1 },
          ),
        'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
          () => json([OFFER], { total: 1 }),
        'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
          () =>
            json(
              [
                {
                  id: 'pr2',
                  price: usd(650),
                  validFor: { startDateTime: '2026-06-01T21:00:00Z' },
                },
              ],
              { total: 1 },
            ),
        ...routes,
      },
      PREPAID,
    );
  }

  async function chooseOffering() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: '+ Add product' }));
    const dialog = screen.getByRole('dialog', { name: 'Add product' });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    return dialog;
  }

  it('shows to pay, available, shortfall and ledger after for a start today, with one preview request', async () => {
    const { fetchMock } = stubbed({ [urlOf('2026-10-01')]: () => json(firstCharge()) });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    const box = expectUi('ui.addProduct.prepaidPreview');
    expect(within(box).getByTestId('prepaid-required').textContent).toBe('780.00 USD');
    const text = box.textContent ?? '';
    expect(text).toContain('To pay now');
    expect(text).toContain('paid in full in advance, no proration');
    expect(text).toContain('2026-10-01 – 2026-10-31');
    expect(text).toContain('Available120.00 USD');
    expect(text).toContain('Shortfall');
    expect(text).toContain('660.00');
    expect(text).toContain('Ledger after');
    expect(text).toContain('Submitting holds the full interval with its taxes');
    expect(within(dialog).queryByTestId('first-charge')).toBeNull();
    expect(within(dialog).getByText(/Insufficient funds: top up at least 660.00 USD/)).toBeTruthy();
    // submit stays enabled with a shortfall
    expect(
      (within(dialog).getByRole('button', { name: 'Activate' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/firstCharge')),
    ).toHaveLength(1);
  });

  it('hands the shortfall to the Top-up form, which sends it as the amount', async () => {
    const { fetchMock } = stubbed({
      [urlOf('2026-10-01')]: () => json(firstCharge({ shortfall: usd(1250.5) })),
      [`POST ${PREPAY}/topupBalance`]: () =>
        json({ id: 'x1', status: 'completed' }, { status: 201 }),
    });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    const button = expectUi('ui.addProduct.topUpShortfall');
    expect(button.textContent).toBe('Top up 1,250.50');
    fireEvent.click(button);
    expect(screen.queryByRole('dialog', { name: 'Add product' })).toBeNull();
    const form = await screen.findByRole('dialog', { name: 'Top up' });
    expect((within(form).getByLabelText('Amount') as HTMLInputElement).value).toBe('1250.50');
    fireEvent.click(within(form).getByRole('button', { name: 'cash' }));
    fireEvent.click(within(form).getByRole('button', { name: 'Top up' }));
    await screen.findByRole('status');
    const sent = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(String(sent?.[0])).toBe(`${PREPAY}/topupBalance`);
    expect(String(sent?.[1]?.body)).toBe(
      '{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},"usageType":"monetary",' +
        `"bucket":{"id":"${BUCKET}"},"partyAccount":{"id":"${A}","@referredType":"BillingAccount"},` +
        '"paymentMethod":{"id":"cash","name":"cash"}}',
    );
  });

  it('a future start shows a hint and no Top-up button, and no ledger after', async () => {
    stubbed({
      [urlOf('2026-10-01')]: () => json(firstCharge()),
      [urlOf('2026-10-20')]: () =>
        json(firstCharge({ period: { start: '2026-10-20', end: '2026-11-20' } })),
    });
    const dialog = await chooseOffering();
    fireEvent.change(within(dialog).getByLabelText('Start date'), {
      target: { value: '2026-10-20' },
    });
    await within(dialog).findByText('To pay on 2026-10-20');
    expect(
      within(dialog).getByText(
        'Funds are checked on 2026-10-20; nothing is held now. Top up before then.',
      ),
    ).toBeTruthy();
    expect(document.querySelector('[data-ui="ui.addProduct.topUpShortfall"]')).toBeNull();
    expect(within(dialog).queryByText(/Insufficient funds/)).toBeNull();
    expect(within(dialog).queryByText('Ledger after')).toBeNull();
    expect(
      within(dialog).getByText(
        'On 2026-10-20 the billing worker takes the interval from the funds available then.',
      ),
    ).toBeTruthy();
  });

  it('shows no shortfall, warning or button when the funds cover the interval', async () => {
    stubbed({
      [urlOf('2026-10-01')]: () =>
        json(
          firstCharge({
            available: usd(900),
            shortfall: usd(0),
            balanceAfter: usd(120),
            exceedsCreditLimit: false,
          }),
        ),
    });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    expect(within(dialog).queryByText('Shortfall')).toBeNull();
    expect(within(dialog).queryByText(/Insufficient funds/)).toBeNull();
    expect(document.querySelector('[data-ui="ui.addProduct.topUpShortfall"]')).toBeNull();
  });
});

describe('prepaid balance actions', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const B = 'bbbb7777-0000-4000-8000-0000000000b7';
  const RX_BUCKET = 'bbbb6666-0000-4000-8000-0000000000b6';
  const OWN = `GET ${HISTORY}?partyAccount.id=${A}&offset=0&limit=100`;
  const RECEIVED = `GET ${HISTORY}?receiverBucket.id=${BUCKET}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const party = { id: A, name: 'Olga Kuznetsova', '@referredType': 'BillingAccount' };
  const action = (id: string, type: string, status: string, requested: string, over = {}) => ({
    id,
    '@type': type,
    status,
    requestedDate: requested,
    partyAccount: party,
    bucket: { id: BUCKET },
    usageType: 'monetary',
    ...over,
  });
  const money = (value: number) => ({ amount: value, units: 'USD' });
  const T1 = 'a1000000-0000-4000-8000-000000000001';
  const T2 = 'a2000000-0000-4000-8000-000000000002';
  const T3 = 'a3000000-0000-4000-8000-000000000003';
  const T4 = 'a4000000-0000-4000-8000-000000000004';
  const T5 = 'a5000000-0000-4000-8000-000000000005';
  const T6 = 'a6000000-0000-4000-8000-000000000006';
  const T7 = 'a7000000-0000-4000-8000-000000000007';
  const topup = action(T1, 'TopupBalance', 'completed', '2026-10-01T05:00:00Z', {
    amount: money(50),
    reason: 'Cash at the desk',
    confirmationDate: '2026-10-01T05:00:01Z',
    paymentMethod: { id: 'cash', name: 'cash' },
  });
  const debit = action(T2, 'AdjustBalance', 'completed', '2026-10-01T06:00:00Z', {
    amount: money(-5),
    reason: 'Correction',
  });
  const credit = action(T3, 'AdjustBalance', 'cancelled', '2026-10-01T05:30:00Z', {
    amount: money(7),
  });
  const hold = action(T4, 'ReserveBalance', 'created', '2026-10-01T07:00:00Z', {
    amount: money(10),
    reason: 'Pre-authorisation',
    validFor: { endDateTime: '2026-10-02T07:00:00Z' },
  });
  const sent = action(T5, 'TransferBalance', 'completed', '2026-10-01T08:00:00Z', {
    amount: money(30),
    receiverBucket: { id: RX_BUCKET },
    transferCost: { unit: 'USD', value: 1 },
    costOwner: 'originator',
    reason: 'Customer requested credit transfer',
    confirmationDate: '2026-10-01T08:00:05Z',
  });
  const received = action(T6, 'TransferBalance', 'completed', '2026-10-01T04:00:00Z', {
    amount: money(12),
    partyAccount: { id: B, name: 'Ivan Sidorov', '@referredType': 'BillingAccount' },
    bucket: { id: RX_BUCKET },
    receiverBucket: { id: BUCKET },
    transferCost: { unit: 'USD', value: 0 },
    costOwner: 'originator',
  });
  const refused = action(T7, 'AdjustBalance', 'failed', '2026-10-01T03:00:00Z', {
    amount: money(-500),
    reason: 'Insufficient spendable funds',
  });
  const bucket = () =>
    json([{ id: BUCKET, remainingValue: money(100), reservedValue: money(10) }], { total: 1 });
  const list =
    (...bodies: object[]) =>
    () =>
      json(bodies, { total: bodies.length });
  const routes = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
    [`GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`]: bucket,
    [OWN]: list(topup, debit, credit, hold, sent, refused),
    [RECEIVED]: list(received, hold),
    [`GET ${PREPAY}/bucket/${RX_BUCKET}`]: () =>
      json({ id: RX_BUCKET, partyAccount: { id: B, name: 'Ivan Sidorov' } }),
    ...over,
  });
  const rowsOf = () =>
    within(document.querySelector('[data-ui="ui.subscriber.prepaid.history"]') as HTMLElement)
      .getAllByRole('row')
      .slice(1)
      .map((r) => r.textContent ?? '');

  it('merges the account and received-transfer requests, de-duplicated and newest first', async () => {
    const { calls } = backend(routes(), PREPAID);
    await open();
    expect(panel('Balance actions')).toBeTruthy();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    expect(calls).toContain(OWN);
    expect(calls).toContain(RECEIVED);
    const rows = rowsOf();
    expect(
      rows.map(
        (r) => r.match(/Top-up|Credit adj|Debit adj|Hold|Transfer \w+ [\w ]+?(?=[+−-]|\d)/)?.[0],
      ),
    ).toEqual([
      'Transfer to Ivan Sidorov',
      'Hold',
      'Debit adj',
      'Credit adj',
      'Top-up',
      'Transfer from Ivan Sidorov',
      'Debit adj',
    ]);
    expectUi('ui.subscriber.prepaid.history');
    // requested is the business date and time (Europe/Moscow, +03:00)
    expect(rows[0]).toContain('2026-10-01 11:00');
  });

  it('signs amounts from this account and states every status', async () => {
    backend(routes(), PREPAID);
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    const [toIvan, held, debit, cancelledCredit, topup, fromIvan, refused] = rowsOf();
    expect(toIvan).toContain('−30.00');
    expect(toIvan).toContain('Completed');
    expect(held).toContain('Held');
    expect(held).toContain('10.00');
    expect(held).not.toContain('−10.00');
    expect(held).not.toContain('+10.00');
    expect(debit).toContain('−5.00');
    expect(cancelledCredit).toContain('+7.00');
    expect(cancelledCredit).toContain('Cancelled');
    expect(topup).toContain('+50.00');
    expect(topup).toContain('Cash at the desk');
    expect(fromIvan).toContain('+12.00');
    expect(refused).toContain('Refused');
    expect(refused).toContain('Insufficient spendable funds');
  });

  it('labels a released hold and a pending transfer', async () => {
    backend(
      routes({
        [OWN]: list(
          { ...hold, status: 'cancelled' },
          { ...sent, status: 'created', confirmationDate: undefined },
        ),
        [RECEIVED]: list(),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(2));
    expect(rowsOf()[0]).toContain('Pending');
    expect(rowsOf()[1]).toContain('Released');
  });

  it('opens the detail fetched fresh, with links to both cards and the transfer cost', async () => {
    const { calls } = backend(
      routes({ [`GET ${HISTORY}/${T5}`]: () => json({ ...sent, description: 'Fresh copy' }) }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    fireEvent.click(within(panel('Balance actions')).getByText('Transfer to Ivan Sidorov'));
    const detail = await screen.findByRole('dialog', { name: 'Balance action' });
    expectUi('ui.subscriber.prepaid.historyDetail');
    expect(await within(detail).findByText('Fresh copy')).toBeTruthy();
    expect(calls).toContain(`GET ${HISTORY}/${T5}`);
    expect(within(detail).getByText('A5000000')).toBeTruthy();
    expect(within(detail).getByText('Customer requested credit transfer')).toBeTruthy();
    expect(within(detail).getByRole('link', { name: 'Ivan Sidorov' }).getAttribute('href')).toBe(
      `/subscribers/${B}`,
    );
    expect(within(detail).getByRole('link', { name: 'Olga Kuznetsova' }).getAttribute('href')).toBe(
      `/subscribers/${A}`,
    );
    expect(within(detail).getByText('1.00')).toBeTruthy();
    expect(within(detail).getByText('originator')).toBeTruthy();
  });

  it('shows the payment method of a top-up and the expiry of a hold', async () => {
    backend(
      routes({
        [`GET ${HISTORY}/${T1}`]: () => json(topup),
        [`GET ${HISTORY}/${T4}`]: () => json(hold),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    fireEvent.click(within(panel('Balance actions')).getByText('Top-up'));
    const first = await screen.findByRole('dialog', { name: 'Balance action' });
    expect(await within(first).findByText('Payment method')).toBeTruthy();
    expect(within(first).getAllByText('cash').length).toBeGreaterThan(0);
    fireEvent.click(within(first).getByRole('button', { name: 'Close' }));
    fireEvent.click(within(panel('Balance actions')).getByText('Hold'));
    const second = await screen.findByRole('dialog', { name: 'Balance action' });
    expect(await within(second).findByText('2026-10-02 10:00')).toBeTruthy();
  });

  it('says so when there are no balance actions, and when loading fails', async () => {
    backend(routes({ [OWN]: list(), [RECEIVED]: list() }), PREPAID);
    await open();
    await waitFor(() =>
      expect(within(panel('Balance actions')).getByText('No balance actions')).toBeTruthy(),
    );
  });

  it('reports a failing history request in place and keeps the card', async () => {
    backend(routes({ [OWN]: () => tmfError(500, 'database is down') }), PREPAID);
    await open();
    await waitFor(() =>
      expect(
        within(panel('Balance actions')).getByText('Failed to load the balance actions'),
      ).toBeTruthy(),
    );
    expect(screen.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeTruthy();
  });

  it('reloads the panel after a money form', async () => {
    const { fetchMock } = backend(
      routes({
        [`POST ${PREPAY}/topupBalance`]: () =>
          json({ id: 'x1', status: 'completed' }, { status: 201 }),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Top up' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
    const form = await screen.findByRole('dialog', { name: 'Top up' });
    fireEvent.change(within(form).getByLabelText('Amount'), { target: { value: '5' } });
    fireEvent.click(within(form).getByRole('button', { name: 'cash' }));
    fireEvent.click(within(form).getByRole('button', { name: 'Top up' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).startsWith(`${HISTORY}?partyAccount`)),
      ).toHaveLength(2),
    );
  });

  it('makes no TMF654 history request for a postpaid card', async () => {
    const { calls } = backend();
    await open();
    await waitFor(() => expect(screen.getByText('Transaction history')).toBeTruthy());
    expect(screen.queryByText('Balance actions')).toBeNull();
    expect(calls.filter((c) => c.includes('prepayBalanceManagement'))).toEqual([]);
  });

  it('labels the new transaction types', async () => {
    const entry = (id: string, type: string, note: string) => ({
      id,
      href: `/${id}`,
      type,
      date: '2026-10-01',
      note,
      period: null,
      amount: usd(1),
      balanceAfter: usd(1),
    });
    backend(
      routes({
        [`GET ${INTERNAL}/transactions?limit=20`]: () =>
          json(
            [
              entry('e1', 'transferOut', 'To Ivan Sidorov'),
              entry('e2', 'transferIn', 'From Ivan Sidorov'),
              entry('e3', 'reversal', 'Cancelled adjustment'),
              entry('e3', 'adjustment', 'Correction'),
            ],
            { total: 4 },
          ),
      }),
      PREPAID,
    );
    await open();
    const tx = panel('Transaction history');
    for (const label of ['Transfer out', 'Transfer in', 'Reversal', 'Adjustment'])
      expect(await within(tx).findByText(label)).toBeTruthy();
  });

  describe('writes on a balance action', () => {
    const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
      fetchMock.mock.calls
        .filter(([, init]) => init?.method === 'PATCH' || init?.method === 'DELETE')
        .map(([url, init]) => [init?.method, String(url), init?.body ? String(init.body) : null]);
    const detailOf = async (body: object, rowText: string, over: Record<string, Handler> = {}) => {
      const stub = backend(
        routes({
          [OWN]: list(body),
          [RECEIVED]: list(),
          [`GET ${HISTORY}/${(body as { id: string }).id}`]: () => json(body),
          ...over,
        }),
        PREPAID,
      );
      await open();
      await waitFor(() => expect(rowsOf()).toHaveLength(1));
      fireEvent.click(within(panel('Balance actions')).getByText(rowText));
      const dialog = await screen.findByRole('dialog', { name: 'Balance action' });
      await within(dialog).findByText('Kind');
      return { dialog, ...stub };
    };
    const buttons = (dialog: HTMLElement) =>
      within(dialog)
        .queryAllByRole('button')
        .map((b) => b.textContent)
        .filter((t) => t !== '×');

    it('cancels a completed top-up after a confirmation, then reloads the card', async () => {
      const { dialog, fetchMock, calls } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => json({ ...topup, status: 'cancelled' }),
      });
      expect(buttons(dialog)).toEqual(['Cancel top-up', 'Edit note']);
      expectUi('ui.subscriber.prepaid.cancelTopup');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      expect(
        within(dialog).getByText(/Takes 50\.00 back off the balance\. Only possible while none/),
      ).toBeTruthy();
      expect(writes(fetchMock)).toEqual([]);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect(await screen.findByText('Top-up cancelled')).toBeTruthy();
      expect(writes(fetchMock)).toEqual([
        ['PATCH', `${PREPAY}/topupBalance/${T1}`, '{"status":"cancelled"}'],
      ]);
      await waitFor(() => expect(calls.filter((c) => c === OWN).length).toBeGreaterThan(1));
    });

    it('cancels an adjustment and releases a hold with the same body', async () => {
      const first = await detailOf(debit, 'Debit adjustment', {
        [`PATCH ${PREPAY}/adjustBalance/${T2}`]: () => json({ ...debit, status: 'cancelled' }),
      });
      expectUi('ui.subscriber.prepaid.cancelAdjustment');
      fireEvent.click(within(first.dialog).getByRole('button', { name: 'Cancel adjustment' }));
      expect(within(first.dialog).getByText('Reverses this debit of 5.00.')).toBeTruthy();
      fireEvent.click(within(first.dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(first.fetchMock)).toEqual([
          ['PATCH', `${PREPAY}/adjustBalance/${T2}`, '{"status":"cancelled"}'],
        ]),
      );
    });

    it('releases a hold', async () => {
      const { dialog, fetchMock } = await detailOf(hold, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json({ ...hold, status: 'cancelled' }),
      });
      expectUi('ui.subscriber.prepaid.releaseHold');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Release hold' }));
      expect(within(dialog).getByText('Makes 10.00 available again.')).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/reserveBalance/${T4}`,
            '{"status":"cancelled","reason":"Pre-authorisation"}',
          ],
        ]),
      );
    });

    it('releases a hold without a reason with a stated one, which ReserveBalance_Update requires', async () => {
      const bare = { ...hold, reason: undefined };
      const { dialog, fetchMock } = await detailOf(bare, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json({ ...bare, status: 'cancelled' }),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Release hold' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/reserveBalance/${T4}`,
            '{"status":"cancelled","reason":"Released by operator"}',
          ],
        ]),
      );
    });

    it('edits the note of a hold with its status and reason, and does not allow clearing the reason', async () => {
      const { dialog, fetchMock } = await detailOf(hold, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json(hold),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Edit note' }));
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: '' } });
      const save = within(dialog).getByRole('button', { name: 'Save note' }) as HTMLButtonElement;
      expect(save.disabled).toBe(true);
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'Deposit' } });
      fireEvent.click(save);
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          ['PATCH', `${PREPAY}/reserveBalance/${T4}`, '{"status":"created","reason":"Deposit"}'],
        ]),
      );
    });

    it('removes a refused action with a DELETE and no body, on any path', async () => {
      const { dialog, fetchMock } = await detailOf(refused, 'Debit adjustment', {
        [`DELETE ${PREPAY}/adjustBalance/${T7}`]: () => new Response(null, { status: 204 }),
      });
      expect(buttons(dialog)).toEqual(['Remove']);
      expectUi('ui.subscriber.prepaid.removeFailed');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
      expect(
        within(dialog).getByText('Deletes this refused request. It moved no money.'),
      ).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([['DELETE', `${PREPAY}/adjustBalance/${T7}`, null]]),
      );
    });

    it('removes a refused transfer through transferBalance', async () => {
      const failedTransfer = { ...sent, status: 'failed' };
      const { dialog, fetchMock } = await detailOf(failedTransfer, 'Transfer to Ivan Sidorov', {
        [`DELETE ${PREPAY}/transferBalance/${T5}`]: () => new Response(null, { status: 204 }),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([['DELETE', `${PREPAY}/transferBalance/${T5}`, null]]),
      );
    });

    it('offers the buttons the kind and status allow', async () => {
      const done = await detailOf({ ...credit, status: 'completed' }, 'Credit adjustment');
      expect(buttons(done.dialog)).toEqual(['Cancel adjustment', 'Edit note']);
    });

    it('offers nothing on a completed transfer and only Edit note on a cancelled top-up', async () => {
      const transfer = await detailOf(sent, 'Transfer to Ivan Sidorov');
      expect(buttons(transfer.dialog)).toEqual([]);
    });

    it('offers only Edit note on a cancelled top-up', async () => {
      const { dialog } = await detailOf({ ...topup, status: 'cancelled' }, 'Top-up');
      expect(buttons(dialog)).toEqual(['Edit note']);
    });

    it('sends only the changed note fields, an emptied one as null', async () => {
      const { dialog, fetchMock } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => json(topup),
      });
      expectUi('ui.subscriber.prepaid.editNote');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Edit note' }));
      expectUi('ui.subscriber.prepaid.editNoteForm');
      const save = within(dialog).getByRole('button', { name: 'Save note' }) as HTMLButtonElement;
      expect(save.disabled).toBe(true);
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: '' } });
      fireEvent.change(within(dialog).getByLabelText('Description'), {
        target: { value: ' Checked ' },
      });
      fireEvent.click(save);
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/topupBalance/${T1}`,
            '{"status":"completed","reason":null,"description":"Checked"}',
          ],
        ]),
      );
    });

    it('shows a 409 in the dialog and keeps it open', async () => {
      const { dialog, fetchMock } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => tmfError(409, 'The top-up was partly spent'),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect((await within(dialog).findByRole('alert')).textContent).toContain(
        'The top-up was partly spent',
      );
      expect(screen.getByRole('dialog', { name: 'Balance action' })).toBeTruthy();
      expect(writes(fetchMock)).toHaveLength(1);
      expect(within(dialog).queryByRole('button', { name: 'Refresh' })).toBeNull();
    });

    it('says it is unsure after a 5xx and offers Refresh', async () => {
      const { dialog } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => tmfError(500, 'database is down'),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect((await within(dialog).findByRole('alert')).textContent).toContain(
        'Unsure whether it was saved',
      );
      expect(within(dialog).getByRole('button', { name: 'Refresh' })).toBeTruthy();
    });
  });
});

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
  const menuOf = (row: HTMLElement) => within(row).queryByRole('button', { name: 'Actions ▾' });
  const openMenu = (row: HTMLElement) =>
    fireEvent.click(within(row).getByRole('button', { name: 'Actions ▾' }));
  const items = (row: HTMLElement) =>
    within(row)
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
    expect(within(active).getByRole('button', { name: 'Actions ▾' })).toBe(
      expectUi('ui.subscriber.productActions'),
    );
    openMenu(active);
    expect(items(active)).toEqual(['Suspend…', 'Change offering…', 'End on a date…']);
    expect(within(active).getByRole('menuitem', { name: 'Suspend…' })).toBe(
      expectUi('ui.subscriber.suspend'),
    );
    expect(within(active).getByRole('menuitem', { name: 'End on a date…' })).toBe(
      expectUi('ui.subscriber.endOnDate'),
    );
    const suspended = productRow('Fiber 100');
    openMenu(suspended);
    expect(items(suspended)).toEqual(['Resume…', 'Change offering…', 'End on a date…']);
    expect(within(suspended).getByRole('menuitem', { name: 'Resume…' })).toBe(
      expectUi('ui.subscriber.resume'),
    );
    // a pending status change leaves no second one; an ending product only its Undo
    const pending = productRow('Pending 1');
    openMenu(pending);
    expect(items(pending)).toEqual(['Change offering…', 'End on a date…']);
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
    expect(items(funding)).toEqual(['Change offering…', 'End on a date…']);
    expect(within(funding).getByText(/Waiting for funds: short 17\.75/)).toBeTruthy();
    openMenu(productRow('Fiber 100'));
    expect(items(productRow('Fiber 100'))).toEqual(['Change offering…', 'End on a date…']);
    openMenu(productRow('Manual 1'));
    expect(items(productRow('Manual 1'))).toEqual([
      'Resume…',
      'Change offering…',
      'End on a date…',
    ]);

    fireEvent.click(within(funding).getByRole('button', { name: 'Top up' }));
    const form = await screen.findByRole('dialog', { name: /Top up/ });
    expect((within(form).getByLabelText('Amount') as HTMLInputElement).value).toBe('17.75');
  });

  it('suspends a postpaid product on a future date with the pinned order body', async () => {
    const { fetchMock } = backend({ [`POST ${ORDERS}`]: completed });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Suspend…' }));
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

  it('ends a postpaid product on a date with a delete item and says how it is billed', async () => {
    const { fetchMock } = backend({ [`POST ${ORDERS}`]: completed });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    fireEvent.click(within(row).getByRole('menuitem', { name: 'End on a date…' }));
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
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Resume…' }));
    const dialog = screen.getByRole('dialog');
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect([date.value, date.readOnly]).toEqual(['2026-10-01', false]);
    expect(
      within(dialog).getByText(/The paid interval has ended: resuming starts a new interval/),
    ).toBeTruthy();
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
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Suspend…' }));
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
    fireEvent.click(within(row).getByRole('menuitem', { name: 'End on a date…' }));
    const dialog = screen.getByRole('dialog');
    expect((within(dialog).getByLabelText('Date') as HTMLInputElement).readOnly).toBe(false);
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-10-10' } });
    expect(
      within(dialog).getByText('Unused paid days from 2026-10-10 are credited to the balance.'),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'End on a date' }));
    await screen.findByRole('status');
    expect(JSON.parse(String(posts(fetchMock)[0]?.[1]?.body))).toMatchObject({
      requestedStartDate: '2026-10-10T12:00:00+03:00',
      productOrderItem: [{ action: 'delete', product: { id: P1, '@type': 'ProductRef' } }],
    });
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
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Suspend…' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Date'), { target: { value: '2026-11-03' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Suspend' }));
    expect(await within(dialog).findByText('Rejected: the product is not active')).toBeTruthy();
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
    productSpecification: { name: 'Spec' },
    productOfferingPrice: [{ id: price }],
  });
  const catalog = {
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
    fireEvent.click(within(row).getByRole('button', { name: 'Actions ▾' }));
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Change offering…' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBe(expectUi('ui.subscriber.offeringChange'));
    return { row, dialog };
  }

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
    fireEvent.click(within(pending).getByRole('button', { name: 'Actions ▾' }));
    expect(within(pending).queryByRole('menuitem', { name: 'Change offering…' })).toBeNull();
    expect(within(productRow('Ending 1')).queryByRole('button', { name: 'Actions ▾' })).toBeNull();
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
    fireEvent.click(within(row).getByRole('button', { name: 'Actions ▾' }));
    expect(within(row).queryByRole('menuitem', { name: 'Change offering…' })).toBeNull();
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

  it('fixes the prepaid date at paidThrough, shows the funds preview and follows an accepted order', async () => {
    const { fetchMock } = backend(
      {
        ...catalog,
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: '2026-10-20', needs_topup: null, suspensionCause: null }),
        ]),
        [preview('2026-10-20')]: () =>
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
      },
      PREPAID,
    );
    const { dialog } = await openDialog();
    await within(dialog).findByText('Fiber 100');
    const date = within(dialog).getByLabelText('Date') as HTMLInputElement;
    expect(date.value).toBe('2026-10-20');
    expect(date.readOnly).toBe(true);
    fireEvent.change(date, { target: { value: '2026-10-25' } });
    // a read-only field is not edited by typing; the dialog keeps the renewal date it was given
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
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      expect.stringContaining('accepted'),
    );
  });

  it('uses today for a prepaid product whose paid interval has ended', async () => {
    const { fetchMock } = backend(
      {
        ...catalog,
        ...prepaidRoutes,
        [`GET ${INTERNAL}/products`]: only([
          product(P1, { paidThrough: '2026-09-20', needs_topup: null, suspensionCause: null }),
        ]),
        [preview('2026-10-01')]: () => json(FIRST_CHARGE),
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
      expect.stringContaining('a prepaid offering changes at its renewal on 2026-10-20'),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change offering' }));
    await waitFor(() =>
      expect(within(dialog).getByRole('alert').textContent).toContain('offeringChangeNotAtRenewal'),
    );
    expect(screen.getByRole('dialog')).toBe(dialog);
  });
});
