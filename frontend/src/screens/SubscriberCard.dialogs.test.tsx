import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, pending, type Handler } from '../test/fetch';
import { accountBody, billBody, billsUrl } from '../test/fixtures';
import {
  A,
  BUCKET,
  C,
  INTERNAL,
  P1,
  backend,
  open,
  product,
  productAction,
  productRow,
  selectTab,
  usd,
} from '../test/subscriberCardKit';

// Every dialog the billing account card opens closes again without a write and leaves the
// card as it was (#487): Escape, the dialog's own close path, from each place that opens it.

const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
const SERVICE_ADDRESS = {
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
const PREPAID = accountBody(
  A,
  { id: C, name: 'Olga Kuznetsova' },
  {
    ratingType: 'prepaid',
    accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }],
    contact: [SERVICE_ADDRESS],
  },
);
const bucket = () =>
  json(
    [
      {
        id: BUCKET,
        remainingValue: { amount: 120, units: 'USD' },
        reservedValue: { amount: 0, units: 'USD' },
      },
    ],
    { total: 1 },
  );
const LINE = product(P1, { paidThrough: '2026-10-20', needs_topup: null });
const BILL = billBody('b0000001-0000-4000-8000-000000000001', '2026-09-28', 100, 100, 'new');

// what a dialog loads once open stays in flight: closing it must not wait for an answer
const LOADS: Record<string, Handler> = { 'GET /*': pending };

type Mock = ReturnType<typeof backend>['fetchMock'];
const writes = (fetchMock: Mock) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method !== undefined && init.method !== 'GET');
const escape = () => fireEvent.keyDown(window, { key: 'Escape' });
const press = (name: string, scope: HTMLElement = document.body) => {
  fireEvent.click(within(scope).getByRole('button', { name }));
};
async function enabled(name: string) {
  await waitFor(() =>
    expect(screen.getByRole('button', { name }) as HTMLButtonElement).toHaveProperty(
      'disabled',
      false,
    ),
  );
  press(name);
}
const strip = () => document.querySelector('[data-ui="ui.subscriber.moneyStrip"]') as HTMLElement;

const postpaid = () => backend({ [billsUrl(A)]: () => json([BILL], { total: 1 }), ...LOADS });
const empty = () =>
  backend({
    [`GET ${INTERNAL}/products`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/charges?limit=20`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/transactions?limit=20`]: () => json([], { total: 0 }),
    ...LOADS,
  });
const prepaid = () =>
  backend(
    {
      [BUCKETS]: bucket,
      [`GET ${INTERNAL}/products`]: () => json([LINE], { total: 1 }),
      ...LOADS,
    },
    PREPAID,
  );

const CASES: [string, () => ReturnType<typeof backend>, () => Promise<void>, RegExp | string][] = [
  [
    'Record payment from the money strip (postpaid)',
    postpaid,
    async () => {
      await waitFor(() =>
        expect(within(strip()).getByRole('button', { name: 'Record payment' })).toHaveProperty(
          'disabled',
          false,
        ),
      );
      press('Record payment', strip());
    },
    'Record payment',
  ],
  [
    'Record payment from the Bills tab',
    postpaid,
    async () => {
      selectTab('Bills');
      const tab = screen.getByRole('tabpanel');
      await waitFor(() =>
        expect(within(tab).getByRole('button', { name: 'Record payment' })).toHaveProperty(
          'disabled',
          false,
        ),
      );
      press('Record payment', tab);
    },
    'Record payment',
  ],
  [
    'a bill from the Bills tab',
    postpaid,
    async () => {
      selectTab('Bills');
      fireEvent.click(await within(screen.getByRole('tabpanel')).findByText('2026-09-28'));
    },
    /^Bill/,
  ],
  ['Edit', postpaid, async () => press('Edit'), 'Edit billing account'],
  [
    'Add billing account',
    postpaid,
    async () => {
      press('More actions');
      fireEvent.click(screen.getByRole('menuitem', { name: 'Add billing account' }));
    },
    'Add billing account',
  ],
  ['Add product', postpaid, async () => press('Add product'), 'Add product'],
  [
    'Delete billing account',
    empty,
    async () => {
      await screen.findByText('No active products');
      press('More actions');
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete…' }));
    },
    'Delete billing account',
  ],
  [
    'Subscriber number',
    postpaid,
    async () => {
      fireEvent.click(
        productAction(await waitFor(() => productRow('Mobile L')), 'Subscriber number'),
      );
    },
    'Subscriber number',
  ],
  [
    'Number Transfer PIN',
    postpaid,
    async () => {
      fireEvent.click(
        productAction(await waitFor(() => productRow('Mobile L')), 'Number Transfer PIN…'),
      );
    },
    'Number Transfer PIN',
  ],
  [
    'Change offering',
    postpaid,
    async () => {
      fireEvent.click(
        productAction(await waitFor(() => productRow('Mobile L')), 'Change offering…'),
      );
    },
    'Change offering: Mobile L',
  ],
  [
    'Record payment from the money strip (prepaid)',
    prepaid,
    async () => {
      await waitFor(() =>
        expect(within(strip()).getByRole('button', { name: 'Record payment' })).toHaveProperty(
          'disabled',
          false,
        ),
      );
      press('Record payment', strip());
    },
    'Record payment',
  ],
  ['Top up', prepaid, () => enabled('Top up'), 'Top up'],
  ['Transfer', prepaid, () => enabled('Transfer'), 'Transfer funds'],
  [
    'Device',
    prepaid,
    async () => {
      fireEvent.click(productAction(await waitFor(() => productRow('Mobile L')), 'Device…'));
    },
    'Device',
  ],
  [
    'Edit service address',
    prepaid,
    async () => {
      selectTab('Account');
      press('Edit service address');
    },
    'Edit service address',
  ],
  [
    'Remove service address',
    prepaid,
    async () => {
      selectTab('Account');
      press('Remove');
    },
    'Remove service address',
  ],
];

describe('every card dialog closes without a write', () => {
  it.each(CASES)('%s', async (_what, stub, openDialog, title) => {
    const { fetchMock } = stub();
    await open();
    await openDialog();
    expect(await screen.findByRole('dialog', { name: title })).toBeTruthy();
    escape();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // the card is still there, on the same account
    expect(screen.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeTruthy();
    expect(window.location.pathname).toBe(`/billing-accounts/${A}`);
    expect(writes(fetchMock)).toEqual([]);
  });
});
