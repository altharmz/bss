import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { accountBody, billsUrl, NO_SETTLEMENT } from '../test/fixtures';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// Payments and refunds history, including the shared account-level refund entry points.
// Requests are replayed against the real API by tests/test_ui_requests.py.

const PAYMENT = '/tmf-api/payment/v4/payment';
const REFUND = '/tmf-api/payment/v4/refund';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const A2 = 'a0000002-0000-4000-8000-000000000002';
const C = 'cccc2222-0000-4000-8000-0000000000c2';
const P1 = 'e0000001-0000-4000-8000-000000000001';
const P2 = 'e0000002-0000-4000-8000-000000000002';
const R1 = 'f0000001-0000-4000-8000-000000000001';
const B1 = 'b1000001-0000-4000-8000-000000000001';
const usd = (value: number) => ({ unit: 'USD', value });
const ref = (id: string, name: string) => ({ id, name, '@referredType': 'BillingAccount' });

function payment(
  id: string,
  account: ReturnType<typeof ref>,
  date: string,
  total: number,
  over = {},
) {
  return {
    id,
    '@type': 'Payment',
    paymentDate: date,
    status: 'done',
    account,
    amount: usd(total),
    taxAmount: usd(0),
    totalAmount: usd(total),
    paymentMethod: { name: 'bankTransfer' },
    ...over,
  };
}

const PAY1 = payment(P1, ref(A1, 'Ivan Ivanov'), '2026-09-10T09:00:00Z', 100, {
  correlatorId: 'INV-77',
  paymentItem: [{ '@type': 'PaymentItem', item: { id: B1 }, amount: usd(60) }],
});
const PAY2 = payment(P2, ref(A2, 'Olga Kuznetsova'), '2026-10-01T09:00:00Z', 25.5, {
  paymentMethod: { name: 'cash' },
});
const REFUND1 = {
  id: R1,
  '@type': 'Refund',
  refundDate: '2026-09-20T09:00:00Z',
  status: 'done',
  account: { id: A1 },
  totalAmount: usd(10),
  paymentMethod: { name: 'bankTransfer' },
  description: 'Returned deposit',
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

function open(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

const PAGE = 'offset=0&limit=100';

describe('payments screen', () => {
  it('lists payments newest first with links to the cards, and the refunds tab', async () => {
    const { calls } = backend({
      [`GET ${PAYMENT}?${PAGE}`]: () => json([PAY1, PAY2], { total: 2 }),
      [`GET ${REFUND}?${PAGE}`]: () => json([REFUND1], { total: 1 }),
    });
    open('/payments');
    await screen.findByText('Ivan Ivanov');
    expectUi('ui.payments');
    const rows = screen.getAllByRole('row');
    expect(rows.findIndex((r) => within(r).queryByText('Olga Kuznetsova'))).toBeLessThan(
      rows.findIndex((r) => within(r).queryByText('Ivan Ivanov')),
    );
    const row = rows.find((r) => within(r).queryByText('Ivan Ivanov')) as HTMLElement;
    expect(within(row).getByRole('link', { name: 'Ivan Ivanov' }).getAttribute('href')).toBe(
      `/subscribers/${A1}`,
    );
    expect(within(row).getByText('100.00 USD')).toBeTruthy();
    expect(within(row).getByText('bankTransfer')).toBeTruthy();
    expect(within(row).getByText('INV-77')).toBeTruthy();
    expect(screen.getByText('Showing 2')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Refunds' }));
    await screen.findByText('Returned deposit');
    const refund = screen
      .getAllByRole('row')
      .find((r) => within(r).queryByText('Returned deposit')) as HTMLElement;
    expect(within(refund).getByRole('link', { name: 'BA-A0000001' }).getAttribute('href')).toBe(
      `/subscribers/${A1}`,
    );
    expect(within(refund).getByText('10.00 USD')).toBeTruthy();
    expect(calls.filter((c) => c.startsWith(`GET ${PAYMENT}?`))).toHaveLength(1);
  });

  it('filters by status, by the account chip and by a client-side date range', async () => {
    const { calls } = backend({
      [`GET ${PAYMENT}?${PAGE}`]: () => json([PAY1, PAY2], { total: 2 }),
      [`GET ${REFUND}?${PAGE}`]: () => json([REFUND1], { total: 1 }),
      [`GET ${PAYMENT}?account.id=${A1}&${PAGE}`]: () => json([PAY1], { total: 1 }),
      [`GET ${REFUND}?account.id=${A1}&${PAGE}`]: () => json([REFUND1], { total: 1 }),
      [`GET ${PAYMENT}?account.id=${A1}&status=done&${PAGE}`]: () => json([PAY1], { total: 1 }),
      [`GET ${REFUND}?account.id=${A1}&status=done&${PAGE}`]: () => json([REFUND1], { total: 1 }),
      [`GET ${PAYMENT}?status=done&${PAGE}`]: () => json([PAY1, PAY2], { total: 2 }),
      [`GET ${REFUND}?status=done&${PAGE}`]: () => json([REFUND1], { total: 1 }),
    });
    open(`/payments?account=${A1}`);
    const chip = await screen.findByText(/Account/, {
      selector: '[data-ui="ui.payments.accountChip"]',
    });
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    expect(screen.queryByText('Olga Kuznetsova')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'done' }));
    await waitFor(() =>
      expect(calls).toContain(`GET ${PAYMENT}?account.id=${A1}&status=done&${PAGE}`),
    );
    // dates are applied to the loaded rows: no new request
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    const before = calls.length;
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-15' } });
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Ivan Ivanov' })).toBeNull());
    expect(calls.length).toBe(before);
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-01' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-09-30' } });
    await screen.findByRole('link', { name: 'Ivan Ivanov' });
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));
    await screen.findByText('Showing 1 of 2'); // PAY2 (Oct 1) is outside the range
    expect(window.location.search).toBe('');
    expect(screen.queryByText('Olga Kuznetsova')).toBeNull();
  });

  it('opens a payment from GET /payment/{id} with what it paid', async () => {
    backend({
      [`GET ${PAYMENT}?${PAGE}`]: () => json([PAY1], { total: 1 }),
      [`GET ${REFUND}?${PAGE}`]: () => json([], { total: 0 }),
      [`GET ${PAYMENT}/${P1}`]: () => json(PAY1),
    });
    open('/payments');
    fireEvent.click(await screen.findByText('INV-77'));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Bill B1000001: 60.00 USD');
    expectUi('ui.payments.detail');
    expect(within(dialog).getByText('Payment E0000001')).toBeTruthy();
    expect(within(dialog).getByRole('link', { name: 'Ivan Ivanov' })).toBeTruthy();
  });

  it('shows the error with Retry and the empty state', async () => {
    let fail = true;
    backend({
      [`GET ${PAYMENT}?${PAGE}`]: () =>
        fail ? tmfError(503, 'Service Unavailable') : json([], { total: 0 }),
      [`GET ${REFUND}?${PAGE}`]: () => json([], { total: 0 }),
    });
    open('/payments');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load payments')).toBeTruthy();
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await screen.findByText('No payments');
  });
});

