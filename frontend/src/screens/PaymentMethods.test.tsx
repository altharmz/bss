import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { toAccount } from '../api/bss';
import { expectUi } from '../test/expectUi';
import { json, stubFetch, tmfError } from '../test/fetch';
import { accountBody } from '../test/fixtures';
import { ToastProvider } from '../ui/kit';
import { PaymentMethodsPanel } from './PaymentMethods';

// Saved cards, Top up by card and auto top-up (issue 185b). The write bodies are replayed
// against the real API by tests/internal/test_ui_requests.py.

const A = 'a0000001-0000-4000-8000-000000000001';
const CARD1 = 'c0000001-0000-4000-8000-000000000001';
const CARD2 = 'c0000002-0000-4000-8000-000000000002';
const CMD = 'd0000001-0000-4000-8000-000000000001';
const METHODS = '/tmf-api/paymentMethod/v4/paymentMethod';
const LIST = `GET ${METHODS}?account.id=${A}&offset=0&limit=100`;
const TOPUPS = `/internal/v1/billingAccount/${A}/cardTopup`;
const AUTO = `/internal/v1/billingAccount/${A}/autoTopup`;

const make = (ratingType: 'prepaid' | 'postpaid') =>
  toAccount({
    ...accountBody(A, { id: 'customer', name: 'Ada' }),
    ratingType,
  } as Parameters<typeof toAccount>[0]);

const card = (id: string, name: string, isPreferred: boolean) => ({
  id,
  '@type': 'tokenizedCard',
  name,
  brand: 'visa',
  lastFourDigits: '4242',
  expirationDate: '2030-01-01T00:00:00Z',
  isPreferred,
});

const RULE_OFF = {
  enabled: false,
  threshold: null,
  amount: null,
  defaultPaymentMethod: null,
  changedAt: null,
};

const charge = (state: string, extra = {}) => ({
  id: CMD,
  state,
  amount: { unit: 'USD', value: 25 },
  declineReason: null,
  lastError: null,
  ...extra,
});

function show(account = make('prepaid')) {
  render(
    <ToastProvider>
      <PaymentMethodsPanel account={account} version={0} onChanged={() => {}} />
    </ToastProvider>,
  );
}

const bodyOf = (fetchMock: ReturnType<typeof stubFetch>['fetchMock'], method: string) =>
  fetchMock.mock.calls.find(([, init]) => init?.method === method)?.[1]?.body;

