// Issue #412: the prepaid card's first reads under the app's request bound (app/overload.py:
// BSS_MAX_IN_FLIGHT, 10 per process on the VPS). The card starts more reads than that at
// once; the client sends READ_SLOTS at a time and resends a read refused as overloaded.
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { READ_SLOTS } from '../api/client';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { json, overloadedError, pending, type Handler } from '../test/fetch';
import { accountBody } from '../test/fixtures';
import { A, BUCKET, C, INTERNAL, backend, open, product, usd } from '../test/subscriberCardKit';

const LIMIT = 10; // the VPS's effective BSS_MAX_IN_FLIGHT when the issue was filed
const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
const PRODUCTS = `GET ${INTERNAL}/products`;
const ADDRESS = {
  '@type': 'Contact',
  contactType: 'serviceAddress',
  contactMedium: [
    {
      '@type': 'GeographicAddressContactMedium',
      street1: '1 Market St',
      city: 'San Francisco',
      stateOrProvince: 'CA',
      postCode: '94105',
      country: 'US',
    },
  ],
};
const prepaid = (contact: object[]) =>
  accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    {
      ratingType: 'prepaid',
      accountBalance: [
        { balanceType: 'currentBalance', amount: usd(148), '@type': 'AccountBalance' },
      ],
      contact,
    },
  );
const bucket = () =>
  json(
    [
      {
        id: BUCKET,
        remainingValue: { amount: 148, units: 'USD' },
        reservedValue: { amount: 0, units: 'USD' },
      },
    ],
    { total: 1 },
  );
const line = () =>
  json(
    [
      product('p0000009-0000-4000-8000-000000000009', {
        productOffering: { id: 'o9', name: 'Prepaid Basic', code: 'PRE-B' },
        paidThrough: '2026-10-20',
        suspensionCause: null,
        needs_topup: null,
      }),
    ],
    { total: 1 },
  );
/** Overloaded for the first `times` calls, then `answer`. */
const busy = (times: number, answer: Handler): Handler & { calls: number } => {
  const handler = Object.assign(
    (url?: string) => (handler.calls++ < times ? overloadedError() : answer(url)),
    { calls: 0 },
  );
  return handler;
};

const settle = () => act(() => new Promise((resolve) => setImmediate(resolve)));
async function until<T>(get: () => T): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return get();
    } catch (error) {
      if (i === 200) throw error;
      await settle();
    }
  }
}
/** Fake setTimeout (the backoff), no jitter; findBy cannot wait under it, `until` does. */
function fakeTimers() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  vi.spyOn(Math, 'random').mockReturnValue(0);
}
async function openFaked() {
  window.history.replaceState(null, '', `/subscribers/${A}`);
  render(<App />);
  await until(() => screen.getByRole('heading', { name: /Olga Kuznetsova/ }));
}
const figure = (ui: string) => expectUi(ui).textContent ?? '';
const productsPanel = () =>
  screen.getByRole('heading', { name: 'Active products' }).closest('section') as HTMLElement;