describe('payments panel on the subscriber card', () => {
  it('lists the account payments and opens one', async () => {
    const INTERNAL = `/internal/v1/billingAccount/${A1}`;
    const { calls } = backend({
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: () =>
        json(accountBody(A1, { id: C, name: 'Ivan Ivanov' })),
      [`GET /tmf-api/customer/v5/customer/${C}`]: () =>
        json({ id: C, name: 'Ivan Ivanov', status: 'Active', '@type': 'Customer' }),
      ...Object.fromEntries(
        ['draft', 'acknowledged', 'inProgress'].map((state) => [
          `GET /tmf-api/productOrderingManagement/v5/productOrder?billingAccount.id=${A1}&state=${state}&offset=0&limit=50`,
          () => json([], { total: 0 }),
        ]),
      ),
      [`GET ${INTERNAL}/products`]: () => json([], { total: 0 }),
      [`GET ${INTERNAL}/charges?limit=20`]: () => json([], { total: 0 }),
      [`GET ${INTERNAL}/transactions?limit=20`]: () => json([], { total: 0 }),
      [`GET ${INTERNAL}/settlement`]: () => json(NO_SETTLEMENT),
      [billsUrl(A1)]: () => json([], { total: 0 }),
      [`GET ${REFUND}?account.id=${A1}&${PAGE}`]: () => json([], { total: 0 }),
      [`GET /tmf-api/customerBillManagement/v5/customerBillOnDemand?billingAccount.id=${A1}&${PAGE}`]:
        () => json([], { total: 0 }),
      [`GET ${PAYMENT}?account.id=${A1}&${PAGE}`]: () => json([PAY1], { total: 1 }),
      [`GET ${PAYMENT}/${P1}`]: () => json(PAY1),
    });
    open(`/subscribers/${A1}`);
    await screen.findByRole('heading', { name: /Ivan Ivanov/ });
    fireEvent.click(screen.getByRole('tab', { name: /^Payments/ }));
    const card = expectUi('ui.subscriber.payments');
    fireEvent.click(await within(card).findByText('INV-77'));
    expect(within(card).getByText('2026-09-10 12:00')).toBeTruthy();
    await screen.findByRole('dialog');
    expectUi('ui.payments.detail');
    expect(calls).toContain(`GET ${PAYMENT}/${P1}`);
  });
});

