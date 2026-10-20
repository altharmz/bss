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