describe('prepaid card under the request bound (#412)', () => {
  it('opens with more reads than the app admits, never more than READ_SLOTS in flight', async () => {
    const { fetchMock } = backend({ [BUCKETS]: bucket, [PRODUCTS]: line }, prepaid([ADDRESS]));
    // the app process: answers after a moment, refuses beyond LIMIT as app/overload.py does
    let inFlight = 0;
    let peak = 0;
    let refused = 0;
    const reads: string[] = [];
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      reads.push(String(input));
      if (inFlight >= LIMIT) {
        refused++;
        return overloadedError();
      }
      inFlight++;
      peak = Math.max(peak, inFlight);
      try {
        await new Promise((resolve) => setTimeout(resolve, 5));
        return await fetchMock(input, init);
      } finally {
        inFlight--;
      }
    });
    await open();
    expect(await screen.findByText('Prepaid Basic')).toBeTruthy();
    await until(() => expect(figure('ui.subscriber.prepaid.available')).toContain('148.00'));
    expect(figure('ui.subscriber.prepaid.reserved')).toContain('0.00');
    expect(new Set(reads).size).toBeGreaterThan(LIMIT); // the burst the card starts
    expect(peak).toBeLessThanOrEqual(READ_SLOTS);
    expect(refused).toBe(0);
    expect(document.querySelector('[data-ui="ui.subscriber.serviceAddress.missing"]')).toBeNull();
  });

  it('recovers a balance and products refused as overloaded, with no Retry click', async () => {
    fakeTimers();
    const buckets = busy(1, bucket);
    const products = busy(1, line);
    backend({ [BUCKETS]: buckets, [PRODUCTS]: products }, prepaid([ADDRESS]));
    await openFaked();
    await until(() => expect([buckets.calls, products.calls]).toEqual([1, 1]));
    // refused once: still loading, no error shown while the read waits Retry-After
    expect(within(productsPanel()).queryByText('Failed to load products')).toBeNull();
    expect(screen.queryByText(/Failed to load the prepaid balance/)).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(1000));
    await until(() => screen.getByText('Prepaid Basic'));
    await until(() => expect(figure('ui.subscriber.prepaid.available')).toContain('148.00'));
    expect([buckets.calls, products.calls]).toEqual([2, 2]);
  });

  it('shows persistent overload with Retry, stops resending, and loads on Retry', async () => {
    fakeTimers();
    const buckets = busy(5, bucket);
    const products = busy(5, line);
    backend({ [BUCKETS]: buckets, [PRODUCTS]: products }, prepaid([]));
    await openFaked();
    await act(() => vi.advanceTimersByTimeAsync(1000 + 2000 + 4000 + 8000));
    const balanceNote = await until(() => screen.getByText(/Failed to load the prepaid balance/));
    expect(balanceNote.textContent).toContain('503 Service Unavailable');
    await until(() => within(productsPanel()).getByText('Failed to load products'));
    expect([buckets.calls, products.calls]).toEqual([5, 5]);
    await act(() => vi.advanceTimersByTimeAsync(60_000)); // no burst sustained after giving up
    expect([buckets.calls, products.calls]).toEqual([5, 5]);
    // the service-address warning keeps its own meaning, beside the overload errors
    expect(expectUi('ui.subscriber.serviceAddress.missing').textContent).toContain(
      'No service address: new prepaid lines are refused',
    );
    // capacity is back: each panel's Retry loads it
    fireEvent.click(within(balanceNote).getByRole('button', { name: 'Retry' }));
    fireEvent.click(within(productsPanel()).getByRole('button', { name: /Retry/ }));
    await until(() => screen.getByText('Prepaid Basic'));
    await until(() => expect(figure('ui.subscriber.prepaid.available')).toContain('148.00'));
    expect(screen.queryByText(/Failed to load the prepaid balance/)).toBeNull();
  });

  it('abandons the backoff on leaving the card: no resend, no stale result on the next account', async () => {
    fakeTimers();
    const B = 'bbbb1111-0000-4000-8000-0000000000b1';
    const buckets = busy(1, bucket);
    const products = busy(1, line);
    backend(
      {
        [BUCKETS]: buckets,
        [PRODUCTS]: products,
        [`GET /tmf-api/accountManagement/v5/billingAccount/${B}`]: () =>
          json(
            accountBody(
              B,
              { id: 'cccc9999-0000-4000-8000-000000000009', name: 'Boris Orlov' },
              { ratingType: 'prepaid', contact: [ADDRESS] },
            ),
          ),
        'GET /*': pending, // the next card's own reads stay in flight
      },
      prepaid([ADDRESS]),
    );
    await openFaked();
    await until(() => expect([buckets.calls, products.calls]).toEqual([1, 1]));
    await act(async () => {
      window.history.pushState(null, '', `/subscribers/${B}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await until(() => screen.getByRole('heading', { name: /Boris Orlov/ }));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect([buckets.calls, products.calls]).toEqual([1, 1]);
    expect(screen.queryByText('Prepaid Basic')).toBeNull();
    expect(figure('ui.subscriber.prepaid.available')).not.toContain('148.00');
  });
});
