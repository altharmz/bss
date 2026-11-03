import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { toAccount } from '../api/bss';
import { SystemContextProvider } from '../app/context';
import { CONTEXT, stubFetch, tmfError } from '../test/fetch';
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
  it.each<[MoneyForm, string, string, string]>([
    ['topup', 'Top up', 'Top up 5.00 USD', '125.00'],
    ['adjust', 'Apply adjustment', 'Apply adjustment', '125.00'],
    ['hold', 'Hold', 'Hold 5.00 USD', '95.00'],
  ])('%s shows outcome labels and the loaded balance after', (form, empty, filled, after) => {
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
