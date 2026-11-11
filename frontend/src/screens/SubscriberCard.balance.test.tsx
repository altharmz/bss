import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';
import {
  A,
  C,
  O1,
  P1,
  P2,
  ORDERS,
  INTERNAL,
  HISTORY,
  usd,
  product,
  FIRST_CHARGE,
  backend,
  open,
  selectTab,
  productMenu,
  productAction,
  panel,
  productRow,
  BUCKET,
} from '../test/subscriberCardKit';

describe('prepaid balance actions', () => {
  const PREPAY = '/tmf-api/prepayBalanceManagement/v4';
  const B = 'bbbb7777-0000-4000-8000-0000000000b7';
  const RX_BUCKET = 'bbbb6666-0000-4000-8000-0000000000b6';
  const OWN = `GET ${HISTORY}?partyAccount.id=${A}&offset=0&limit=100`;
  const RECEIVED = `GET ${HISTORY}?receiverBucket.id=${BUCKET}&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const party = { id: A, name: 'Olga Kuznetsova', '@referredType': 'BillingAccount' };
  const action = (id: string, type: string, status: string, requested: string, over = {}) => ({
    id,
    '@type': type,
    status,
    requestedDate: requested,
    partyAccount: party,
    bucket: { id: BUCKET },
    usageType: 'monetary',
    ...over,
  });
  const money = (value: number) => ({ amount: value, units: 'USD' });
  const T1 = 'a1000000-0000-4000-8000-000000000001';
  const T2 = 'a2000000-0000-4000-8000-000000000002';
  const T3 = 'a3000000-0000-4000-8000-000000000003';
  const T4 = 'a4000000-0000-4000-8000-000000000004';
  const T5 = 'a5000000-0000-4000-8000-000000000005';
  const T6 = 'a6000000-0000-4000-8000-000000000006';
  const T7 = 'a7000000-0000-4000-8000-000000000007';
  const topup = action(T1, 'TopupBalance', 'completed', '2026-10-01T05:00:00Z', {
    amount: money(50),
    reason: 'Cash at the desk',
    confirmationDate: '2026-10-01T05:00:01Z',
    paymentMethod: { id: 'cash', name: 'cash' },
  });
  const debit = action(T2, 'AdjustBalance', 'completed', '2026-10-01T06:00:00Z', {
    amount: money(-5),
    reason: 'Correction',
  });
  const credit = action(T3, 'AdjustBalance', 'cancelled', '2026-10-01T05:30:00Z', {
    amount: money(7),
  });
  const hold = action(T4, 'ReserveBalance', 'created', '2026-10-01T07:00:00Z', {
    amount: money(10),
    reason: 'Pre-authorisation',
    validFor: { endDateTime: '2026-10-01T21:00:00Z' },
  });
  const sent = action(T5, 'TransferBalance', 'completed', '2026-10-01T08:00:00Z', {
    amount: money(30),
    receiverBucket: { id: RX_BUCKET },
    transferCost: { unit: 'USD', value: 1 },
    costOwner: 'originator',
    reason: 'Customer requested credit transfer',
    confirmationDate: '2026-10-01T08:00:05Z',
  });
  const received = action(T6, 'TransferBalance', 'completed', '2026-10-01T04:00:00Z', {
    amount: money(12),
    partyAccount: { id: B, name: 'Ivan Sidorov', '@referredType': 'BillingAccount' },
    bucket: { id: RX_BUCKET },
    receiverBucket: { id: BUCKET },
    transferCost: { unit: 'USD', value: 0 },
    costOwner: 'originator',
  });
  const refused = action(T7, 'AdjustBalance', 'failed', '2026-10-01T03:00:00Z', {
    amount: money(-500),
    reason: 'Insufficient spendable funds',
  });
  const bucket = () =>
    json([{ id: BUCKET, remainingValue: money(100), reservedValue: money(10) }], { total: 1 });
  const list =
    (...bodies: object[]) =>
    () =>
      json(bodies, { total: bodies.length });
  const routes = (over: Record<string, Handler> = {}): Record<string, Handler> => ({
    [`GET ${PREPAY}/bucket?partyAccount.id=${A}&offset=0&limit=100`]: bucket,
    [OWN]: list(topup, debit, credit, hold, sent, refused),
    [RECEIVED]: list(received, hold),
    [`GET ${PREPAY}/bucket/${RX_BUCKET}`]: () =>
      json({ id: RX_BUCKET, partyAccount: { id: B, name: 'Ivan Sidorov' } }),
    ...over,
  });
  const rowsOf = () => {
    selectTab('Balance actions');
    return within(
      document.querySelector('[data-ui="ui.subscriber.prepaid.history"]') as HTMLElement,
    )
      .getAllByRole('row')
      .slice(1)
      .map((r) => r.textContent ?? '');
  };

  it('shows the independently read accumulated balance in a KeyValue', async () => {
    const { calls } = backend(routes(), PREPAID);
    await open();
    selectTab('Balance actions');
    const total = await within(screen.getByRole('tabpanel')).findByText('123.45');
    expect(total.closest('dl')?.className).toBe('bss-key-value');
    expectUi('ui.subscriber.prepaid.accountTotal');
    expect(calls).toContain(
      `GET ${PREPAY}/accumulatedBalance?partyAccount.id=${A}&offset=0&limit=100`,
    );
  });

  it('merges the account and received-transfer requests, de-duplicated and newest first', async () => {
    const { calls } = backend(routes(), PREPAID);
    await open();
    expect(panel('Balance actions')).toBeTruthy();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    expect(calls).toContain(OWN);
    expect(calls).toContain(RECEIVED);
    const rows = rowsOf();
    expect(
      rows.map(
        (r) => r.match(/Top-up|Credit adj|Debit adj|Hold|Transfer \w+ [\w ]+?(?=[+−-]|\d)/)?.[0],
      ),
    ).toEqual([
      'Transfer to Ivan Sidorov',
      'Hold',
      'Debit adj',
      'Credit adj',
      'Top-up',
      'Transfer from Ivan Sidorov',
      'Debit adj',
    ]);
    expectUi('ui.subscriber.prepaid.history');
    // requested is the business date and time (Europe/Moscow, +03:00)
    expect(rows[0]).toContain('2026-10-01 11:00');
  });

  it('signs amounts from this account and states every status', async () => {
    backend(routes(), PREPAID);
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    const [toIvan, held, debit, cancelledCredit, topup, fromIvan, refused] = rowsOf();
    expect(toIvan).toContain('−30.00');
    expect(toIvan).toContain('Completed');
    expect(held).toContain('Held');
    expect(held).toContain('10.00');
    expect(held).not.toContain('−10.00');
    expect(held).not.toContain('+10.00');
    expect(debit).toContain('−5.00');
    expect(cancelledCredit).toContain('+7.00');
    expect(cancelledCredit).toContain('Cancelled');
    expect(topup).toContain('+50.00');
    expect(topup).toContain('Cash at the desk');
    expect(fromIvan).toContain('+12.00');
    expect(refused).toContain('Refused');
    expect(refused).toContain('Insufficient spendable funds');
  });

  it('labels a released hold and a pending transfer', async () => {
    backend(
      routes({
        [OWN]: list(
          { ...hold, status: 'cancelled' },
          { ...sent, status: 'created', confirmationDate: undefined },
        ),
        [RECEIVED]: list(),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(2));
    expect(rowsOf()[0]).toContain('Pending');
    expect(rowsOf()[1]).toContain('Released');
  });

  it('opens the detail fetched fresh, with links to both cards and the transfer cost', async () => {
    const { calls } = backend(
      routes({ [`GET ${HISTORY}/${T5}`]: () => json({ ...sent, description: 'Fresh copy' }) }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    fireEvent.click(within(panel('Balance actions')).getByText('Transfer to Ivan Sidorov'));
    const detail = await screen.findByRole('dialog', { name: 'Balance action' });
    expectUi('ui.subscriber.prepaid.historyDetail');
    expect(await within(detail).findByText('Fresh copy')).toBeTruthy();
    expect(calls).toContain(`GET ${HISTORY}/${T5}`);
    expect(within(detail).getByText('A5000000')).toBeTruthy();
    expect(within(detail).getByText('Customer requested credit transfer')).toBeTruthy();
    expect(within(detail).getByRole('link', { name: 'Ivan Sidorov' }).getAttribute('href')).toBe(
      `/subscribers/${B}`,
    );
    expect(within(detail).getByRole('link', { name: 'Olga Kuznetsova' }).getAttribute('href')).toBe(
      `/subscribers/${A}`,
    );
    expect(within(detail).getByText('1.00')).toBeTruthy();
    expect(within(detail).getByText('originator')).toBeTruthy();
  });

  it('shows the payment method of a top-up and the expiry of a hold', async () => {
    backend(
      routes({
        [`GET ${HISTORY}/${T1}`]: () => json(topup),
        [`GET ${HISTORY}/${T4}`]: () => json(hold),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    fireEvent.click(within(panel('Balance actions')).getByText('Top-up'));
    const first = await screen.findByRole('dialog', { name: 'Balance action' });
    expect(await within(first).findByText('Payment method')).toBeTruthy();
    expect(within(first).getAllByText('Cash').length).toBeGreaterThan(0);
    fireEvent.click(within(first).getByRole('button', { name: 'Close' }));
    fireEvent.click(within(panel('Balance actions')).getByText('Hold'));
    const second = await screen.findByRole('dialog', { name: 'Balance action' });
    expect(await within(second).findByText('Holds through')).toBeTruthy();
    expect(within(second).getByText('2026-10-01')).toBeTruthy();
  });

  it('says so when there are no balance actions, and when loading fails', async () => {
    backend(routes({ [OWN]: list(), [RECEIVED]: list() }), PREPAID);
    await open();
    await waitFor(() =>
      expect(within(panel('Balance actions')).getByText('No balance actions')).toBeTruthy(),
    );
  });

  it('reports a failing history request in place and keeps the card', async () => {
    backend(routes({ [OWN]: () => tmfError(500, 'database is down') }), PREPAID);
    await open();
    await waitFor(() =>
      expect(
        within(panel('Balance actions')).getByText('Failed to load the balance actions'),
      ).toBeTruthy(),
    );
    expect(screen.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeTruthy();
  });

  it('reloads the panel after a money form', async () => {
    const { fetchMock } = backend(
      routes({
        [`POST ${PREPAY}/topupBalance`]: () =>
          json({ id: 'x1', status: 'completed' }, { status: 201 }),
      }),
      PREPAID,
    );
    await open();
    await waitFor(() => expect(rowsOf()).toHaveLength(7));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Top up' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
    const form = await screen.findByRole('dialog', { name: 'Top up' });
    fireEvent.change(within(form).getByLabelText('Amount'), { target: { value: '5' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Cash' }));
    fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).startsWith(`${HISTORY}?partyAccount`)),
      ).toHaveLength(2),
    );
  });

  it('makes no TMF654 history request for a postpaid card', async () => {
    const { calls } = backend();
    await open();
    await waitFor(() => expect(screen.getByText('Transaction history')).toBeTruthy());
    expect(screen.queryByText('Balance actions')).toBeNull();
    expect(calls.filter((c) => c.includes('prepayBalanceManagement'))).toEqual([]);
  });

  it('labels the new transaction types', async () => {
    const entry = (id: string, type: string, note: string) => ({
      id,
      href: `/${id}`,
      type,
      date: '2026-10-01',
      note,
      period: null,
      amount: usd(1),
      balanceAfter: usd(1),
    });
    backend(
      routes({
        [`GET ${INTERNAL}/transactions?limit=20`]: () =>
          json(
            [
              entry('e1', 'transferOut', 'To Ivan Sidorov'),
              entry('e2', 'transferIn', 'From Ivan Sidorov'),
              entry('e3', 'reversal', 'Cancelled adjustment'),
              entry('e3', 'adjustment', 'Correction'),
            ],
            { total: 4 },
          ),
      }),
      PREPAID,
    );
    await open();
    const tx = panel('Transaction history');
    for (const label of ['Transfer out', 'Transfer in', 'Reversal', 'Adjustment'])
      expect(await within(tx).findByText(label)).toBeTruthy();
  });

  describe('writes on a balance action', () => {
    const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
      fetchMock.mock.calls
        .filter(([, init]) => init?.method === 'PATCH' || init?.method === 'DELETE')
        .map(([url, init]) => [init?.method, String(url), init?.body ? String(init.body) : null]);
    const detailOf = async (body: object, rowText: string, over: Record<string, Handler> = {}) => {
      const stub = backend(
        routes({
          [OWN]: list(body),
          [RECEIVED]: list(),
          [`GET ${HISTORY}/${(body as { id: string }).id}`]: () => json(body),
          ...over,
        }),
        PREPAID,
      );
      await open();
      await waitFor(() => expect(rowsOf()).toHaveLength(1));
      fireEvent.click(within(panel('Balance actions')).getByText(rowText));
      const dialog = await screen.findByRole('dialog', { name: 'Balance action' });
      await within(dialog).findByText('Kind');
      return { dialog, ...stub };
    };
    const buttons = (dialog: HTMLElement) =>
      within(dialog)
        .queryAllByRole('button')
        .filter((b) => !(b.getAttribute('aria-label') ?? '').startsWith('Copy '))
        .map((b) => b.textContent)
        .filter((t) => t !== '×');

    it('cancels a completed top-up after a confirmation, then reloads the card', async () => {
      const { dialog, fetchMock, calls } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => json({ ...topup, status: 'cancelled' }),
      });
      expect(buttons(dialog)).toEqual(['Cancel top-up', 'Edit note']);
      expectUi('ui.subscriber.prepaid.cancelTopup');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      expect(
        within(dialog).getByText(/Takes 50\.00 back off the balance\. Only possible while none/),
      ).toBeTruthy();
      expect(writes(fetchMock)).toEqual([]);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect(await screen.findByText('Top-up cancelled')).toBeTruthy();
      expect(writes(fetchMock)).toEqual([
        ['PATCH', `${PREPAY}/topupBalance/${T1}`, '{"status":"cancelled"}'],
      ]);
      await waitFor(() => expect(calls.filter((c) => c === OWN).length).toBeGreaterThan(1));
    });

    it('cancels an adjustment and releases a hold with the same body', async () => {
      const first = await detailOf(debit, 'Debit adjustment', {
        [`PATCH ${PREPAY}/adjustBalance/${T2}`]: () => json({ ...debit, status: 'cancelled' }),
      });
      expectUi('ui.subscriber.prepaid.cancelAdjustment');
      fireEvent.click(within(first.dialog).getByRole('button', { name: 'Cancel adjustment' }));
      expect(within(first.dialog).getByText('Reverses this debit of 5.00.')).toBeTruthy();
      fireEvent.click(within(first.dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(first.fetchMock)).toEqual([
          ['PATCH', `${PREPAY}/adjustBalance/${T2}`, '{"status":"cancelled"}'],
        ]),
      );
    });

    it('releases a hold', async () => {
      const { dialog, fetchMock } = await detailOf(hold, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json({ ...hold, status: 'cancelled' }),
      });
      expectUi('ui.subscriber.prepaid.releaseHold');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Release hold' }));
      expect(within(dialog).getByText('Makes 10.00 available again.')).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/reserveBalance/${T4}`,
            '{"status":"cancelled","reason":"Pre-authorisation"}',
          ],
        ]),
      );
    });

    it.each([false, true])(
      'keeps the release confirmation alive across an injected card refetch (failed=%s) and sends once',
      async (failed) => {
        let accountReads = 0;
        const { dialog, fetchMock, calls } = await detailOf(hold, 'Hold', {
          [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json({ ...hold, status: 'cancelled' }),
          [`POST ${PREPAY}/topupBalance`]: () => json(topup, { status: 201 }),
          [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () => {
            accountReads += 1;
            return failed && accountReads > 1 ? tmfError(503, 'Temporary failure') : json(PREPAID);
          },
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Release hold' }));
        // A separate completed write injects a real card reload while confirmation is open.
        fireEvent.click(screen.getByRole('button', { name: 'Top up' }));
        const form = await screen.findByRole('dialog', { name: 'Top up' });
        fireEvent.change(within(form).getByLabelText('Amount'), { target: { value: '1' } });
        fireEvent.click(within(form).getByRole('button', { name: /^Top up [\d,]+\.\d{2} USD$/ }));
        await waitFor(() => expect(calls.filter((call) => call === OWN).length).toBeGreaterThan(1));
        if (failed) {
          await screen.findByText('Failed to refresh the billing account');
          expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
        }
        expect(screen.getByRole('dialog', { name: 'Balance action' })).toBe(dialog);
        fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
        await waitFor(() =>
          expect(writes(fetchMock)).toEqual([
            [
              'PATCH',
              `${PREPAY}/reserveBalance/${T4}`,
              '{"status":"cancelled","reason":"Pre-authorisation"}',
            ],
          ]),
        );
        await waitFor(() =>
          expect(screen.queryByRole('dialog', { name: 'Balance action' })).toBeNull(),
        );
      },
    );

    it('releases a hold without a reason with a stated one, which ReserveBalance_Update requires', async () => {
      const bare = { ...hold, reason: undefined };
      const { dialog, fetchMock } = await detailOf(bare, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json({ ...bare, status: 'cancelled' }),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Release hold' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/reserveBalance/${T4}`,
            '{"status":"cancelled","reason":"Released by operator"}',
          ],
        ]),
      );
    });

    it('edits the note of a hold with its status and reason, and does not allow clearing the reason', async () => {
      const { dialog, fetchMock } = await detailOf(hold, 'Hold', {
        [`PATCH ${PREPAY}/reserveBalance/${T4}`]: () => json(hold),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Edit note' }));
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: '' } });
      const save = within(dialog).getByRole('button', { name: 'Save note' }) as HTMLButtonElement;
      expect(save.disabled).toBe(true);
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'Deposit' } });
      fireEvent.click(save);
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          ['PATCH', `${PREPAY}/reserveBalance/${T4}`, '{"status":"created","reason":"Deposit"}'],
        ]),
      );
    });

    it('removes a refused action with a DELETE and no body, on any path', async () => {
      const { dialog, fetchMock } = await detailOf(refused, 'Debit adjustment', {
        [`DELETE ${PREPAY}/adjustBalance/${T7}`]: () => new Response(null, { status: 204 }),
      });
      expect(buttons(dialog)).toEqual(['Remove']);
      expectUi('ui.subscriber.prepaid.removeFailed');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
      expect(
        within(dialog).getByText('Deletes this refused request. It moved no money.'),
      ).toBeTruthy();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([['DELETE', `${PREPAY}/adjustBalance/${T7}`, null]]),
      );
    });

    it('removes a refused transfer through transferBalance', async () => {
      const failedTransfer = { ...sent, status: 'failed' };
      const { dialog, fetchMock } = await detailOf(failedTransfer, 'Transfer to Ivan Sidorov', {
        [`DELETE ${PREPAY}/transferBalance/${T5}`]: () => new Response(null, { status: 204 }),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([['DELETE', `${PREPAY}/transferBalance/${T5}`, null]]),
      );
    });

    it('offers the buttons the kind and status allow', async () => {
      const done = await detailOf({ ...credit, status: 'completed' }, 'Credit adjustment');
      expect(buttons(done.dialog)).toEqual(['Cancel adjustment', 'Edit note']);
    });

    it('offers nothing on a completed transfer and only Edit note on a cancelled top-up', async () => {
      const transfer = await detailOf(sent, 'Transfer to Ivan Sidorov');
      expect(buttons(transfer.dialog)).toEqual([]);
    });

    it('offers only Edit note on a cancelled top-up', async () => {
      const { dialog } = await detailOf({ ...topup, status: 'cancelled' }, 'Top-up');
      expect(buttons(dialog)).toEqual(['Edit note']);
    });

    it('sends only the changed note fields, an emptied one as null', async () => {
      const { dialog, fetchMock } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => json(topup),
      });
      expectUi('ui.subscriber.prepaid.editNote');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Edit note' }));
      expectUi('ui.subscriber.prepaid.editNoteForm');
      const save = within(dialog).getByRole('button', { name: 'Save note' }) as HTMLButtonElement;
      expect(save.disabled).toBe(true);
      fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: '' } });
      fireEvent.change(within(dialog).getByLabelText('Description'), {
        target: { value: ' Checked ' },
      });
      fireEvent.click(save);
      await waitFor(() =>
        expect(writes(fetchMock)).toEqual([
          [
            'PATCH',
            `${PREPAY}/topupBalance/${T1}`,
            '{"status":"completed","reason":null,"description":"Checked"}',
          ],
        ]),
      );
    });

    it('shows a 409 in the dialog and keeps it open', async () => {
      const { dialog, fetchMock } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => tmfError(409, 'The top-up was partly spent'),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect((await within(dialog).findByRole('alert')).textContent).toContain(
        'The top-up was partly spent',
      );
      expect(screen.getByRole('dialog', { name: 'Balance action' })).toBeTruthy();
      expect(writes(fetchMock)).toHaveLength(1);
      expect(within(dialog).queryByRole('button', { name: 'Refresh' })).toBeNull();
    });

    it('says it is unsure after a 5xx and offers Refresh', async () => {
      const { dialog } = await detailOf(topup, 'Top-up', {
        [`PATCH ${PREPAY}/topupBalance/${T1}`]: () => tmfError(500, 'database is down'),
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel top-up' }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
      expect((await within(dialog).findByRole('alert')).textContent).toContain(
        'Unsure whether it was saved',
      );
      expect(within(dialog).getByRole('button', { name: 'Refresh' })).toBeTruthy();
    });
  });
});

