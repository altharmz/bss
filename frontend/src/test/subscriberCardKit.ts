// Shared fixtures of the SubscriberCard.*.test.tsx files: one screen, several files, so vitest runs them side by side (#319).
import { createElement } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { App } from '../app/App';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, type Handler } from './fetch';
import { accountBody, billsUrl, NO_SETTLEMENT } from './fixtures';

export const A = 'aaaa1111-0000-4000-8000-0000000000a1';
export const C = 'cccc2222-0000-4000-8000-0000000000c2';
export const O1 = 'o0000001-0000-4000-8000-000000000001';
export const O2 = 'o0000002-0000-4000-8000-000000000002';
export const P1 = 'p0000001-0000-4000-8000-000000000001';
export const P2 = 'p0000002-0000-4000-8000-000000000002';
export const P3 = 'p0000003-0000-4000-8000-000000000003';
export const ORDERS = '/tmf-api/productOrderingManagement/v5/productOrder';
export const INTERNAL = `/internal/v1/billingAccount/${A}`;
export const HISTORY = '/tmf-api/prepayBalanceManagement/v4/balanceActionHistory';
export const usd = (value: number) => ({ unit: 'USD', value });

export const ACCOUNT = accountBody(
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

export function product(id: string, over: Record<string, unknown>) {
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

export const PRODUCTS = [
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

export const CHARGES = [
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

export const TRANSACTIONS = [
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

export const FIRST_CHARGE = {
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

export function backend(routes: Record<string, Handler> = {}, account: object = ACCOUNT) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    [`GET /tmf-api/productInventory/v5/product?billingAccount.id=${A}&status=active&offset=0&limit=100`]:
      () => json([], { total: 0 }),
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
    ...Object.fromEntries(
      ['draft', 'acknowledged', 'inProgress'].map((state) => [
        `GET /tmf-api/productOrderingManagement/v5/productOrder?billingAccount.id=${A}&state=${state}&offset=0&limit=50`,
        () => json([], { total: 0 }),
      ]),
    ),
    // asked only of a prepaid account: no command failed technically
    [`GET /internal/v1/prepaid/command?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`]:
      () => json([], { total: 0 }),
    // asked only of a prepaid account: no balance actions yet
    [`GET /tmf-api/prepayBalanceManagement/v4/accumulatedBalance?partyAccount.id=${A}&offset=0&limit=100`]:
      () =>
        json(
          [
            {
              id: 'total-a',
              partyAccount: { id: A },
              totalBalance: { amount: 123.45, units: 'USD' },
            },
          ],
          { total: 1 },
        ),
    [`GET ${HISTORY}?partyAccount.id=${A}&offset=0&limit=100`]: () => json([], { total: 0 }),
    [`GET ${HISTORY}?receiverBucket.id=${BUCKET}&offset=0&limit=100`]: () => json([], { total: 0 }),
    [billsUrl(A)]: () => json([], { total: 0 }),
    [`GET /tmf-api/payment/v4/payment?account.id=${A}&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    [`GET /tmf-api/payment/v4/refund?account.id=${A}&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    [`GET /tmf-api/customerBillManagement/v5/customerBillOnDemand?billingAccount.id=${A}&offset=0&limit=100`]:
      () => json([], { total: 0 }),
    ...routes,
  });
}

export async function open(hash = '') {
  window.history.replaceState(null, '', `/billing-accounts/${A}${hash}`);
  render(createElement(App));
  await screen.findByRole('heading', { name: /Olga Kuznetsova/ });
}

export function selectTab(name: string) {
  const tab = screen.getByRole('tab', { name: new RegExp(`^${name}`) });
  if (tab.getAttribute('aria-selected') !== 'true') fireEvent.click(tab);
}

export function productMenu(row: HTMLElement) {
  const id = within(row).getByRole('button', { name: 'Actions' }).getAttribute('aria-controls');
  const menu = id ? document.getElementById(id) : null;
  if (!menu) throw new Error('Product action menu is not open');
  return menu;
}

export function productAction(row: HTMLElement, name: string) {
  const trigger = within(row).getByRole('button', { name: 'Actions' });
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger);
  return within(productMenu(row)).getByRole('menuitem', { name });
}

export const panel = (title: string) => {
  const tabs: Record<string, string> = {
    'Charge history': 'Charges',
    'Transaction history': 'Transactions',
    'Balance actions': 'Balance actions',
    'Needs attention': 'Commands',
    Bills: 'Bills',
    'Recorded refunds': 'Payments',
    Payments: 'Payments',
    'Active products': 'Overview',
  };
  if (tabs[title]) selectTab(tabs[title]);
  return screen
    .getByText(title, { selector: '.bss-panel-header .title' })
    .closest('section') as HTMLElement;
};

export function productRow(name: string): HTMLElement {
  const row = within(panel('Active products'))
    .getAllByRole('row')
    .find((r) => within(r).queryByText(name));
  if (!row) throw new Error(`no product row ${name} yet`); // lets waitFor retry
  return row.closest('.bss-product-row') as HTMLElement;
}

// Edit subscriber: only what changed is patched, party -> customer -> account. The bodies
// are replayed against the real API by tests/internal/test_ui_requests.py.
export const PARTY_ID = 'dddd4444-0000-4000-8000-000000000004';
export const PARTY_URL = `/tmf-api/partyManagement/v5/individual/${PARTY_ID}`;
export const CUSTOMER_URL = `/tmf-api/customer/v5/customer/${C}`;
export const ACCOUNT_URL = `/tmf-api/accountManagement/v5/billingAccount/${A}`;

export const ACCOUNT_BASE = '/tmf-api/accountManagement/v5';
export const FINANCIAL_URL = '/tmf-api/accountManagement/v5/financialAccount';
export const FIN1 = 'f0000001-0000-4000-8000-000000000001';
export const FIN2 = 'f0000002-0000-4000-8000-000000000002';
export const OWNER_FINANCIAL = [
  { id: FIN1, name: 'Group receivables', state: 'Active', '@type': 'FinancialAccount' },
  { id: FIN2, name: 'Old group', state: 'Closed', '@type': 'FinancialAccount' },
];

export const BILLING_LISTS: Record<string, Handler> = {
  [`GET ${ACCOUNT_BASE}/billingCycleSpecification?offset=0&limit=100`]: () =>
    json([{ id: 'monthly', name: 'Monthly billing' }], { total: 1 }),
  [`GET ${ACCOUNT_BASE}/billFormat?offset=0&limit=100`]: () => json([], { total: 0 }),
  [`GET ${ACCOUNT_BASE}/billPresentationMedia?offset=0&limit=100`]: () => json([], { total: 0 }),
};

export function editBackend(routes: Record<string, Handler> = {}, account: object = ACCOUNT) {
  return backend(
    {
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
      [`GET ${FINANCIAL_URL}?relatedParty.id=${PARTY_ID}&offset=0&limit=100`]: () =>
        json(OWNER_FINANCIAL, { total: 2 }),
      ...BILLING_LISTS,
      ...routes,
    },
    account,
  );
}

export async function openEdit() {
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const dialog = await screen.findByRole('dialog', { name: 'Edit billing account' });
  await within(dialog).findByDisplayValue('Kuznetsova');
  return dialog;
}

export function patches(fetchMock: ReturnType<typeof stubFetch>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([url, init]) => [String(url), String(init?.body)]);
}

export const BUCKET = 'bbbb3333-0000-4000-8000-0000000000b3';
