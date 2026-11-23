import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AccountTools } from './AccountTools';
import { AddOns } from './AddOns';
import { stubFetch, json, tmfError } from '../test/fetch';

const account = {
  id: 'account1',
  name: 'My prepaid account',
  ratingType: 'prepaid',
  state: 'Active',
  currency: 'USD',
  autoTopUp: null,
  balance: null,
  serviceAddress: null,
} as const;
const line = {
  id: 'line1',
  billingAccount: { id: account.id },
  number: null,
  plan: { name: 'Plan', id: 'plan1' },
  status: 'active',
  startDate: '2026-10-01',
  paidThrough: null,
  addOns: [],
  renewalMode: 'balance' as const,
  offeringKind: 'plan' as const,
};
it('keeps the top-up amount exact and retries the same uncertain charge with the same key', async () => {
  let attempts = 0;
  const { fetchMock } = stubFetch({
    'GET /self-care/v1/paymentMethod': () =>
      json([
        { id: 'card1', billingAccount: { id: account.id }, brand: 'Visa', lastFourDigits: '4242' },
      ]),
    'POST /self-care/v1/topUp': () =>
      ++attempts === 1 ? tmfError(503, 'Try again') : json({ state: 'pending' }),
    'GET /self-care/v1/history?billingAccountId=account1&offset=0&limit=20': () => json([]),
  });
  render(<AccountTools account={account} refresh={() => {}} />);
  fireEvent.change(await screen.findByLabelText('Top-up amount (USD)'), {
    target: { value: '19.90' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
  await screen.findByRole('status');
  const writes = fetchMock.mock.calls
    .filter(([url]) => url === '/self-care/v1/topUp')
    .map(([, init]) => init);
  expect(writes.map((init) => init?.body)).toEqual(
    Array(2).fill('{"amount":{"unit":"USD","value":19.90},"paymentMethodId":"card1"}'),
  );
  const keys = writes.map((init) => new Headers(init?.headers).get('Idempotency-Key'));
  expect(keys[0]).toBeTruthy();
  expect(keys[0]).toBe(keys[1]);
  fireEvent.click(screen.getByText('History'));
  await screen.findByText('No transactions.');
});
it('sends the selected line and offering and reports a refusal', async () => {
  const { fetchMock } = stubFetch({
    'GET /self-care/v1/offering?kind=addOn&billingAccountId=account1': () =>
      json([
        { id: 'pack1', name: 'Extra data', firstCharge: { amount: { unit: 'USD', value: 5 } } },
      ]),
    'POST /self-care/v1/addOn': () => tmfError(422, 'Insufficient balance'),
  });
  const refresh = vi.fn();
  render(<AddOns line={line} refresh={refresh} />);
  fireEvent.click(await screen.findByRole('button', { name: /Buy Extra data/ }));
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toContain('Insufficient balance'),
  );
  expect(fetchMock.mock.calls.at(-1)?.[1]?.body).toBe('{"lineId":"line1","offeringId":"pack1"}');
  expect(refresh).not.toHaveBeenCalled();
});