describe('prepaid Resume funding gate', () => {
  it('waits for the server preview and hands its shortfall to Top up without ordering', async () => {
    const { fetchMock } = backend(
      {
        [`GET ${INTERNAL}/products`]: () =>
          json(
            [
              product(P1, {
                status: 'suspended',
                suspensionCause: 'manual',
                paidThrough: '2026-10-01',
              }),
            ],
            { total: 1 },
          ),
        [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
          () =>
            json(
              [
                {
                  id: BUCKET,
                  remainingValue: { amount: 5, units: 'USD' },
                  reservedValue: { amount: 0, units: 'USD' },
                },
              ],
              { total: 1 },
            ),
        [`GET ${INTERNAL}/firstCharge?productOffering.id=${O1}&startDate=2026-10-01`]: () =>
          json({ ...FIRST_CHARGE, required: usd(585), available: usd(5), shortfall: usd(580) }),
      },
      accountBody(A, { id: C, name: 'Olga Kuznetsova' }, { ratingType: 'prepaid' }),
    );
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(productAction(row, 'Resume…'));
    const dialog = screen.getByRole('dialog', { name: 'Resume: Mobile L' });
    expect(
      (within(dialog).getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    const topup = await within(dialog).findByRole('button', { name: 'Top up 580.00' });
    expectUi('ui.subscriber.resumePreview');
    expect(
      (within(dialog).getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.click(topup);
    const form = await screen.findByRole('dialog', { name: 'Top up' });
    expect((within(form).getByLabelText('Amount') as HTMLInputElement).value).toBe('580.00');
  });
});

describe('prepaid plans (#212)', () => {
  const ADDON = 'o0000009-0000-4000-8000-000000000009';
  const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
  const FAILED = `GET /internal/v1/prepaid/command?billingAccount.id=${A}&state=failedTechnical&offset=0&limit=100`;
  const PREPAID = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { ratingType: 'prepaid', accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }] },
  );
  const plan = product(P1, {
    offeringKind: 'plan',
    fundingSource: 'balanceThenCard',
    parentProduct: null,
    paidThrough: '2026-10-20',
    nextChargeDate: '2026-10-20',
    renewalHistory: [
      {
        authorizedAt: '2026-09-20T09:00:00Z',
        period: { start: '2026-09-20', end: '2026-10-20' },
        kind: 'renewal',
        amount: usd(12),
        tax: usd(2),
        fundedBy: 'card',
        bill: { id: 'b0000001-0000-4000-8000-000000000001' },
      },
    ],
  });
  const pack = product(P2, {
    offeringKind: 'addOn',
    fundingSource: 'balance',
    parentProduct: { id: 'abcd1234-0000-4000-8000-0000000000ff', href: '/p' },
    terminationDate: '2026-10-20',
    price: null,
    nextChargeDate: null,
    productOffering: { id: ADDON, name: 'Extra 2 GB', code: null },
  });
  const quote = {
    billingAccount: { id: A },
    productOffering: { id: ADDON, name: 'Extra 2 GB' },
    startDate: '2026-10-01',
    period: { start: '2026-10-01', end: '2026-10-02' },
    cyclePeriod: { start: '2026-10-01', end: '2026-10-02' },
    billableDays: 1,
    periodDays: 1,
    prorated: false,
    price: usd(0),
    amount: usd(4.8),
    chargeRunDate: '2026-10-01',
    billDate: '2026-10-01',
    next: null,
    balance: usd(3),
    balanceAfter: usd(-1.8),
    creditLimit: null,
    exceedsCreditLimit: true,
    required: usd(4.8),
    available: usd(3),
    shortfall: usd(1.8),
    fees: [{ name: 'Extra 2 GB', amount: usd(4), tax: usd(0.8), gross: usd(4.8) }],
  };
  const routes = (extra: Record<string, Handler> = {}) =>
    backend(
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
        [FAILED]: () => json([], { total: 0 }),
        [`GET ${INTERNAL}/products`]: () => json([plan, pack], { total: 2 }),
        'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
          () =>
            json(
              [
                { id: O1, name: 'Plan 10', '@type': 'ProductOffering' },
                {
                  id: ADDON,
                  name: 'Extra 2 GB',
                  productOfferingCharacteristic: [
                    {
                      name: 'offeringKind',
                      characteristicValueSpecification: [{ value: 'addOn' }],
                    },
                  ],
                  '@type': 'ProductOffering',
                },
              ],
              { total: 2 },
            ),
        ...extra,
      },
      PREPAID,
    );

  it('shows the kind, funding source, add-on parent and end date, and the renewal history', async () => {
    routes();
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(
      within(row).getByText(/Plan · Renews from the balance, then the default card/),
    ).toBeTruthy();
    expect(within(row).getByText(/on 2026-10-20/)).toBeTruthy();
    const addOn = productRow('Extra 2 GB');
    expect(within(addOn).getByText(/Add-on · plan PRD-ABCD1234 · ends 2026-10-20/)).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: /renewal history \(1\)/ }));
    const table = within(row).getByRole('table', { name: 'Renewal history' });
    expect(table.textContent).toContain('2026-09-20 – 2026-10-20');
    expect(table.textContent).toContain('Card');
    expect(table.textContent).toContain('12.00 USD');
  });

  it('offers Buy add-on on a plan only, quotes the fee and orders the add item', async () => {
    const { fetchMock } = routes({
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${ADDON}&startDate=2026-10-01`]: () =>
        json(quote),
      [`POST ${ORDERS}`]: () =>
        json({ id: 'o1', state: 'completed', '@type': 'ProductOrder' }, { status: 201 }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(productAction(row, 'Buy add-on…')).toBe(expectUi('ui.subscriber.buyAddOnItem'));
    const packRow = productRow('Extra 2 GB');
    fireEvent.click(within(packRow).getByRole('button', { name: 'Actions ▾' }));
    expect(
      within(productMenu(packRow)).queryByRole('menuitem', { name: 'Buy add-on…' }),
    ).toBeNull();
    fireEvent.click(productAction(row, 'Buy add-on…'));
    const dialog = await screen.findByRole('dialog', { name: /Buy add-on/ });
    // only the add-on offering is listed, not the plan
    const radios = await within(dialog).findAllByRole('radio');
    expect(radios).toHaveLength(1);
    fireEvent.click(radios[0] as HTMLElement);
    expect((await within(dialog).findByTestId('addon-required')).textContent).toContain('4.80 USD');
    expect(within(dialog).getByText(/4\.00 \+ tax 0\.80 = 4\.80 USD/)).toBeTruthy();
    expect(within(dialog).getByText(/Insufficient funds: top up at least 1\.80/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Buy add-on' }));
    await screen.findByRole('status');
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      '@type': 'ProductOrder',
      billingAccount: { id: A, '@type': 'BillingAccountRef' },
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'add',
          productOffering: { id: ADDON, '@type': 'ProductOfferingRef' },
          product: {
            '@type': 'ProductRefOrValue',
            productRelationship: [
              { '@type': 'ProductRelationship', relationshipType: 'addOn', id: P1 },
            ],
          },
        },
      ],
    });
  });

  it('shows the readable reason when the server rejects the add-on', async () => {
    routes({
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${ADDON}&startDate=2026-10-01`]: () =>
        json(quote),
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o1',
            state: 'rejected',
            productOrderErrorMessage: [{ code: 'addOnParentNotActive', reason: 'raw' }],
            '@type': 'ProductOrder',
          },
          { status: 201 },
        ),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    fireEvent.click(productAction(row, 'Buy add-on…'));
    const dialog = await screen.findByRole('dialog', { name: /Buy add-on/ });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    await within(dialog).findByTestId('addon-required');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Buy add-on' }));
    expect(await within(dialog).findByText(/no paid period running today/)).toBeTruthy();
  });
});
