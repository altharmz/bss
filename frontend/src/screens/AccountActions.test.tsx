import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BILL_DEMANDS, REFUNDS, type Account } from '../api/bss';
import { SystemContextProvider } from '../app/context';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, pending, stubFetch, tmfError } from '../test/fetch';
import { accountBody, NO_SETTLEMENT } from '../test/fixtures';
import { AccountBills, RefundAction, RefundModal, RefundsPanel } from './AccountActions';

const A = 'aaaa1111-0000-4000-8000-0000000000a1';
const account: Account = {
  id: A,
  name: 'Olga main',
  state: 'Closed',
  customerId: 'c1',
  customerName: 'Olga',
  cycleDay: 1,
  currency: 'RUB',
  balanceCents: 10025,
  creditLimitCents: null,
  financialAccount: null,
};
const credit = { ...NO_SETTLEMENT, unallocatedCredit: { unit: 'RUB', value: 150 } };
const DEMANDS = `GET ${BILL_DEMANDS}?billingAccount.id=${A}&offset=0&limit=100`;
const REFUND_LIST = `GET ${REFUNDS}?account.id=${A}&offset=0&limit=100`;
const demand = (state = 'inProgress') => ({ id: 'd1', state, lastUpdate: '2026-09-30T21:30:00Z' });
afterEach(() => vi.useRealTimers());

function billView(onCompleted = vi.fn(), onOpen = vi.fn()) {
  const view = render(
    <SystemContextProvider value={CONTEXT}>
      <AccountBills
        account={account}
        version={0}
        bills={{ status: 'ok', data: [], retry: vi.fn() }}
        onCompleted={onCompleted}
        onOpen={onOpen}
      />
    </SystemContextProvider>,
  );
  return { ...view, onCompleted, onOpen };
}

async function until(check: () => boolean) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await act(async () => {
      await Promise.resolve();
    });
  }
  throw new Error('condition not reached');
}

