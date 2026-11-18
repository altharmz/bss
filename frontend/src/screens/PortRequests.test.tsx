import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PortRequestsScreen } from './PortRequests';
import { PoolDialog } from './PoolDialog';
import { TransferPinDialog } from './TransferPin';
import { CrumbsProvider, SystemContextProvider } from '../app/context';
import { RouterProvider } from '../app/router';
import { CONTEXT, json, stubFetch, tmfError } from '../test/fetch';
import { ToastProvider } from '../ui/kit';
import { expectUi } from '../test/expectUi';

const PR = '30000000-0000-4000-8000-000000000001';
const PO = '30000000-0000-4000-8000-000000000002';
const PRODUCT = '30000000-0000-4000-8000-000000000003';
const base = {
  areaCode: '555',
  carrierAccountNumber: 'ACC-1',
  billingZip: '12345',
  firstName: 'Ada',
  lastName: 'Lovelace',
  carrierReference: null,
  submissions: 1,
  attempts: 0,
  nextAttemptAt: null,
  lastError: null,
  resource: { id: 'r1' },
  product: null,
  billingAccount: null,
  productOrder: null,
  createdAt: '2026-11-06T10:00:00Z',
  updatedAt: '2026-11-06T10:00:00Z',
  completedAt: null,
};
const rejected = {
  ...base,
  id: PR,
  direction: 'in',
  state: 'rejected',
  number: '15551230001',
  rejectionCode: 'pinMismatch',
  rejectionReason: 'The PIN does not match',
};
const out = {
  ...base,
  id: PO,
  direction: 'out',
  state: 'completed',
  number: '15551230002',
  rejectionCode: null,
  rejectionReason: null,
};
const LIST = 'GET /internal/v1/port?offset=0&limit=100';

function screenOf() {
  return render(
    <RouterProvider>
      <SystemContextProvider value={CONTEXT}>
        <CrumbsProvider>
          <ToastProvider>
            <PortRequestsScreen />
          </ToastProvider>
        </CrumbsProvider>
      </SystemContextProvider>
    </RouterProvider>,
  );
}

describe('port requests', () => {
  it('lists the requests with state and reason and filters them on the server', async () => {
    const { calls } = stubFetch({
      [LIST]: () => json([rejected, out], { total: 2 }),
      'GET /internal/v1/port?direction=in&offset=0&limit=100': () => json([rejected], { total: 1 }),
      'GET /internal/v1/port?direction=in&state=rejected&offset=0&limit=100': () =>
        json([rejected], { total: 1 }),
    });
    screenOf();
    expectUi('ui.ports.list');
    expect(await screen.findByText(/The PIN does not match \(pinMismatch\)/)).toBeTruthy();
    expect(screen.getAllByText('Port-out').length).toBe(2);
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'in' } });
    await waitFor(() => expect(screen.queryByText('15551230002')).toBeNull());
    fireEvent.change(screen.getByLabelText('State'), { target: { value: 'rejected' } });
    await waitFor(() =>
      expect(
        calls.includes('GET /internal/v1/port?direction=in&state=rejected&offset=0&limit=100'),
      ).toBe(true),
    );
  });

  it('resubmits with the PIN again and only the changed fields', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([rejected], { total: 1 }),
      [`GET /internal/v1/port/${PR}`]: () => json(rejected),
      [`POST /internal/v1/port/${PR}/resubmit`]: () =>
        json({ ...rejected, state: 'submitted' }, { status: 202 }),
    });
    screenOf();
    fireEvent.click(await screen.findByRole('button', { name: '15551230001' }));
    const dialog = screen.getByRole('dialog');
    expectUi('ui.ports.detail');
    const correct = await within(dialog).findByText('Correct and resubmit');
    expect(correct).toBe(expectUi('ui.ports.resubmit'));
    fireEvent.click(correct);
    const submit = within(dialog).getByText('Resubmit');
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Number Transfer PIN'), {
      target: { value: '123456' },
    });
    fireEvent.change(within(dialog).getByLabelText('Billing ZIP'), { target: { value: '54321' } });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      transferPin: '123456',
      billingZip: '54321',
    });
  });

  it('cancels an open port-in and keeps a refusal in the dialog', async () => {
    const pending = { ...rejected, state: 'pending', rejectionCode: null, rejectionReason: null };
    let refuse = true;
    stubFetch({
      [LIST]: () => json([pending], { total: 1 }),
      [`GET /internal/v1/port/${PR}`]: () => json(pending),
      [`POST /internal/v1/port/${PR}/cancel`]: () =>
        refuse
          ? tmfError(409, 'an order holds this number')
          : json({ ...pending, state: 'cancelled' }),
    });
    screenOf();
    fireEvent.click(await screen.findByRole('button', { name: '15551230001' }));
    const dialog = screen.getByRole('dialog');
    const cancel = await within(dialog).findByText('Cancel port-in');
    expectUi('ui.ports.cancel');
    fireEvent.click(cancel);
    expect(await within(dialog).findByText(/an order holds this number/)).toBeTruthy();
    refuse = false;
    fireEvent.click(cancel);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('offers no actions on a completed port-out', async () => {
    stubFetch({
      [LIST]: () => json([out], { total: 1 }),
      [`GET /internal/v1/port/${PO}`]: () => json(out),
    });
    screenOf();
    fireEvent.click(await screen.findByRole('button', { name: '15551230002' }));
    const dialog = screen.getByRole('dialog');
    await within(dialog).findByText('Carrier reference');
    expect(within(dialog).queryByText('Cancel port-in')).toBeNull();
    expect(within(dialog).queryByText('Correct and resubmit')).toBeNull();
  });
});

