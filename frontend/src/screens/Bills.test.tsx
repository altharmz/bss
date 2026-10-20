import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody, billBody, billsUrl, NO_SETTLEMENT } from '../test/fixtures';

// The bill lifecycle on the subscriber card (docs/bill-lifecycle-design.md 6.7). The
// request bodies asserted here are replayed against the real API by
// tests/test_ui_requests.py - change both together.

const A = 'aaaa1111-0000-4000-8000-0000000000a1';
const C = 'cccc2222-0000-4000-8000-0000000000c2';
const B1 = 'b1000001-0000-4000-8000-000000000001';
const B2 = 'b2000002-0000-4000-8000-000000000002';
const B3 = 'b3000003-0000-4000-8000-000000000003';
const INTERNAL = `/internal/v1/billingAccount/${A}`;
const BILL = '/tmf-api/customerBillManagement/v5/customerBill';
const PAYMENT = '/tmf-api/payment/v4/payment';
const rub = (value: number) => ({ unit: 'RUB', value });

// business date 2026-10-01: B1 (due Aug 31) is overdue, B2 (due Oct 1) is not yet
const BILLS = [
  billBody(B1, '2026-08-01', 300, 300, 'sent'),
  billBody(B2, '2026-09-01', 300, 100, 'partiallyPaid'),
  billBody(B3, '2026-10-01', -50, 0, 'settled'),
];

const SETTLEMENT = {
  outstanding: rub(400),
  unallocatedCredit: rub(25.5),
  overdue: { count: 1, amount: rub(300) },
  onHold: { count: 0, amount: rub(0) },
  paymentTermDays: 30,
};

function detail(over: Record<string, unknown> = {}) {
  return {
    id: B2,
    href: `${BILL}/${B2}`,
    billNo: 'BL-B2000002',
    billingAccount: { id: A, href: `/tmf-api/accountManagement/v5/billingAccount/${A}` },
    billDate: '2026-09-01',
    period: { start: '2026-09-01', end: '2026-10-01' },
    paymentDueDate: '2026-10-01',
    amountDue: rub(300),
    remainingAmount: rub(100),
    state: 'partiallyPaid',
    category: 'normal',
    overdue: false,
    allowedStates: ['onHold'],
    charges: [
      {
        id: 'r1',
        href: '/r1',
        kind: 'charge',
        period: { start: '2026-09-01', end: '2026-10-01' },
        amount: rub(300),
        product: { id: 'p1', href: '/p1' },
        productOffering: { id: 'o1', name: 'Mobile L' },
      },
    ],
    sources: [
      {
        kind: 'payment',
        id: 'pay1',
        href: `${PAYMENT}/pay1`,
        label: 'bankTransfer',
        amount: rub(200),
        allocation: 'automatic',
        allocatedAt: '2026-09-05T09:00:00Z',
      },
    ],
    history: [
      {
        fromState: null,
        toState: 'new',
        changedAt: '2026-08-31T21:30:00Z',
        cause: 'run',
        operator: null,
      },
      {
        fromState: 'new',
        toState: 'validated',
        changedAt: '2026-08-31T21:30:00Z',
        cause: 'run',
        operator: null,
      },
      {
        fromState: 'validated',
        toState: 'sent',
        changedAt: '2026-08-31T21:30:00Z',
        cause: 'run',
        operator: null,
      },
      {
        fromState: 'sent',
        toState: 'partiallyPaid',
        changedAt: '2026-09-05T09:00:00Z',
        cause: 'payment',
        operator: null,
      },
    ],
    ...over,
  };
}

