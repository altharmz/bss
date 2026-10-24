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
    // asked only of a prepaid account: no command failed technically
    [`GET /internal/v1/prepaid/command?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`]:
      () => json([], { total: 0 }),
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