describe('Payment methods', () => {
  it('lists cards, makes one default and removes one', async () => {
    const { fetchMock, calls } = stubFetch({
      [LIST]: () => json([card(CARD1, 'Visa A', true), card(CARD2, 'Visa B', false)], { total: 2 }),
      [`GET ${AUTO}`]: () => json(RULE_OFF),
      [`PATCH ${METHODS}/${CARD2}`]: () => json(card(CARD2, 'Visa B', true)),
      [`DELETE ${METHODS}/${CARD1}`]: () => new Response(null, { status: 204 }),
    });
    show();
    await screen.findByText(/Visa A · visa ending 4242/);
    expectUi('ui.subscriber.paymentMethods');
    expect(expectUi('ui.subscriber.paymentMethods.card').textContent).toContain('Visa A');
    fireEvent.click(screen.getByRole('button', { name: 'Make default' }));
    await waitFor(() => expect(bodyOf(fetchMock, 'PATCH')).toBe('{"isPreferred":true}'));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Visa A' }));
    await waitFor(() => expect(calls).toContain(`DELETE ${METHODS}/${CARD1}`));
  });

  it('shows the 409 of a card with a charge in progress', async () => {
    stubFetch({
      [LIST]: () => json([card(CARD1, 'Visa A', true)], { total: 1 }),
      [`GET ${AUTO}`]: () => json(RULE_OFF),
      [`DELETE ${METHODS}/${CARD1}`]: () => tmfError(409, 'A charge with this card is in progress'),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Visa A' }));
    await screen.findByText(/in progress/);
  });

  it('adds a card with a stub token and never a card number', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([], { total: 0 }),
      [`GET ${AUTO}`]: () => json(RULE_OFF),
      [`POST ${METHODS}`]: () => json(card(CARD1, 'Work', true), { status: 201 }),
    });
    show();
    await screen.findByText(/No saved cards/);
    fireEvent.click(screen.getByRole('button', { name: 'Add card' }));
    expectUi('ui.subscriber.paymentMethods.add');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Work' } });
    fireEvent.change(screen.getByLabelText('Last four digits'), { target: { value: '4242' } });
    fireEvent.change(screen.getByLabelText('Expiry month'), { target: { value: '12' } });
    fireEvent.change(screen.getByLabelText('Expiry year'), { target: { value: '2030' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save card' }));
    await waitFor(() => expect(bodyOf(fetchMock, 'POST')).toBeTruthy());
    const sent = JSON.parse(String(bodyOf(fetchMock, 'POST'))) as Record<string, unknown>;
    expect(sent).toEqual({
      '@type': 'tokenizedCard',
      name: 'Work',
      account: [{ id: A, '@referredType': 'BillingAccount' }],
      brand: 'visa',
      lastFourDigits: '4242',
      expirationDate: '2031-01-01T00:00:00Z',
      token: expect.stringMatching(/^tok_stub_/),
    });
  });

  it('tops up by card with the default preselected and one key per submission', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([card(CARD1, 'Visa A', false), card(CARD2, 'Visa B', true)], { total: 2 }),
      [`GET ${AUTO}`]: () => json(RULE_OFF),
      [`GET ${TOPUPS}?limit=10`]: () =>
        json([charge('exhausted', { lastError: 'gateway timeout' })]),
      [`POST ${TOPUPS}`]: () => json(charge('pending'), { status: 202 }),
      [`POST /internal/v1/payment/command/${CMD}/retry`]: () => json(charge('pending')),
    });
    show();
    await screen.findByText(/Visa B/);
    fireEvent.click(screen.getByRole('button', { name: 'Top up by card' }));
    expectUi('ui.subscriber.paymentMethods.topup');
    expect((screen.getByLabelText('Card') as HTMLSelectElement).value).toBe(CARD2);
    await screen.findByText(/Last error: gateway timeout/);
    expect(expectUi('ui.subscriber.paymentMethods.charges').textContent).toContain(
      'gateway timeout',
    );
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Charge card' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([url, init]) => init?.method === 'POST' && String(url) === TOPUPS,
        ),
      ).toHaveLength(1),
    );
    const sent = String(
      fetchMock.mock.calls.find(
        ([url, init]) => init?.method === 'POST' && String(url) === TOPUPS,
      )?.[1]?.body,
    );
    expect(sent).toMatch(
      new RegExp(
        `^\\{"amount":\\{"unit":"USD","value":25\\.00\\},"paymentMethod":\\{"id":"${CARD2}"\\},"idempotencyKey":"[^"]+"\\}$`,
      ),
    );
    const dialog = screen.getByRole('dialog');
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).endsWith(`/payment/command/${CMD}/retry`)),
      ).toBe(true),
    );
  });

  it('saves the auto top-up rule and refuses an amount below the threshold', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([], { total: 0 }),
      [`GET ${AUTO}`]: () => json(RULE_OFF),
      [`PATCH ${AUTO}`]: () => json({ ...RULE_OFF, enabled: true }),
    });
    show();
    const edit = await screen.findByRole('button', { name: 'Edit' });
    expect(edit).toBe(expectUi('ui.subscriber.autoTopup.edit'));
    expect(expectUi('ui.subscriber.autoTopup').textContent).toContain('Off');
    fireEvent.click(edit);
    expectUi('ui.subscriber.autoTopup.dialog');
    fireEvent.click(screen.getByLabelText('Top up automatically'));
    expect(screen.getByText(/No default card is saved/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Threshold'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Top-up amount'), { target: { value: '5' } });
    expect(screen.getByText(/at least the threshold/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText('Top-up amount'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, 'PATCH')).toBe(
        '{"enabled":true,"threshold":{"unit":"USD","value":10.00},"amount":{"unit":"USD","value":20.00}}',
      ),
    );
  });

  it('has no top-up or auto top-up for a postpaid account', async () => {
    stubFetch({ [LIST]: () => json([], { total: 0 }) });
    show(make('postpaid'));
    await screen.findByText(/No saved cards/);
    expect(screen.queryByRole('button', { name: 'Top up by card' })).toBeNull();
    expect(screen.queryByText('Auto top-up')).toBeNull();
  });
});