function backend(routes: Record<string, Handler> = {}) {
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
    [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () =>
      json(accountBody(A, { id: C, name: 'Olga Kuznetsova' })),
    [`GET /tmf-api/customer/v5/customer/${C}`]: () =>
      json({ id: C, name: 'Olga Kuznetsova', status: 'Active', '@type': 'Customer' }),
    [`GET ${INTERNAL}/products`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/charges?limit=20`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/transactions?limit=20`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/settlement`]: () => json(SETTLEMENT),
    [billsUrl(A)]: () => json(BILLS, { total: BILLS.length }),
    [`GET /tmf-api/payment/v4/refund?account.id=${A}&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    [`GET /tmf-api/customerBillManagement/v5/customerBillOnDemand?billingAccount.id=${A}&offset=0&limit=100`]:
      () => json([], { total: 0 }),
    [`GET /internal/v1/customerBill/${B2}`]: () => json(detail()),
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

function billRow(no: string): HTMLElement {
  const row = within(panel('Bills'))
    .getAllByRole('row')
    .find((r) => within(r).queryByText(no));
  if (!row) throw new Error(`no bill row ${no} yet`);
  return row;
}

function sentBody(fetchMock: ReturnType<typeof stubFetch>['fetchMock'], method: string) {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === method);
  return { body: String(call?.[1]?.body), headers: call?.[1]?.headers, url: String(call?.[0]) };
}

describe('bills on the subscriber card', () => {
  it('lists the bills newest first, with state, overdue and credit notes', async () => {
    backend();
    await open();
    await waitFor(() => billRow('BL-B1000001'));
    expectUi('ui.subscriber.bills');
    const rows = within(panel('Bills')).getAllByRole('row').slice(1); // after the head
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual([
      'BL-B3000003',
      'BL-B2000002',
      'BL-B1000001',
    ]);
    const overdue = billRow('BL-B1000001');
    expect(within(overdue).getByText('2026-08-31')).toBeTruthy();
    expect(within(overdue).getByText('overdue')).toBeTruthy();
    expect(within(billRow('BL-B2000002')).queryByText('overdue')).toBeNull(); // due today
    expect(within(billRow('BL-B2000002')).getByText('partiallyPaid')).toBeTruthy();
    expect(within(billRow('BL-B3000003')).getByText('credit note')).toBeTruthy();
    expect(within(billRow('BL-B3000003')).getByText('−50.00')).toBeTruthy();
  });

  it('shows what is due on bills and the unused credit', async () => {
    backend();
    await open();
    const kpis = panel('Billing account');
    expect(await within(kpis).findByText('400.00')).toBeTruthy();
    expect(within(kpis).getByText('1 overdue · 300.00')).toBeTruthy();
    expect(within(kpis).getByText('25.50')).toBeTruthy();
  });

  it('opens a bill with its charges, payments and history, and puts it on hold', async () => {
    let held = false;
    const { fetchMock } = backend({
      [`PATCH ${BILL}/${B2}`]: () => {
        held = true;
        return json(billBody(B2, '2026-09-01', 300, 100, 'onHold'));
      },
    });
    await open();
    fireEvent.click(await waitFor(() => billRow('BL-B2000002')));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B2000002' });
    expectUi('ui.subscriber.billDetail');
    expect(within(dialog).getByText('Mobile L')).toBeTruthy();
    expect(within(dialog).getByText('Payment · bankTransfer')).toBeTruthy();
    expect(within(dialog).getByText('automatic · 2026-09-05 12:00')).toBeTruthy(); // MSK
    expect(within(dialog).getByText('issued as new')).toBeTruthy();
    expect(within(dialog).getByText('sent → partiallyPaid')).toBeTruthy();

    const hold = within(dialog).getByRole('button', { name: 'Put on hold' });
    expect(hold).toBe(expectUi('ui.subscriber.billHold'));
    fireEvent.click(hold);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Bill BL-B2000002 put on hold',
    );
    const patch = sentBody(fetchMock, 'PATCH');
    expect(patch.url).toBe(`${BILL}/${B2}`);
    expect(JSON.parse(patch.body)).toEqual({ state: 'onHold' });
    expect(patch.headers).toMatchObject({ 'Content-Type': 'application/merge-patch+json' });
    // the dialog closes and the card reloads its bills, as after every change
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes('customerBill?')),
      ).toHaveLength(2),
    );
    expect(held).toBe(true);
  });

  it('releases a held bill to the state the server allows', async () => {
    const { fetchMock } = backend({
      [`GET /internal/v1/customerBill/${B2}`]: () =>
        json(detail({ state: 'onHold', allowedStates: ['partiallyPaid'] })),
      [`PATCH ${BILL}/${B2}`]: () => json(billBody(B2, '2026-09-01', 300, 100, 'partiallyPaid')),
    });
    await open();
    fireEvent.click(await waitFor(() => billRow('BL-B2000002')));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B2000002' });
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Release' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Bill BL-B2000002 released',
    );
    expect(JSON.parse(sentBody(fetchMock, 'PATCH').body)).toEqual({ state: 'partiallyPaid' });
  });

  it('shows a refused hold in the dialog', async () => {
    backend({ [`PATCH ${BILL}/${B2}`]: () => tmfError(409, 'Conflict') });
    await open();
    fireEvent.click(await waitFor(() => billRow('BL-B2000002')));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B2000002' });
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Put on hold' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('offers no action on a settled bill', async () => {
    backend({
      [`GET /internal/v1/customerBill/${B2}`]: () =>
        json(detail({ state: 'settled', remainingAmount: rub(0), allowedStates: [] })),
    });
    await open();
    fireEvent.click(await waitFor(() => billRow('BL-B2000002')));
    const dialog = await screen.findByRole('dialog', { name: 'Bill BL-B2000002' });
    await within(dialog).findByText('Mobile L');
    expect(within(dialog).queryByRole('button', { name: 'Put on hold' })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Release' })).toBeNull();
  });
});

