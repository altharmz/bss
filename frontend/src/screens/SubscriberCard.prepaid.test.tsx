import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { json, pending, stubFetch, tmfError, type Handler } from '../test/fetch';
import { NUMBERS } from '../api/numbers';
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';
import {
  A,
  C,
  O2,
  P1,
  P3,
  ORDERS,
  INTERNAL,
  usd,
  product,
  FIRST_CHARGE,
  backend,
  open,
  selectTab,
  productAction,
  productRow,
  BUCKET,
} from '../test/subscriberCardKit';

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

  it('bases the paid-through hint on the business date and keeps error notes below the strip', async () => {
    backend(
      {
        [BUCKETS]: () => tmfError(503, 'Unavailable'),
        [`GET ${INTERNAL}/products`]: () => json([prepaidProduct(P1, {})], { total: 1 }),
      },
      PREPAID,
    );
    await open();
    const strip = expectUi('ui.subscriber.moneyStrip');
    expect(await within(strip).findByText('in 19 days')).toBeTruthy();
    const note = await within(strip).findByRole('alert');
    expect(note.parentElement).toBe(strip);
    expect(strip.firstElementChild?.className).toBe('bss-money-strip');
    expect(note.previousElementSibling).toBe(strip.firstElementChild);
    expect(within(expectUi('ui.subscriber.prepaid.available')).getByText('—')).toBeTruthy();
    expect(within(expectUi('ui.subscriber.prepaid.reserved')).getByText('—')).toBeTruthy();
  });

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
    const kpis = expectUi('ui.subscriber.moneyStrip');
    const figure = (label: string) =>
      (within(kpis).getByText(label).closest('.money-figure') as HTMLElement).textContent;
    expect(await within(kpis).findByText('100.50')).toBeTruthy();
    expect(figure('Ledger')).toContain('120.00');
    expect(figure('Available')).toContain('100.50');
    expect(figure('Reserved')).toContain('19.50');
    expect(
      Array.from(kpis.querySelectorAll('.money-operator')).map((el) => el.textContent),
    ).toEqual(['−', '=']);
    expect(within(kpis).getByRole('button', { name: 'Top up' }).className).toContain('primary');
    expect(within(kpis).getByRole('button', { name: 'Hold' })).toBeTruthy();
    expect(expectUi('ui.subscriber.prepaid.available').textContent).toContain('100.50');
    expect(expectUi('ui.subscriber.prepaid.reserved').textContent).toContain('19.50');
    expect(await within(kpis).findByText('2026-10-12')).toBeTruthy(); // the earliest
    expect(figure('Paid through')).toContain('2026-10-12');
    expect(figure('Shortfall')).toContain('17.75');
    expect(figure('Shortfall')).toContain('renewal needs 30.00, available 12.25');
    expect(figure('Shortfall')).toContain('Suspension: funding ran out');
    // none of the postpaid-only figures
    expect(within(kpis).queryByText('Credit limit')).toBeNull();
    expect(within(kpis).queryByText('Available incl. credit')).toBeNull();
    expect(within(kpis).queryByText('In arrears')).toBeNull();
    expect(within(expectUi('ui.subscriber.account')).queryByText('Credit limit')).toBeNull();
    expect(
      within(expectUi('ui.subscriber.account')).queryByText('Available incl. credit'),
    ).toBeNull();
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
    const kpis = expectUi('ui.subscriber.moneyStrip');
    expect((await within(kpis).findByText('−5.00')).closest('.bss-amount')?.className).toContain(
      'negative',
    );
    expect(await within(kpis).findByText('none')).toBeTruthy();
    expect(within(kpis).getByText('2026-10-20')).toBeTruthy();
  });

  it('reports a failing bucket without taking the card down', async () => {
    backend({ [BUCKETS]: () => tmfError(503, 'Unavailable') }, PREPAID);
    await open();
    expect(await within(expectUi('ui.subscriber.moneyStrip')).findByRole('alert')).toBeTruthy();
    expect(within(expectUi('ui.subscriber.moneyStrip')).getByText('Ledger')).toBeTruthy();
  });

  it('asks nothing of TMF654 for a postpaid account', async () => {
    const { fetchMock } = backend();
    await open();
    await within(expectUi('ui.subscriber.moneyStrip')).findByText('Credit limit');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('prepayBalance'))).toBe(false);
  });
});

