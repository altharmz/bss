import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { toAccount } from '../api/bss';
import { json, pending, stubFetch } from '../test/fetch';
import { accountBody } from '../test/fixtures';
import { ToastProvider } from '../ui/kit';
import { PaymentMethodsPanel } from './PaymentMethods';

// The refused, uncertain and cancelled paths of saved cards, card top-up and auto top-up
// (#487): what the operator sees, what is sent again and what is never sent.

const A = 'a0000001-0000-4000-8000-000000000001';
const CARD1 = 'c0000001-0000-4000-8000-000000000001';
const CARD2 = 'c0000002-0000-4000-8000-000000000002';
const CMD = 'd0000001-0000-4000-8000-000000000001';
const METHODS = '/tmf-api/paymentMethod/v4/paymentMethod';
const LIST = `GET ${METHODS}?account.id=${A}&offset=0&limit=100`;
const TOPUPS = `/internal/v1/billingAccount/${A}/cardTopup`;
const AUTO = `/internal/v1/billingAccount/${A}/autoTopup`;

const prepaid = toAccount({
  ...accountBody(A, { id: 'customer', name: 'Ada' }),
  ratingType: 'prepaid',
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

const RULE = {
  enabled: true,
  threshold: { unit: 'USD', value: 5 },
  amount: { unit: 'USD', value: 20 },
  defaultPaymentMethod: { id: CARD2 },
  changedAt: '2026-09-30T10:00:00Z',
};

const charge = (state: string) => ({
  id: CMD,
  state,
  amount: { unit: 'USD', value: 25 },
  declineReason: null,
  lastError: null,
});

/** A TMF Error whose message is the server's own words (its reason is the status phrase). */
const refused = (status: number, message: string) =>
  json(
    { '@type': 'Error', code: String(status), reason: 'Refused', message, status: String(status) },
    { status },
  );

const TWO_CARDS = () => json([card(CARD1, 'Visa A', false), card(CARD2, 'Visa B', true)]);

function show(onChanged = () => {}) {
  render(
    <ToastProvider>
      <PaymentMethodsPanel account={prepaid} version={0} onChanged={onChanged} />
    </ToastProvider>,
  );
}

type Mock = ReturnType<typeof stubFetch>['fetchMock'];
const sent = (fetchMock: Mock, method: string, url: string) =>
  fetchMock.mock.calls
    .filter(([u, init]) => init?.method === method && String(u) === url)
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
const writes = (fetchMock: Mock) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method !== undefined && init.method !== 'GET');
// the footer's alert, which may hold a negative Note (itself an alert)
const problem = async (dialog: HTMLElement) =>
  (await within(dialog).findAllByRole('alert'))[0]?.textContent;
const escape = () => fireEvent.keyDown(window, { key: 'Escape' });

async function fillCard(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Work' } });
  fireEvent.change(within(dialog).getByLabelText('Brand'), { target: { value: 'mastercard' } });
  fireEvent.change(within(dialog).getByLabelText('Last four digits'), {
    target: { value: '5555' },
  });
  fireEvent.change(within(dialog).getByLabelText('Expiry month'), { target: { value: '3' } });
  fireEvent.change(within(dialog).getByLabelText('Expiry year'), { target: { value: '2031' } });
}

describe('Add card: refused, uncertain and cancelled', () => {
  it('keeps the input after a refusal and sends a new token on the next try', async () => {
    let answer = () => refused(422, 'the card has expired');
    const { fetchMock } = stubFetch({
      [LIST]: () => json([]),
      [`GET ${AUTO}`]: () => json(RULE),
      [`POST ${METHODS}`]: () => answer(),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Add card' }));
    const dialog = screen.getByRole('dialog', { name: 'Add card' });
    await fillCard(dialog);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save card' }));
    expect(await problem(dialog)).toBe('Refused. the card has expired');
    // what the operator typed is still there
    expect(within(dialog).getByLabelText('Name')).toHaveProperty('value', 'Work');
    expect(within(dialog).getByLabelText('Brand')).toHaveProperty('value', 'mastercard');
    answer = () => json(card(CARD1, 'Work', true), { status: 201 });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save card' }));
    await screen.findByText('Work saved');
    const [first, second] = sent(fetchMock, 'POST', METHODS);
    expect(first).toMatchObject({
      brand: 'mastercard',
      lastFourDigits: '5555',
      expirationDate: '2031-04-01T00:00:00Z',
    });
    // a refusal makes the next try a new card
    expect(second?.token).not.toBe(first?.token);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('reuses the token after an unknown outcome so a replay is the same card', async () => {
    let answer = () => refused(500, 'gateway timeout');
    const { fetchMock } = stubFetch({
      [LIST]: () => json([]),
      [`GET ${AUTO}`]: () => json(RULE),
      [`POST ${METHODS}`]: () => answer(),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Add card' }));
    const dialog = screen.getByRole('dialog', { name: 'Add card' });
    await fillCard(dialog);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save card' }));
    expect(await problem(dialog)).toContain('gateway timeout');
    answer = () => refused(409, 'this card is already saved');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save card' }));
    await within(dialog).findByText('this card is already saved');
    const [first, second] = sent(fetchMock, 'POST', METHODS);
    expect(second?.token).toBe(first?.token);
  });

  it('closes on Cancel and on Escape without sending, but not while saving', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([]),
      [`GET ${AUTO}`]: () => json(RULE),
      [`POST ${METHODS}`]: pending,
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Add card' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add card' }));
    escape();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
    // a save in flight keeps the dialog open: its outcome must be seen
    fireEvent.click(screen.getByRole('button', { name: 'Add card' }));
    const dialog = screen.getByRole('dialog', { name: 'Add card' });
    await fillCard(dialog);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save card' }));
    await waitFor(() => expect(sent(fetchMock, 'POST', METHODS)).toHaveLength(1));
    escape();
    expect(screen.getByRole('dialog', { name: 'Add card' })).toBeTruthy();
  });
});

describe('Top up by card: refused, uncertain and cancelled', () => {
  it('retries an uncertain charge with the same key and a refused one with a new key', async () => {
    const answers = [
      () => refused(503, 'gateway unavailable'),
      () => refused(422, 'the card is blocked'),
      () => json(charge('pending'), { status: 202 }),
    ];
    let charges = 0;
    const { fetchMock } = stubFetch({
      [LIST]: TWO_CARDS,
      [`GET ${AUTO}`]: () => json(RULE),
      [`GET ${TOPUPS}?limit=10`]: () => {
        charges += 1;
        return json(charges > 1 ? [charge('pending')] : []);
      },
      [`POST ${TOPUPS}`]: () => (answers.shift() as () => Response)(),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Top up by card' }));
    const dialog = screen.getByRole('dialog', { name: 'Top up by card' });
    await within(dialog).findByText('No card charges yet.');
    // the operator picks the other card
    fireEvent.change(within(dialog).getByLabelText('Card'), { target: { value: CARD1 } });
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '25' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Charge card' }));
    expect(await problem(dialog)).toBe(
      'gateway unavailable. Unsure whether it was queued: retry sends it again safely.',
    );
    // the input stays and the primary action says what it does now
    expect(within(dialog).getByLabelText('Amount')).toHaveProperty('value', '25');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
    await within(dialog).findByText('the card is blocked');
    expect(await problem(dialog)).toBe('Refused. the card is blocked');
    expect(within(dialog).getByRole('button', { name: 'Charge card' })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Charge card' }));
    await within(dialog).findByText('Pending');
    expect(within(dialog).getByLabelText('Amount')).toHaveProperty('value', '');
    const [first, second, third] = sent(fetchMock, 'POST', TOPUPS);
    expect(first).toMatchObject({ paymentMethod: { id: CARD1 } });
    expect(second?.idempotencyKey).toBe(first?.idempotencyKey);
    expect(third?.idempotencyKey).not.toBe(second?.idempotencyKey);
  });

  it('shows a refused retry of an exhausted charge and refreshes the list on demand', async () => {
    let looks = 0;
    const { calls } = stubFetch({
      [LIST]: TWO_CARDS,
      [`GET ${AUTO}`]: () => json(RULE),
      [`GET ${TOPUPS}?limit=10`]: () => {
        looks += 1;
        return json([charge(looks > 1 ? 'confirmed' : 'exhausted')]);
      },
      [`POST /internal/v1/payment/command/${CMD}/retry`]: () =>
        refused(409, 'the charge is no longer exhausted'),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Top up by card' }));
    const dialog = screen.getByRole('dialog', { name: 'Top up by card' });
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Retry' }));
    expect(await problem(dialog)).toBe('Refused. the charge is no longer exhausted');
    expect(calls).toContain(`POST /internal/v1/payment/command/${CMD}/retry`);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    await within(dialog).findByText('Confirmed');
    expect(calls.filter((c) => c === `GET ${TOPUPS}?limit=10`)).toHaveLength(2);
  });

  it('tells the card that a dialog closed by Close or Escape may have changed things', async () => {
    const onChanged = vi.fn();
    stubFetch({
      [LIST]: TWO_CARDS,
      [`GET ${AUTO}`]: () => json(RULE),
      [`GET ${TOPUPS}?limit=10`]: () => json([]),
    });
    show(onChanged);
    fireEvent.click(await screen.findByRole('button', { name: 'Top up by card' }));
    const dialog = screen.getByRole('dialog', { name: 'Top up by card' });
    await within(dialog).findByText('No card charges yet.');
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' }).at(-1) as HTMLElement);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onChanged).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Top up by card' }));
    escape();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(onChanged).toHaveBeenCalledTimes(2);
  });
});

describe('Auto top-up: refused and cancelled', () => {
  it('keeps the edited rule after a refusal and turns it off with enabled alone', async () => {
    let answer = () => refused(422, 'the threshold is above the account limit');
    const { fetchMock } = stubFetch({
      [LIST]: TWO_CARDS,
      [`GET ${AUTO}`]: () => json(RULE),
      [`PATCH ${AUTO}`]: () => answer(),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const dialog = screen.getByRole('dialog', { name: 'Auto top-up' });
    // the saved rule is loaded and a default card exists: no warning
    expect(within(dialog).getByLabelText('Threshold')).toHaveProperty('value', '5.00');
    expect(within(dialog).queryByText(/No default card is saved/)).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('Threshold'), { target: { value: '15' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await problem(dialog)).toBe('Refused. the threshold is above the account limit');
    expect(within(dialog).getByLabelText('Threshold')).toHaveProperty('value', '15');
    answer = () => json({ ...RULE, enabled: false });
    fireEvent.click(within(dialog).getByLabelText('Top up automatically'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Auto top-up saved');
    expect(sent(fetchMock, 'PATCH', AUTO)).toEqual([
      {
        enabled: true,
        threshold: { unit: 'USD', value: 15 },
        amount: { unit: 'USD', value: 20 },
      },
      { enabled: false },
    ]);
  });

  it('closes on Cancel and on Escape without sending', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: TWO_CARDS,
      [`GET ${AUTO}`]: () => json(RULE),
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    escape();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });
});