describe('record payment', () => {
  it('records a payment for the oldest bills first, with the amount as an exact token', async () => {
    const { fetchMock } = backend({
      [`POST ${PAYMENT}`]: () =>
        json({ id: 'pay9', paymentItem: [{}, {}], '@type': 'Payment' }, { status: 201 }),
    });
    await open();
    await waitFor(() => billRow('BL-B1000001'));
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const dialog = await screen.findByRole('dialog', { name: 'Record payment' });
    expectUi('ui.subscriber.recordPayment');
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '1,500.5' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record payment' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Payment of 1,500.50 RUB recorded · applied to 2 bills',
    );
    const post = sentBody(fetchMock, 'POST');
    expect(post.url).toBe(PAYMENT);
    expect(post.body).toBe(
      `{"@type":"Payment","account":{"id":"${A}","@referredType":"BillingAccount"},` +
        '"totalAmount":{"unit":"RUB","value":1500.50},"paymentMethod":{"name":"bankTransfer"}}',
    );
    // the card reloads its bills
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes('customerBill?')),
      ).toHaveLength(2),
    );
  });

  it('applies a payment to chosen bills, prefilled oldest first', async () => {
    const { fetchMock } = backend({
      [`POST ${PAYMENT}`]: () =>
        json({ id: 'pay9', paymentItem: [{}], '@type': 'Payment' }, { status: 201 }),
    });
    await open();
    await waitFor(() => billRow('BL-B1000001'));
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const dialog = await screen.findByRole('dialog', { name: 'Record payment' });
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '350' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'cash' }));
    fireEvent.click(within(dialog).getByLabelText(/Apply to specific bills/));
    // oldest first: B1 takes 300, B2 the 50 left; the credit note is not offered
    const b1 = within(dialog).getByLabelText('Amount for BL-B1000001') as HTMLInputElement;
    const b2 = within(dialog).getByLabelText('Amount for BL-B2000002') as HTMLInputElement;
    expect([b1.value, b2.value]).toEqual(['300.00', '50.00']);
    expect(within(dialog).queryByLabelText('Amount for BL-B3000003')).toBeNull();

    // more than a bill's remaining amount is refused before anything is sent
    fireEvent.change(b2, { target: { value: '100.01' } });
    expect(
      within(dialog).getByText('BL-B2000002: enter at most its remaining amount.'),
    ).toBeTruthy();
    const submit = within(dialog).getByRole('button', {
      name: 'Record payment',
    }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(b1, { target: { value: '' } }); // only B2
    fireEvent.change(b2, { target: { value: '100' } });
    fireEvent.click(submit);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Payment of 350.00 RUB recorded · applied to 1 bill',
    );
    expect(sentBody(fetchMock, 'POST').body).toBe(
      `{"@type":"Payment","account":{"id":"${A}","@referredType":"BillingAccount"},` +
        '"totalAmount":{"unit":"RUB","value":350.00},"paymentMethod":{"name":"cash"},' +
        `"paymentItem":[{"@type":"PaymentItem","item":{"id":"${B2}","@referredType":"CustomerBill"},` +
        '"amount":{"unit":"RUB","value":100.00}}]}',
    );
  });

  it('refuses items above the payment and keeps a refused payment open with the reason', async () => {
    backend({ [`POST ${PAYMENT}`]: () => tmfError(409, 'Conflict') });
    await open();
    await waitFor(() => billRow('BL-B1000001'));
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const dialog = await screen.findByRole('dialog', { name: 'Record payment' });
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '100' } });
    fireEvent.click(within(dialog).getByLabelText(/Apply to specific bills/));
    fireEvent.change(within(dialog).getByLabelText('Amount for BL-B2000002'), {
      target: { value: '50' },
    });
    expect(within(dialog).getByText('The bills add up to more than the payment.')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Amount for BL-B1000001'), {
      target: { value: '50' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record payment' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Record payment' })).toBeTruthy();
  });

  it('keeps a payment as credit when there is no open bill', async () => {
    backend({
      [billsUrl(A)]: () => json([], { total: 0 }),
      [`GET ${INTERNAL}/settlement`]: () => json(NO_SETTLEMENT),
      [`POST ${PAYMENT}`]: () => json({ id: 'pay9', '@type': 'Payment' }, { status: 201 }),
    });
    await open();
    await within(panel('Bills')).findByText('No bills');
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const dialog = await screen.findByRole('dialog', { name: 'Record payment' });
    expect(within(dialog).getByText(/No open bill/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '10' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Other' }));
    fireEvent.change(within(dialog).getByLabelText('Payment method name'), {
      target: { value: 'Faster Payments' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record payment' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Payment of 10.00 RUB recorded · kept as credit',
    );
  });
});