describe('account-level refunds from payments', () => {
  it.each(['subscriber header', 'subscriber payment', 'global payment'])(
    'reuses the mandatory-description refund and exact account body from the %s',
    async (entry) => {
      const internal = `/internal/v1/billingAccount/${A1}`;
      const { fetchMock, calls } = backend({
        [`GET /tmf-api/accountManagement/v5/billingAccount/${A1}`]: () =>
          json(
            accountBody(
              A1,
              { id: C, name: 'Ivan Ivanov' },
              {
                accountBalance: [{ amount: usd(100.25) }],
              },
            ),
          ),
        [`GET /tmf-api/customer/v5/customer/${C}`]: () =>
          json({ id: C, name: 'Ivan Ivanov', status: 'Active', '@type': 'Customer' }),
        ...Object.fromEntries(
          ['draft', 'acknowledged', 'inProgress'].map((state) => [
            `GET /tmf-api/productOrderingManagement/v5/productOrder?billingAccount.id=${A1}&state=${state}&offset=0&limit=50`,
            () => json([], { total: 0 }),
          ]),
        ),
        [`GET ${internal}/products`]: () => json([], { total: 0 }),
        [`GET ${internal}/charges?limit=20`]: () => json([], { total: 0 }),
        [`GET ${internal}/transactions?limit=20`]: () => json([], { total: 0 }),
        [`GET ${internal}/settlement`]: () =>
          json({ ...NO_SETTLEMENT, unallocatedCredit: usd(100.25) }),
        [billsUrl(A1)]: () => json([], { total: 0 }),
        [`GET ${REFUND}?account.id=${A1}&${PAGE}`]: () => json([], { total: 0 }),
        [`GET /tmf-api/customerBillManagement/v5/customerBillOnDemand?billingAccount.id=${A1}&${PAGE}`]:
          () => json([], { total: 0 }),
        [`GET ${PAYMENT}?account.id=${A1}&${PAGE}`]: () => json([PAY1], { total: 1 }),
        [`GET ${PAYMENT}?${PAGE}`]: () => json([PAY1], { total: 1 }),
        [`GET ${REFUND}?${PAGE}`]: () => json([], { total: 0 }),
        [`GET ${PAYMENT}/${P1}`]: () => json(PAY1),
        [`POST ${REFUND}`]: () => json({ ...REFUND1, totalAmount: usd(25.1) }),
      });

      if (entry === 'global payment') {
        open('/payments');
      } else {
        open(`/subscribers/${A1}`);
        await screen.findByRole('heading', { name: /Ivan Ivanov/ });
        fireEvent.click(screen.getByRole('tab', { name: /^Payments/ }));
      }

      if (entry === 'subscriber header') {
        const action = expectUi('ui.subscriber.payments.recordRefund');
        await waitFor(() => expect(action).toHaveProperty('disabled', false));
        expect(
          within(expectUi('ui.subscriber.payments')).getByRole('button', {
            name: 'Record refund',
          }),
        ).toBe(action);
        fireEvent.click(action);
      } else {
        fireEvent.click(await screen.findByText('INV-77'));
        const detail = await screen.findByRole('dialog', { name: 'Payment E0000001' });
        const action = await within(detail).findByRole('button', { name: 'Record refund' });
        expectUi('ui.payments.detail.recordRefund');
        fireEvent.click(action);
      }

      const amount = await screen.findByLabelText('Refund amount');
      const dialog = screen.getByRole('dialog', { name: 'Record refund' });
      fireEvent.change(amount, { target: { value: '25.10' } });
      const record = within(dialog).getByRole('button', { name: 'Record refund' });
      expect(within(dialog).getByText('Enter a refund description.')).toBeTruthy();
      expect(record).toHaveProperty('disabled', true);
      fireEvent.change(within(dialog).getByLabelText('Refund description'), {
        target: { value: '   ' },
      });
      expect(record).toHaveProperty('disabled', true);
      expect(calls).not.toContain(`POST ${REFUND}`);
      fireEvent.change(within(dialog).getByLabelText('Refund description'), {
        target: { value: ' Returned deposit ' },
      });
      expect(record).toHaveProperty('disabled', false);
      fireEvent.click(record);
      await screen.findByText('Refund recorded');
      const writes = fetchMock.mock.calls.filter((call) => call[1]?.method === 'POST');
      expect(writes).toHaveLength(1);
      expect(writes[0]?.[0]).toBe(REFUND);
      expect(writes[0]?.[1]?.body).toBe(
        `{"@type":"Refund","account":{"id":"${A1}","@referredType":"BillingAccount"},"totalAmount":{"unit":"USD","value":25.10},"paymentMethod":{"name":"bankTransfer"},"description":"Returned deposit"}`,
      );
      const refreshed =
        entry === 'global payment'
          ? `GET ${REFUND}?${PAGE}`
          : `GET ${REFUND}?account.id=${A1}&${PAGE}`;
      await waitFor(() => expect(calls.filter((call) => call === refreshed)).toHaveLength(2));
    },
  );
});