describe('refunds', () => {
  it('offers the lesser credit on closed accounts and pins the exact refund request', async () => {
    const { fetchMock } = stubFetch({ [`POST ${REFUNDS}`]: () => json({ id: 'r1' }) });
    const changed = vi.fn();
    render(<RefundAction account={account} settlement={credit} onChanged={changed} />);
    expectUi('ui.subscriber.recordRefund');
    fireEvent.click(screen.getByRole('button', { name: 'Refund unused credit' }));
    expect((screen.getByLabelText('Refund amount') as HTMLInputElement).value).toBe('100.25');
    expect(screen.getByText(/does not send money/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Refund amount'), { target: { value: '25.10' } });
    fireEvent.change(screen.getByLabelText('Refund description'), {
      target: { value: ' Returned deposit ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Record refund' }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      `{"@type":"Refund","account":{"id":"${A}","@referredType":"BillingAccount"},"totalAmount":{"unit":"RUB","value":25.10},"paymentMethod":{"name":"bankTransfer"},"description":"Returned deposit"}`,
    );
  });

  it.each(['0', '-1', '100.26', '0.001', 'no'])('refuses invalid/excessive amount %s', (value) => {
    stubFetch({});
    render(
      <RefundModal account={account} initialLimit={10025} onClose={vi.fn()} onRecorded={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText('Refund amount'), { target: { value } });
    expect(
      (screen.getByRole('button', { name: 'Record refund' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('requires an Other method name and prevents duplicate submissions and closing while saving', async () => {
    const { calls } = stubFetch({ [`POST ${REFUNDS}`]: pending });
    const close = vi.fn();
    render(
      <RefundModal account={account} initialLimit={10025} onClose={close} onRecorded={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText('Refund method'), { target: { value: 'other' } });
    const save = screen.getByRole('button', { name: 'Record refund' });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Refund method name'), { target: { value: 'Cheque' } });
    fireEvent.click(save);
    fireEvent.click(save);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(calls).toEqual([`POST ${REFUNDS}`]);
    expect(close).not.toHaveBeenCalled();
  });

  it('keeps fields after a credit conflict and refreshes the refundable limit', async () => {
    stubFetch({
      [`POST ${REFUNDS}`]: () => tmfError(409, 'refund exceeds refundable credit 10'),
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () =>
        json(
          accountBody(
            A,
            { id: 'c1', name: 'Olga' },
            { accountBalance: [{ amount: { unit: 'RUB', value: 10 } }] },
          ),
        ),
      [`GET /internal/v1/billingAccount/${A}/settlement`]: () => json(credit),
    });
    render(
      <RefundModal account={account} initialLimit={10025} onClose={vi.fn()} onRecorded={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Record refund' }));
    await screen.findByText('Refundable credit 10.00 RUB');
    expect((screen.getByLabelText('Refund amount') as HTMLInputElement).value).toBe('100.25');
    expect(
      (screen.getByRole('button', { name: 'Record refund' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('blocks resubmission after an uncertain network outcome', async () => {
    stubFetch({
      [`POST ${REFUNDS}`]: () => {
        throw new Error('disconnected');
      },
    });
    render(
      <RefundModal account={account} initialLimit={10025} onClose={vi.fn()} onRecorded={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Record refund' }));
    await screen.findByText(/check Recorded refunds/);
    expect(
      (screen.getByRole('button', { name: 'Record refund' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('shows all refund pages in the business timezone with metadata', async () => {
    stubFetch({
      [REFUND_LIST]: () =>
        json(
          [
            {
              id: 'r1',
              refundDate: '2026-09-30T21:30:00Z',
              totalAmount: { unit: 'RUB', value: 25.1 },
              paymentMethod: { name: 'cash' },
              description: 'Returned deposit',
            },
          ],
          { total: 2 },
        ),
      [`GET ${REFUNDS}?account.id=${A}&offset=1&limit=100`]: () =>
        json(
          [
            {
              id: 'r2',
              refundDate: '2026-10-01T09:00:00Z',
              totalAmount: { unit: 'RUB', value: 10 },
              paymentMethod: { name: 'card' },
            },
          ],
          { total: 2 },
        ),
    });
    render(
      <SystemContextProvider value={CONTEXT}>
        <RefundsPanel accountId={A} version={0} />
      </SystemContextProvider>,
    );
    await screen.findByText('Returned deposit');
    expectUi('ui.subscriber.refunds');
    expect(screen.getByText('2026-10-01 00:30:00')).toBeTruthy();
    expect(screen.getByText('25.10 RUB')).toBeTruthy();
    expect(screen.getByText('card')).toBeTruthy();
  });

  it('disables refunds before settlement loads or when net credit is zero', () => {
    stubFetch({});
    const view = render(<RefundAction account={account} settlement={null} onChanged={vi.fn()} />);
    expect(
      (screen.getByRole('button', { name: 'Refund unused credit' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    view.rerender(
      <RefundAction
        account={{ ...account, balanceCents: -10 }}
        settlement={credit}
        onChanged={vi.fn()}
      />,
    );
    expect(
      (screen.getByRole('button', { name: 'Refund unused credit' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe('bill requests', () => {
  it('pins the request on a closed account and disables duplicates', async () => {
    let created = false;
    const { fetchMock } = stubFetch({
      [DEMANDS]: () => json(created ? [demand()] : [], { total: created ? 1 : 0 }),
      [`POST ${BILL_DEMANDS}`]: () => {
        created = true;
        return json(demand());
      },
    });
    billView();
    await screen.findByText('No bill requests');
    expectUi('ui.subscriber.billNow');
    expectUi('ui.subscriber.billRequests');
    fireEvent.click(screen.getByRole('button', { name: 'Bill now' }));
    const dialog = screen.getByRole('dialog', { name: 'Bill now' });
    expect(within(dialog).getByText(/from tomorrow onward/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Request bill' }));
    await screen.findByText('In progress');
    const post = fetchMock.mock.calls.find((c) => c[1]?.method === 'POST');
    expect(post?.[1]?.body).toBe(
      `{"@type":"CustomerBillOnDemand","billingAccount":{"id":"${A}","@referredType":"BillingAccount"}}`,
    );
    expect((screen.getByRole('button', { name: 'Bill now' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('refreshes after conflicts and retains the server explanation', async () => {
    let conflict = false;
    stubFetch({
      [DEMANDS]: () => json(conflict ? [demand()] : [], { total: conflict ? 1 : 0 }),
      [`POST ${BILL_DEMANDS}`]: () => {
        conflict = true;
        return tmfError(409, 'already in progress');
      },
    });
    billView();
    await screen.findByText('No bill requests');
    fireEvent.click(screen.getByRole('button', { name: 'Bill now' }));
    fireEvent.click(screen.getByRole('button', { name: 'Request bill' }));
    await screen.findByText('In progress');
    expect(screen.getByText(/409 already in progress/)).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Request bill' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('requires checking status after an uncertain write', async () => {
    stubFetch({
      [DEMANDS]: () => json([], { total: 0 }),
      [`POST ${BILL_DEMANDS}`]: () => tmfError(502, 'proxy failed'),
    });
    billView();
    await screen.findByText('No bill requests');
    fireEvent.click(screen.getByRole('button', { name: 'Bill now' }));
    fireEvent.click(screen.getByRole('button', { name: 'Request bill' }));
    await screen.findByText(/refresh Bill requests before/);
    expect(
      (screen.getByRole('button', { name: 'Request bill' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it.each(['rejected', 'failed'])(
    'shows %s explanations and permits another request',
    async (state) => {
      stubFetch({
        [DEMANDS]: () =>
          json([{ ...demand(state), description: 'No unbilled charges remain' }], { total: 1 }),
      });
      billView();
      await screen.findByText('No unbilled charges remain');
      expect((screen.getByRole('button', { name: 'Bill now' }) as HTMLButtonElement).disabled).toBe(
        false,
      );
    },
  );

  it('polls to completion, refreshes the card and opens the resulting bill', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let done = false;
    stubFetch({
      [DEMANDS]: () =>
        json([done ? { ...demand('done'), customerBill: { id: 'b1' } } : demand()], { total: 1 }),
    });
    const { onCompleted, onOpen } = billView();
    await until(() => screen.queryByText('In progress') !== null);
    done = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await until(() => screen.queryByText('Done') !== null);
    expect(onCompleted).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Open bill' }));
    expect(onOpen).toHaveBeenCalledWith('b1');
  });

  it('stops after three minutes, offers Refresh and cancels timers on unmount', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { calls } = stubFetch({ [DEMANDS]: () => json([demand()], { total: 1 }) });
    const view = billView();
    await until(() => screen.queryByText('In progress') !== null);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    await until(() => screen.queryByText(/Processing continues/) !== null);
    const count = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(calls).toHaveLength(count);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await until(() => calls.length > count);
    view.unmount();
    const last = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(calls).toHaveLength(last);
  });

  it('disables Bill now on a load error and recovers with Retry', async () => {
    let failed = true;
    stubFetch({
      [DEMANDS]: () => (failed ? tmfError(503, 'unavailable') : json([], { total: 0 })),
    });
    billView();
    await screen.findByText('Failed to load bill requests');
    expect((screen.getByRole('button', { name: 'Bill now' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    failed = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('No bill requests');
    expect((screen.getByRole('button', { name: 'Bill now' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });
});