describe('number pool', () => {
  it('requests numbers by area code with the exact body', async () => {
    const onDone = vi.fn();
    const { fetchMock } = stubFetch({
      'POST /internal/v1/number/pool': () =>
        json(
          { areaCode: '415', requested: 3, numbers: [{ id: 'n1', value: '14155550100' }] },
          { status: 201 },
        ),
    });
    render(
      <ToastProvider>
        <PoolDialog onClose={vi.fn()} onDone={onDone} />
      </ToastProvider>,
    );
    expectUi('ui.numbers.poolDialog');
    const primary = screen.getByText('Request');
    fireEvent.change(screen.getByLabelText('Area code'), { target: { value: '115' } });
    expect(primary.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('Area code'), { target: { value: '415' } });
    fireEvent.change(screen.getByLabelText('How many'), { target: { value: '3' } });
    fireEvent.click(primary);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      areaCode: '415',
      count: 3,
    });
  });

  it('shows the carrier running out of numbers', async () => {
    stubFetch({
      'POST /internal/v1/number/pool': () => tmfError(409, 'the carrier has no more numbers'),
    });
    render(
      <ToastProvider>
        <PoolDialog onClose={vi.fn()} onDone={vi.fn()} />
      </ToastProvider>,
    );
    fireEvent.change(screen.getByLabelText('Area code'), { target: { value: '415' } });
    fireEvent.click(screen.getByText('Request'));
    expect(await screen.findByText(/no more numbers/)).toBeTruthy();
  });
});

describe('Number Transfer PIN', () => {
  it('generates a PIN, shows it once with its expiry and copies it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const pin = {
      id: 'p1',
      state: 'active',
      number: '15551230001',
      createdAt: '2026-11-07T10:00:00Z',
      expiresAt: '2026-11-14T10:00:00Z',
    };
    const { fetchMock } = stubFetch({
      [`GET /internal/v1/port/pin?product.id=${PRODUCT}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      'POST /internal/v1/port/pin': () => json({ ...pin, pin: '482913' }, { status: 201 }),
    });
    render(
      <SystemContextProvider value={CONTEXT}>
        <ToastProvider>
          <TransferPinDialog productId={PRODUCT} onClose={vi.fn()} />
        </ToastProvider>
      </SystemContextProvider>,
    );
    expectUi('ui.subscriber.transferPin');
    fireEvent.change(screen.getByLabelText('Account PIN'), { target: { value: '4321' } });
    fireEvent.click(screen.getByText('Generate PIN'));
    expect((await screen.findByTestId('transfer-pin')).textContent).toBe('482913');
    expect(screen.getByText(/Valid until 2026-11-14/)).toBeTruthy();
    fireEvent.click(screen.getByText('Copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('482913'));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ productId: PRODUCT });
    expect((post?.[1]?.headers as Record<string, string>)['X-Account-Pin']).toBe('4321');
  });

  it('shows the server refusal for a missing or wrong account PIN and keeps the dialog', async () => {
    stubFetch({
      [`GET /internal/v1/port/pin?product.id=${PRODUCT}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      'POST /internal/v1/port/pin': () => tmfError(403, 'pinRequired'),
    });
    render(
      <SystemContextProvider value={CONTEXT}>
        <ToastProvider>
          <TransferPinDialog productId={PRODUCT} onClose={vi.fn()} />
        </ToastProvider>
      </SystemContextProvider>,
    );
    fireEvent.click(screen.getByText('Generate PIN'));
    expect(await screen.findByText(/pinRequired/)).toBeTruthy();
    expect(screen.queryByTestId('transfer-pin')).toBeNull();
    expect(screen.getByText('Generate PIN').hasAttribute('disabled')).toBe(false);
  });
});
