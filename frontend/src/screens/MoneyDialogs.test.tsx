import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { toAccount } from '../api/bss';
import { SystemContextProvider } from '../app/context';
import { CONTEXT, json, stubFetch, tmfError } from '../test/fetch';
import { accountBody } from '../test/fixtures';
import { expectUi } from '../test/expectUi';
import { PrepaidMoneyModal, type MoneyForm } from './PrepaidMoney';
import { RecordPaymentModal } from './Bills';

const account = toAccount(
  accountBody('a0000001-0000-4000-8000-000000000001', {
    id: 'customer',
    name: 'Ada',
  }) as Parameters<typeof toAccount>[0],
);
const balance = {
  bucketId: 'bucket',
  ledgerCents: 12000,
  reservedCents: 2000,
  availableCents: 10000,
};

describe('Ledger money outcomes', () => {
  it.each(['0', '-1', '-0.001', '−2.123', '0.000', '0,000.00', '000,000.000'])(
    'explains the sign before decimals for %s',
    (value) => {
      stubFetch({});
      render(
        <RecordPaymentModal account={account} bills={[]} onClose={vi.fn()} onRecorded={vi.fn()} />,
      );
      fireEvent.change(screen.getByLabelText('Amount received'), { target: { value } });
      expect(screen.getByRole('alert').textContent).toContain('Amount must be greater than 0');
      expect(screen.queryByText('Enter an amount with at most 2 decimals.')).toBeNull();
      expect(screen.getByRole('button', { name: 'Record payment' })).toHaveProperty(
        'disabled',
        true,
      );
    },
  );

  it('requires an adjustment reason before writing', () => {
    const { calls } = stubFetch({});
    render(
      <SystemContextProvider value={CONTEXT}>
        <PrepaidMoneyModal
          form="adjust"
          account={account}
          balance={balance}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </SystemContextProvider>,
    );
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '5' } });
    expectUi('ui.subscriber.prepaid.reason');
    expect(screen.getByText('Enter a reason.')).toBeTruthy();
    const button = screen.getByRole('button', { name: 'Apply adjustment' });
    expect(button).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '   ' } });
    fireEvent.click(button);
    expect(calls).toEqual([]);
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Correction' } });
    expect(button).toHaveProperty('disabled', false);
  });

  it('tops up an optional active number with the exact decimal body', async () => {
    const { fetchMock } = stubFetch({
      [`GET /tmf-api/productInventory/v5/product?billingAccount.id=${account.id}&status=active&offset=0&limit=100`]:
        () => json([{ realizingResource: [{ id: 'number' }] }], { total: 1 }),
      'GET /tmf-api/resourceInventory/v5/resource/number': () =>
        json({ id: 'number', value: '12025550123' }),
      'POST /tmf-api/prepayBalanceManagement/v4/topupBalance': () =>
        json({ id: 'topup', status: 'completed' }),
    });
    const done = vi.fn();
    render(
      <SystemContextProvider value={CONTEXT}>
        <PrepaidMoneyModal
          form="topup"
          account={account}
          balance={balance}
          onClose={vi.fn()}
          onDone={done}
        />
      </SystemContextProvider>,
    );
    await screen.findByRole('option', { name: '12025550123' });
    expectUi('ui.subscriber.prepaid.topup.number');
    expect(screen.queryByText(/voucher/i)).toBeNull();
    fireEvent.change(screen.getByLabelText('Number'), { target: { value: 'number' } });
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '1,250.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cash' }));
    fireEvent.click(screen.getByRole('button', { name: 'Top up 1,250.50 USD' }));
    await waitFor(() => expect(done).toHaveBeenCalledOnce());
    const sent = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(sent?.[1]?.body).toBe(
      `{"@type":"TopupBalance","amount":{"amount":1250.50,"units":"USD"},"usageType":"monetary","bucket":{"id":"bucket"},"partyAccount":{"id":"${account.id}","@referredType":"BillingAccount"},"paymentMethod":{"id":"cash","name":"cash"},"logicalResource":[{"id":"number"}]}`,
    );
  });

  it('records the external reference and description without a value date', async () => {
    const { fetchMock } = stubFetch({
      'POST /tmf-api/payment/v4/payment': () => json({ id: 'payment' }),
    });
    render(
      <RecordPaymentModal account={account} bills={[]} onClose={vi.fn()} onRecorded={vi.fn()} />,
    );
    expectUi('ui.subscriber.recordPayment.details');
    fireEvent.change(screen.getByLabelText('Amount received'), { target: { value: '1250.50' } });
    fireEvent.change(screen.getByLabelText('External reference'), {
      target: { value: ' BANK-77 ' },
    });
    fireEvent.change(screen.getByLabelText('Payment description'), {
      target: { value: ' Cash at the desk ' },
    });
    expect(screen.queryByLabelText(/value date/i)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Record 1,250.50 USD' }));
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      `{"@type":"Payment","account":{"id":"${account.id}","@referredType":"BillingAccount"},"totalAmount":{"unit":"USD","value":1250.50},"paymentMethod":{"name":"bankTransfer"},"correlatorId":"BANK-77","description":"Cash at the desk"}`,
    );
  });
  it.each<[MoneyForm, string, string, string]>([
    ['topup', 'Top up', 'Top up 5.00 USD', '125.00'],
    ['adjust', 'Apply adjustment', 'Apply adjustment', '125.00'],
    ['hold', 'Hold', 'Hold 5.00 USD', '95.00'],
  ])('%s shows outcome labels and the loaded balance after', (form, empty, filled, after) => {
    stubFetch({
      [`GET /tmf-api/productInventory/v5/product?billingAccount.id=${account.id}&status=active&offset=0&limit=100`]:
        () => json([], { total: 0 }),
    });
    render(
      <SystemContextProvider value={CONTEXT}>
        <PrepaidMoneyModal
          form={form}
          account={account}
          balance={balance}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </SystemContextProvider>,
    );
    const dialog = screen.getByRole('dialog');
    if (form === 'adjust')
      fireEvent.change(within(dialog).getByLabelText('Reason'), {
        target: { value: 'Correction' },
      });
    expectUi('ui.subscriber.prepaid.balanceAfter');
    expect(within(dialog).getByRole('button', { name: empty })).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText('Amount'), { target: { value: '5' } });
    expect(within(dialog).getByRole('button', { name: filled })).toHaveProperty('disabled', false);
    expect(within(dialog).getByText(after)).toBeTruthy();
    if (form === 'adjust') {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Debit' }));
      expect(within(dialog).getByText('115.00')).toBeTruthy();
    }
  });

  it('records the received amount and refreshes an unsure result without another Record action', async () => {
    stubFetch({ 'POST /tmf-api/payment/v4/payment': () => tmfError(503, 'Unavailable') });
    const refreshed = vi.fn();
    render(
      <RecordPaymentModal account={account} bills={[]} onClose={vi.fn()} onRecorded={refreshed} />,
    );
    expectUi('ui.subscriber.recordPayment.balances');
    expect(screen.getByRole('button', { name: 'Record payment' })).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText('Amount received'), { target: { value: '5' } });
    expect(within(expectUi('ui.subscriber.recordPayment.balances')).getByText('5.00')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Record 5.00 USD' }));
    await screen.findByText('Unsure whether it was saved.');
    expect(screen.queryByRole('button', { name: /^Record/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(refreshed).toHaveBeenCalledWith('Refreshed');
  });

  it('locks Record payment while busy and keeps a refusal editable', async () => {
    let respond: ((response: Response) => void) | undefined;
    stubFetch({
      'POST /tmf-api/payment/v4/payment': () =>
        new Promise<Response>((resolve) => {
          respond = resolve;
        }),
    });
    render(
      <RecordPaymentModal account={account} bills={[]} onClose={vi.fn()} onRecorded={vi.fn()} />,
    );
    const amount = screen.getByLabelText('Amount received');
    fireEvent.change(amount, { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record 5.00 USD' }));
    expect(screen.getByRole('button', { name: 'Recording…' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveProperty('disabled', true);
    expect((amount as HTMLInputElement).matches(':disabled')).toBe(true);
    respond?.(tmfError(409, 'Payment refused'));
    expect((await screen.findByRole('alert')).textContent).toContain('Payment refused');
    expect((amount as HTMLInputElement).matches(':disabled')).toBe(false);
    expect(amount).toHaveProperty('value', '5');
    expect(screen.getByRole('button', { name: 'Record 5.00 USD' })).toHaveProperty(
      'disabled',
      false,
    );
  });
});
