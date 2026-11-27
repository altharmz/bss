import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { components } from '../api/generated/care';
import { json, stubFetch, tmfError } from '../test/fetch';
import { AccountTools } from './AccountTools';

type Account = components['schemas']['CareAccount'];
const account: Account = {
  id: 'account1',
  name: 'My prepaid account',
  ratingType: 'prepaid',
  state: 'Active',
  currency: 'USD',
  autoTopUp: null,
  balance: null,
  serviceAddress: null,
};
const CARDS = 'GET /self-care/v1/paymentMethod';
const visa = {
  id: 'card 1',
  billingAccount: { id: 'account1' },
  brand: 'Visa',
  lastFourDigits: '4242',
};
const noContent = () => new Response(null, { status: 204 });
const history = (offset: number) =>
  `GET /self-care/v1/history?billingAccountId=account1&offset=${offset}&limit=20`;

function writes(fetchMock: ReturnType<typeof stubFetch>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') !== 'GET')
    .map(([url, init]) => ({
      url,
      method: init?.method,
      body: init?.body,
      key: new Headers(init?.headers).get('Idempotency-Key'),
    }));
}

describe('self-care account tools', () => {
  it('says why the saved cards could not load', async () => {
    stubFetch({ [CARDS]: () => tmfError(503, 'Cards unavailable') });
    render(<AccountTools account={account} refresh={() => {}} />);
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Cards unavailable');
  });

  it("lists only this account's cards and removes one without a body or key", async () => {
    let removed = false;
    const refresh = vi.fn();
    const { fetchMock } = stubFetch({
      [CARDS]: () =>
        json(
          removed
            ? []
            : [visa, { ...visa, id: 'other', billingAccount: { id: 'account2' }, brand: 'Amex' }],
        ),
      'DELETE /self-care/v1/paymentMethod/card%201': () => {
        removed = true;
        return noContent();
      },
    });
    render(<AccountTools account={account} refresh={refresh} />);
    expect(await screen.findByText(/Visa ending in 4242/)).toBeTruthy();
    expect(screen.queryByText(/Amex/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect((await screen.findByRole('status')).textContent).toBe('Card removed.');
    expect(await screen.findByText('No saved cards.')).toBeTruthy();
    // no cards, no top-up form
    expect(screen.queryByRole('button', { name: 'Top up' })).toBeNull();
    expect(writes(fetchMock)).toEqual([
      { url: '/self-care/v1/paymentMethod/card%201', method: 'DELETE', body: null, key: null },
    ]);
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('saves the card the gateway tokenised, once, with an idempotency key', async () => {
    const { fetchMock } = stubFetch({
      [CARDS]: () => json([]),
      'POST /self-care/v1/paymentMethod': () => json(visa, { status: 201 }),
    });
    render(<AccountTools account={account} refresh={() => {}} />);
    await screen.findByText('No saved cards.');
    const save = screen.getByRole('button', { name: 'Save card' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    const gateway = screen.getByTitle('Test payment gateway') as HTMLIFrameElement;
    // jsdom may replace the frame's window when the gateway page loads, so the message
    // goes again from the frame's current window until it is taken (#498)
    await waitFor(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'stub-card-token', token: 'tok_visa' },
          origin: location.origin,
          source: gateway.contentWindow,
        }),
      );
      expect(save.disabled).toBe(false);
    });
    fireEvent.click(save);
    expect((await screen.findByRole('status')).textContent).toBe('Card saved.');
    // the token is spent: saving again needs a new one
    expect(save.disabled).toBe(true);
    const [write] = writes(fetchMock);
    expect(write).toMatchObject({
      url: '/self-care/v1/paymentMethod',
      method: 'POST',
      body: '{"billingAccountId":"account1","token":"tok_visa"}',
    });
    expect(write?.key).toBeTruthy();
  });

  it.each(['0', '0.00', '12.345', 'ten'])(
    'refuses a top-up of %s before sending anything',
    async (value) => {
      const { fetchMock } = stubFetch({ [CARDS]: () => json([visa]) });
      render(<AccountTools account={account} refresh={() => {}} />);
      fireEvent.change(await screen.findByLabelText('Top-up amount (USD)'), {
        target: { value },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
      expect((await screen.findByRole('alert')).textContent).toBe(
        'Error: Enter a positive amount with at most two decimal places.',
      );
      expect(writes(fetchMock)).toEqual([]);
    },
  );

  it('enables auto top-up with exact amounts and disables it again', async () => {
    const refresh = vi.fn();
    const enabled: Account = {
      ...account,
      autoTopUp: {
        enabled: true,
        threshold: { unit: 'USD', value: 5 },
        amount: { unit: 'USD', value: 20 },
        defaultPaymentMethod: null,
      },
    };
    const { fetchMock } = stubFetch({
      [CARDS]: () => json([]),
      'PUT /self-care/v1/autoTopUp': () => json({}),
    });
    const { rerender } = render(<AccountTools account={account} refresh={refresh} />);
    expect(screen.getByText('Auto top-up · Off')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Disable auto top-up' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Balance threshold (USD)'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Recharge amount (USD)'), {
      target: { value: '20.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enable auto top-up' }));
    expect((await screen.findByRole('status')).textContent).toBe('Auto top-up enabled.');
    rerender(<AccountTools account={enabled} refresh={refresh} />);
    expect(screen.getByText('Auto top-up · On')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Disable auto top-up' }));
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toBe('Auto top-up disabled.'),
    );
    expect(writes(fetchMock)).toEqual([
      {
        url: '/self-care/v1/autoTopUp',
        method: 'PUT',
        body: '{"enabled":true,"billingAccountId":"account1","threshold":{"unit":"USD","value":5.00},"amount":{"unit":"USD","value":20.50}}',
        key: null,
      },
      {
        url: '/self-care/v1/autoTopUp',
        method: 'PUT',
        body: '{"enabled":false,"billingAccountId":"account1"}',
        key: null,
      },
    ]);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('shows no payment tools on a postpaid account', async () => {
    stubFetch({ [CARDS]: () => json([visa]) });
    render(<AccountTools account={{ ...account, ratingType: 'postpaid' }} refresh={() => {}} />);
    expect(screen.getByText('History')).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Payments' })).toBeNull();
    expect(screen.queryByText('Payment methods')).toBeNull();
  });
});

describe('self-care history', () => {
  const row = (n: number) => ({
    id: `tx${n}`,
    date: `2026-11-${String((n % 28) + 1).padStart(2, '0')}`,
    type: 'topUp',
    amount: { unit: 'USD', value: 19.9 },
  });

  it('loads only when opened and pages twenty rows at a time', async () => {
    const { calls } = stubFetch({
      [CARDS]: () => json([]),
      [history(0)]: () => json(Array.from({ length: 20 }, (_, i) => row(i))),
      [history(20)]: () => json([row(20)]),
    });
    render(<AccountTools account={account} refresh={() => {}} />);
    await screen.findByText('No saved cards.');
    expect(calls).not.toContain(history(0));
    fireEvent.click(screen.getByText('History'));
    const list = await screen.findAllByRole('listitem');
    expect(list).toHaveLength(20);
    expect(list[0]?.textContent).toBe('2026-11-01 · topUp · 19.90 USD');
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1));
    expect(screen.getByRole('listitem').textContent).toBe('2026-11-21 · topUp · 19.90 USD');
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(20));
    expect(calls.filter((call) => call === history(0))).toHaveLength(2);
  });

  it('says why the history could not load', async () => {
    stubFetch({
      [CARDS]: () => json([]),
      [history(0)]: () => tmfError(503, 'History unavailable'),
    });
    render(<AccountTools account={account} refresh={() => {}} />);
    await screen.findByText('No saved cards.');
    fireEvent.click(screen.getByText('History'));
    const details = screen.getByText('History').closest('details') as HTMLElement;
    expect((await within(details).findByRole('alert')).textContent).toBe(
      'Error: History unavailable',
    );
  });
});