describe('usage tab', () => {
  const SUMMARY = `GET /internal/v1/usage/summary?product.id=${P1}`;
  const RECORDS = `GET /tmf-api/productUsageManagement/v5/productUsage?product.id=${P1}&offset=0&limit=25`;
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
  const MIB = 1024 ** 2;
  const plan = product(P1, {
    offeringKind: 'plan',
    paidThrough: '2026-10-20',
    suspensionCause: null,
    needs_topup: null,
  });
  const summary = (over: Record<string, unknown> = {}) => ({
    product: { id: P1, href: '' },
    date: '2026-10-01',
    period: { startDate: '2026-09-20', endDate: '2026-10-20' },
    allowances: [
      {
        kind: 'data',
        zone: 'domestic',
        unit: 'bytes',
        used: 850 * MIB,
        included: 1000 * MIB,
        remaining: 150 * MIB,
        percent: 85,
        thresholds: [80],
      },
      {
        kind: 'voice',
        zone: 'domestic',
        unit: 'seconds',
        used: 600,
        included: 6000,
        remaining: 5400,
        percent: 10,
        thresholds: [],
      },
      {
        kind: 'sms',
        zone: 'domestic',
        unit: 'messages',
        used: 3,
        included: 0,
        remaining: 0,
        percent: null,
        thresholds: [],
      },
    ],
    throttled: true,
    payAsYouGo: null,
    ...over,
  });
  const record = (id: string, over: Record<string, unknown> = {}) => ({
    id,
    status: 'notRated',
    usageType: 'data',
    usageCharacteristic: [
      { name: 'quantity', value: 5 * MIB },
      { name: 'unit', value: 'bytes' },
      { name: 'eventTime', value: '2026-10-01T06:00:00Z' },
      { name: 'roaming', value: true },
      { name: 'throttled', value: false },
    ],
    ...over,
  });
  const lazy = (routes: Record<string, Handler>) =>
    backend(
      {
        [`GET ${INTERNAL}/products`]: () => json([plan], { total: 1 }),
        [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
          () => json([], { total: 0 }),
        ...routes,
      },
      PREPAID,
    );

  it('shows allowance bars with used, included, remaining and the marks', async () => {
    lazy({
      [SUMMARY]: () => json(summary()),
      [RECORDS]: () => json([record('u1')], { total: 1 }),
    });
    await open('#usage');
    const data = await waitFor(() => expectUi('ui.subscriber.usage.data'));
    expect(data.textContent).toContain('850.0 MB of 1000.0 MB');
    expect(data.textContent).toContain('150.0 MB left');
    expect(data.textContent).toContain('80% reached');
    expect(within(data).getByRole('progressbar').getAttribute('aria-valuenow')).toBe('85');
    expect(expectUi('ui.subscriber.usage.voice').textContent).toContain('10 min 0 s of 100 min');
    expect(expectUi('ui.subscriber.usage.sms').textContent).toContain('nothing included');
    expect(within(expectUi('ui.subscriber.usage.throttled')).getByText('Throttled')).toBeTruthy();
    expect(screen.getByText('2026-09-20 – 2026-10-20 (end excluded)')).toBeTruthy();
  });

  it('lists the records with roaming and pages them', async () => {
    const second = `GET /tmf-api/productUsageManagement/v5/productUsage?product.id=${P1}&offset=25&limit=25`;
    lazy({
      [SUMMARY]: () => json(summary({ throttled: false })),
      [RECORDS]: () => json([record('u1')], { more: true }),
      [second]: () => json([record('u2', { usageType: 'sms' })], { more: false }),
    });
    await open('#usage');
    expect(await screen.findByText('Roaming')).toBeTruthy();
    expect(screen.getByText('2026-10-01 09:00:00')).toBeTruthy();
    expect(screen.getByText('5.0 MB')).toBeTruthy();
    expect(screen.queryByText('Throttled', { selector: '.bss-badge' })).toBeNull();
    fireEvent.click(expectUi('ui.subscriber.usage.next'));
    expect(await screen.findByText('Showing 26–26')).toBeTruthy();
    expect(expectUi('ui.subscriber.usage.next').hasAttribute('disabled')).toBe(true);
  });

  it('shows the rating status and the applied price only on a rated record', async () => {
    lazy({
      [SUMMARY]: () => json(summary({ throttled: false })),
      [RECORDS]: () =>
        json(
          [
            record('u1', {
              status: 'rated',
              usageProductPrice: [
                {
                  name: 'Metered data usage',
                  unitOfMeasure: '1 MB',
                  price: { dutyFreeAmount: usd(0.25) },
                },
              ],
            }),
            record('u2'),
          ],
          { total: 2 },
        ),
    });
    await open('#usage');
    const applied = await waitFor(() => expectUi('ui.subscriber.usage.appliedPrice'));
    expect(within(applied).getByText('0.25')).toBeTruthy();
    expect(within(applied).getByText('USD')).toBeTruthy();
    expect(within(applied).getByText('Metered data usage · per 1 MB')).toBeTruthy();
    const rated = screen.getByText('Rated').closest('[role="row"]') as HTMLElement;
    expect(rated.querySelector('[data-ui="ui.subscriber.usage.status"]')?.textContent).toBe(
      'Rated',
    );
    const unrated = screen.getByText('Not rated').closest('[role="row"]') as HTMLElement;
    expect(within(unrated).getByText('—')).toBeTruthy();
    expect(unrated.querySelector('[data-ui="ui.subscriber.usage.appliedPrice"]')).toBeNull();
  });

  it('asks nothing of usage until the tab is opened', async () => {
    const { fetchMock } = lazy({
      [SUMMARY]: () => json(summary()),
      [RECORDS]: () => json([], { total: 0 }),
    });
    await open();
    await within(expectUi('ui.subscriber.moneyStrip')).findByText('Ledger');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('usage'))).toBe(false);
  });

  it('reports a failing summary with a retry, records unaffected', async () => {
    let fail = true;
    lazy({
      [SUMMARY]: () => (fail ? tmfError(503, 'Unavailable') : json(summary())),
      [RECORDS]: () => json([record('u1')], { total: 1 }),
    });
    await open('#usage');
    expect(await screen.findByText('Failed to load the usage summary')).toBeTruthy();
    expect(await screen.findByText('Roaming')).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText(/150.0 MB left/)).toBeTruthy();
  });

  it('shows the pay-as-you-go balance and a barred line', async () => {
    lazy({
      [SUMMARY]: () =>
        json(
          summary({
            payAsYouGo: {
              balance: { unit: 'USD', value: -2.5 },
              barred: true,
              barring: null,
              unratedRecords: 4,
            },
          }),
        ),
      [RECORDS]: () => json([], { total: 0 }),
    });
    await open('#usage');
    expect(await screen.findByText('Barred')).toBeTruthy();
    expect(screen.getByText('-2.5 USD')).toBeTruthy();
    expect(await screen.findByText('No usage')).toBeTruthy();
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
    press(form, 'Cash');
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
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
    type(form, 'Reason', ' Correction ');
    press(form, 'Apply adjustment');
    await screen.findByRole('status');
    await openForm('Adjust');
    form = await dialog('Adjust balance');
    press(form, 'Debit');
    type(form, 'Amount', '12.50');
    type(form, 'Reason', ' Correction ');
    press(form, 'Apply adjustment');
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(2));
    const [credit, debit] = posts(fetchMock);
    const head = (value: string) =>
      `{"@type":"AdjustBalance",${quantity(value)},"usageType":"monetary","bucket":{"id":"${BUCKET}"},"reason":"Correction"}`;
    expect(credit?.body).toBe(head('12.50'));
    expect(debit?.body).toBe(head('-12.50'));
    expect(credit?.key).not.toBe(debit?.key); // two submissions, two keys
  });

  it("holds funds, optionally through a business date (the end is that date's close)", async () => {
    const { fetchMock } = await prepaid({ [`POST ${PREPAY}/reserveBalance`]: stored('created') });
    press(document.body, 'Hold');
    const form = await dialog('Hold funds');
    expectUi('ui.subscriber.prepaid.hold');
    type(form, 'Amount', '20');
    type(form, 'Hold expires on', '2026-09-30');
    expect(within(form).getByText('Enter today or a later business date.')).toBeTruthy();
    expect(
      (within(form).getByRole('button', { name: /^Hold [\d,]+\.\d{2} USD$/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    type(form, 'Hold expires on', '2026-10-05');
    fireEvent.click(within(form).getByRole('button', { name: /^Hold [\d,]+\.\d{2} USD$/ }));
    await screen.findByRole('status');
    expect(posts(fetchMock)[0]?.body).toBe(
      `{"@type":"ReserveBalance",${quantity('20.00')},"usageType":"monetary",` +
        `"bucket":{"id":"${BUCKET}"},${owner},` +
        '"validFor":{"endDateTime":"2026-10-06T00:00:00+03:00"}}',
    );
  });

  it('refuses a debit or hold above the available funds before sending anything', async () => {
    const { fetchMock } = await prepaid();
    press(document.body, 'Hold');
    const form = await dialog('Hold funds');
    type(form, 'Amount', '100.51');
    expect(within(form).getByText('Enter at most the available 100.50.')).toBeTruthy();
    expect(
      (within(form).getByRole('button', { name: /^Hold [\d,]+\.\d{2} USD$/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    type(form, 'Amount', '100.50');
    expect(
      (within(form).getByRole('button', { name: /^Hold [\d,]+\.\d{2} USD$/ }) as HTMLButtonElement)
        .disabled,
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
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
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
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
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
    type(form, 'Reason', 'Correction');
    press(form, 'Apply adjustment');
    expect((await within(form).findByRole('alert')).textContent).toBe(
      'Refused. insufficient spendable funds',
    );
    expect(screen.queryByRole('status')).toBeNull(); // nothing was recorded
    press(form, 'Apply adjustment');
    await screen.findByRole('status');
    const [first, second] = posts(fetchMock);
    expect(second?.key).not.toBe(first?.key);
  });

  it('shows a definitive refusal and does not offer a blind retry', async () => {
    await prepaid({ [`POST ${PREPAY}/topupBalance`]: () => tmfError(422, 'bad amount') });
    press(document.body, 'Top up');
    const form = await dialog('Top up');
    type(form, 'Amount', '10');
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
    expect((await within(form).findByRole('alert')).textContent).toContain('bad amount');
    expect(within(form).queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('offers no money forms on a postpaid account', async () => {
    backend();
    await open();
    await within(expectUi('ui.subscriber.moneyStrip')).findByText('Credit limit');
    expect(screen.queryByRole('button', { name: 'Top up' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hold' })).toBeNull();
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
    expectUi('ui.subscriber.prepaid.transfer.recipient');
    const next = within(form).getByRole('button', { name: 'Next' }) as HTMLButtonElement;
    if (next.disabled) return;
    press(form, 'Next');
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
    expectUi('ui.subscriber.prepaid.transfer.balances');
    press(form, 'Review transfer');
    expectUi('ui.subscriber.prepaid.transfer.acknowledgement');
    expectUi('ui.subscriber.prepaid.transfer.summary');
    expect(within(form).getByText(/This is final/).textContent).toContain("can't be undone here");
    expect(posts(fetchMock)).toEqual([]); // nothing is sent before the confirmation
    const primary = within(form).getByRole('button', { name: 'Transfer 30.00 USD' });
    expect(primary).toHaveProperty('disabled', true);
    fireEvent.keyDown(primary, { key: 'Enter' });
    expect(posts(fetchMock)).toEqual([]);
    fireEvent.click(within(form).getByRole('checkbox'));
    expect(primary).toHaveProperty('disabled', false);
    fireEvent.keyDown(primary, { key: 'Enter' });
    expect(posts(fetchMock)).toEqual([]);
    press(form, 'Transfer 30.00 USD');
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
    fireEvent.click(within(form).getByRole('checkbox'));
    press(form, 'Transfer 30.00 USD');
    expect((await within(form).findByRole('alert')).textContent).toBe(
      'Refused. the receiver account is closed',
    );
    expect(screen.queryByText(/accepted, waiting/)).toBeNull();
  });

  it('clears transfer acknowledgement after returning to Amount', async () => {
    const { form, fetchMock } = await transferForm();
    await fill(form);
    press(form, 'Review transfer');
    fireEvent.click(within(form).getByRole('checkbox'));
    expect(within(form).getByRole('button', { name: 'Transfer 30.00 USD' })).toHaveProperty(
      'disabled',
      false,
    );
    press(form, 'Back');
    type(form, 'Amount', '20');
    press(form, 'Review transfer');
    expect(within(form).getByRole('checkbox')).toHaveProperty('checked', false);
    expect(within(form).getByRole('button', { name: 'Transfer 20.00 USD' })).toHaveProperty(
      'disabled',
      true,
    );
    expect(posts(fetchMock)).toEqual([]);
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
    fireEvent.click(within(form).getByRole('checkbox'));
    press(form, 'Transfer 30.00 USD');
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
    expect(within(form).getByRole('button', { name: 'Next' })).toHaveProperty('disabled', true);
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
    expect(within(form).getByRole('button', { name: 'Next' })).toHaveProperty('disabled', true);
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
    expect(within(form).getByRole('button', { name: 'Next' })).toHaveProperty('disabled', true);
    // the sender's own number resolves to the sender's account
    const own = '79991234000';
    type(form, 'Receiver number', own);
    press(form, 'Find');
    await within(form).findByText(/belongs to this account/);
    expect(within(form).getByRole('button', { name: 'Next' })).toHaveProperty('disabled', true);
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
    fireEvent.click(productAction(row, 'Deactivate'));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'End on a date' }),
    );
    return stub;
  }

  it('keeps the Overview watch mounted through a card refresh and tab changes', async () => {
    let accountReads = 0;
    let release: ((response: Response) => void) | undefined;
    const { calls } = await deactivate({
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () =>
        ++accountReads === 1
          ? json(PREPAID)
          : new Promise<Response>((resolve) => {
              release = resolve;
            }),
      [`GET ${CMD}/${ORDER_ID}`]: () => json(command()),
    });
    const steps = await screen.findByRole('list', { name: 'Command steps' });
    const progress = expectUi('ui.subscriber.inProgress');
    expect(screen.getByRole('tabpanel').contains(progress)).toBe(true);
    const watched = expectUi('ui.subscriber.prepaid.orderWatch');
    expect(progress.contains(watched)).toBe(true);
    expect(within(steps).getByText('Accepted')).toBeTruthy();
    expect(within(steps).getByText('Waiting for the worker').getAttribute('aria-current')).toBe(
      'step',
    );
    await waitFor(() => expect(release).toBeDefined());
    release?.(json(PREPAID));
    await waitFor(() => expect(expectUi('ui.subscriber.prepaid.orderWatch')).toBe(watched));
    selectTab('Transactions');
    expect(document.contains(watched)).toBe(true);
    expect(watched.closest('[hidden]')).toBeTruthy();
    selectTab('Overview');
    expect(expectUi('ui.subscriber.prepaid.orderWatch')).toBe(watched);
    expect(calls.filter((call) => call === `GET ${CMD}/${ORDER_ID}`)).toHaveLength(1);
  });

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
    expect(screen.getByText('Needs top-up').getAttribute('aria-current')).toBe('step');
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
    expect(screen.getByText('Failed (technical)').getAttribute('aria-current')).toBe('step');
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
    await screen.findByRole('tab', { name: 'Commands 1' });
    selectTab('Commands');
    expect(
      screen.getByRole('tab', { name: 'Commands 1' }).querySelector('.badge-info'),
    ).toBeTruthy();
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
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    const dialog = screen.getByRole('dialog', { name: 'Add product' });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    const add = await within(dialog).findByRole('button', { name: 'Add Fiber 100' });
    await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(add);
    expect(await screen.findByText('Order for Fiber 100 completed')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText(/The order was/)).toBeNull();
  });
});

describe('prepaid first-charge preview in Add product', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const BUCKETS = `GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const OFFER = {
    id: O2,
    name: 'Fiber 100',
    externalIdentifier: [{ id: 'NET-100', externalIdentifierType: 'offeringCode' }],
    productSpecification: { name: 'Home Internet' },
    productOfferingPrice: [{ id: 'pr2' }],
  };
  const firstCharge = (over: Record<string, unknown> = {}) => ({
    ...FIRST_CHARGE,
    period: { start: '2026-10-01', end: '2026-11-01' },
    prorated: false,
    billableDays: 31,
    periodDays: 31,
    amount: usd(780),
    next: null,
    balance: usd(120),
    balanceAfter: usd(-660),
    creditLimit: null,
    exceedsCreditLimit: true,
    required: usd(780),
    available: usd(120),
    shortfall: usd(660),
    ...over,
  });
  const urlOf = (start: string) =>
    `GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=${start}`;

  function stubbed(routes: Record<string, Handler>) {
    return backend(
      {
        [BUCKETS]: () =>
          json(
            [
              {
                id: BUCKET,
                remainingValue: { amount: 120, units: 'USD' },
                reservedValue: { amount: 0, units: 'USD' },
              },
            ],
            { total: 1 },
          ),
        'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
          () => json([OFFER], { total: 1 }),
        'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
          () =>
            json(
              [
                {
                  id: 'pr2',
                  price: usd(650),
                  validFor: { startDateTime: '2026-06-01T21:00:00Z' },
                },
              ],
              { total: 1 },
            ),
        ...routes,
      },
      PREPAID,
    );
  }

  async function chooseOffering() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    const dialog = screen.getByRole('dialog', { name: 'Add product' });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    return dialog;
  }

  it('shows the server preview and makes Top up primary for an immediate shortfall', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-01T12:34:56Z'));
    const { fetchMock } = stubbed({ [urlOf('2026-10-01')]: () => json(firstCharge()) });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    const box = expectUi('ui.addProduct.prepaidPreview');
    expect(within(box).getByTestId('prepaid-required').textContent).toBe('780.00 USD');
    const text = box.textContent ?? '';
    expect(text).toContain('Required now');
    expect(within(dialog).getByText('Paid from the balance on 2026-10-01')).toBeTruthy();
    expect(within(dialog).queryByText(/billing run of/)).toBeNull();
    expect(within(dialog).queryByText(/billing day/)).toBeNull();
    expect(text).toContain('paid in full in advance, no proration');
    expect(text).toContain('2026-10-01 – 2026-10-31');
    expect(text).toContain('Available120.00 USD');
    expect(text).toContain('Shortfall');
    expect(text).toContain('660.00');
    expect(text).toContain('Ledger after');
    expect(text).toContain('Submitting holds the full interval with its taxes');
    expect(within(dialog).queryByTestId('first-charge')).toBeNull();
    expect(within(dialog).getByText('Shortfall 660.00 USD.')).toBeTruthy();
    expect(
      within(dialog).getByText(
        /The first charge needs 780.00 and 120.00 is available\. Top up first/,
      ),
    ).toBeTruthy();
    expect(
      (within(dialog).getByRole('button', { name: 'Save as draft' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    const add = within(dialog).getByRole('button', { name: 'Add Fiber 100' });
    expect((add as HTMLButtonElement).disabled).toBe(true);
    expect(add.className).toContain('secondary');
    const topUp = expectUi('ui.addProduct.topUpShortfall');
    expect(topUp.textContent).toBe('Top up 660.00 USD…');
    expect(topUp.className).toContain('primary');
    expect(within(dialog).getByText('15:34:56').parentElement?.textContent).toBe(
      'Preview from the server · 15:34:56',
    );
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/firstCharge')),
    ).toHaveLength(1);
  });

  it('hands the shortfall to the Top-up form, which sends it as the amount', async () => {
    const { fetchMock } = stubbed({
      [urlOf('2026-10-01')]: () => json(firstCharge({ shortfall: usd(1250.5) })),
      [`POST ${PREPAY}/topupBalance`]: () =>
        json({ id: 'x1', status: 'completed' }, { status: 201 }),
    });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    const button = expectUi('ui.addProduct.topUpShortfall');
    expect(button.textContent).toBe('Top up 1,250.50 USD…');
    fireEvent.click(button);
    expect(screen.queryByRole('dialog', { name: 'Add product' })).toBeNull();
    const form = await screen.findByRole('dialog', { name: 'Top up' });
    expect((within(form).getByLabelText('Amount') as HTMLInputElement).value).toBe('1250.50');
    fireEvent.click(within(form).getByRole('button', { name: 'Cash' }));
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
    await screen.findByRole('status');
    const sent = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(String(sent?.[0])).toBe(`${PREPAY}/topupBalance`);
    expect(String(sent?.[1]?.body)).toBe(
      '{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},"usageType":"monetary",' +
        `"bucket":{"id":"${BUCKET}"},"partyAccount":{"id":"${A}","@referredType":"BillingAccount"},` +
        '"paymentMethod":{"id":"cash","name":"cash"}}',
    );
  });

  it('a future start shows a hint and no Top-up button, and no ledger after', async () => {
    stubbed({
      [urlOf('2026-10-01')]: () => json(firstCharge()),
      [urlOf('2026-10-20')]: () =>
        json(firstCharge({ period: { start: '2026-10-20', end: '2026-11-20' } })),
    });
    const dialog = await chooseOffering();
    fireEvent.change(within(dialog).getByLabelText('Start date'), {
      target: { value: '2026-10-20' },
    });
    await within(dialog).findByText('Required on 2026-10-20');
    expect(within(dialog).getByText('Paid from the balance on 2026-10-20')).toBeTruthy();
    expect(
      (
        within(dialog).getByRole('button', {
          name: 'Add Fiber 100',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    expect(
      within(dialog).getByText(
        'Funds are checked on 2026-10-20; nothing is held now. Top up before then.',
      ),
    ).toBeTruthy();
    expect(document.querySelector('[data-ui="ui.addProduct.topUpShortfall"]')).toBeNull();
    expect(within(dialog).queryByText(/Insufficient funds/)).toBeNull();
    expect(within(dialog).queryByText('Ledger after')).toBeNull();
    expect(
      within(dialog).getByText(
        'On 2026-10-20 the billing worker takes the interval from the funds available then.',
      ),
    ).toBeTruthy();
  });

  it('shows no shortfall, warning or button when the funds cover the interval', async () => {
    stubbed({
      [urlOf('2026-10-01')]: () =>
        json(
          firstCharge({
            available: usd(900),
            shortfall: usd(0),
            balanceAfter: usd(120),
            exceedsCreditLimit: false,
          }),
        ),
    });
    const dialog = await chooseOffering();
    await within(dialog).findByTestId('prepaid-required');
    expect(within(dialog).queryByText('Shortfall')).toBeNull();
    expect(within(dialog).queryByText(/Insufficient funds/)).toBeNull();
    expect(document.querySelector('[data-ui="ui.addProduct.topUpShortfall"]')).toBeNull();
  });

  it("opens a prepaid product's Device dialog from its menu", async () => {
    stubbed({
      [`GET /internal/v1/device?product.id=${P1}`]: () =>
        json([
          {
            id: 'd1',
            product: { id: P1 },
            imei: '356938035643809',
            compatibility: 'compatible',
            reasons: [],
            checkedAt: '2026-10-01T09:00:00Z',
            startedAt: '2026-10-01T09:00:00Z',
            endedAt: null,
            recordedByItem: { id: 'i1' },
          },
        ]),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    const item = productAction(row, 'Device…');
    expect(item.getAttribute('data-ui')).toBe('ui.subscriber.deviceItem');
    expectUi('ui.subscriber.deviceItem');
    fireEvent.click(item);
    const dialog = await screen.findByRole('dialog', { name: 'Device' });
    expect(await within(dialog).findByText(/Works on the network · since 2026-10-01/)).toBeTruthy();
  });

  it('sends the optional device IMEI of a line and shows the unconfirmed-device warning', async () => {
    const IMEI = '356938035643809'; // Luhn-valid
    const { fetchMock } = stubbed({
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () =>
          json([{ ...OFFER, productSpecification: { id: 'spec-line', name: 'Mobile' } }], {
            total: 1,
          }),
      'GET /tmf-api/productCatalogManagement/v5/productSpecification/spec-line': () =>
        json({ id: 'spec-line', resourceSpecification: [{ id: 'imsi' }] }),
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&administrativeState=unlocked&allocationStatus=available&offset=0&limit=50`]:
        pending,
      [urlOf('2026-10-01')]: () =>
        json(
          firstCharge({
            available: usd(900),
            shortfall: usd(0),
            balanceAfter: usd(120),
            exceedsCreditLimit: false,
          }),
        ),
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o-imei',
            state: 'completed',
            '@type': 'ProductOrder',
            productOrderItem: [
              {
                id: '1',
                action: 'add',
                product: {
                  productCharacteristic: [
                    { name: 'imei', value: IMEI },
                    { name: 'deviceCompatibility', value: 'unknown' },
                    { name: 'deviceReasons', value: 'device not in the database' },
                  ],
                },
              },
            ],
          },
          { status: 201 },
        ),
    });
    const dialog = await chooseOffering();
    fireEvent.change(await within(dialog).findByLabelText('SIM'), { target: { value: 'esim' } });
    const field = expectUi('ui.subscriber.addProductImei');
    const add = within(dialog).getByRole('button', { name: 'Add Fiber 100' }) as HTMLButtonElement;
    fireEvent.change(field, { target: { value: '356938035643801' } }); // wrong check digit
    expect(within(dialog).getByText('The check digit (the last one) is wrong.')).toBeTruthy();
    await within(dialog).findByTestId('prepaid-required');
    expect(add.disabled).toBe(true);
    fireEvent.change(field, { target: { value: IMEI } });
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(
      await screen.findByText(/could not confirm the device works: device not in the database/),
    ).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body.productOrderItem[0].product).toEqual({
      '@type': 'ProductRefOrValue',
      productCharacteristic: [
        { '@type': 'StringCharacteristic', name: 'simType', valueType: 'string', value: 'esim' },
        { '@type': 'StringCharacteristic', name: 'imei', valueType: 'string', value: IMEI },
      ],
    });
  });
});
