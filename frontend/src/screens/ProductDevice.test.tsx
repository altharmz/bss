import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DeviceDialog } from './ProductDevice';
import { SystemContextProvider } from '../app/context';
import { PATHS } from '../api/bss';
import { imeiProblem } from '../api/devices';
import { CONTEXT, json, stubFetch, tmfError } from '../test/fetch';
import { ToastProvider } from '../ui/kit';
import { expectUi } from '../test/expectUi';

const ACCOUNT = '40000000-0000-4000-8000-000000000001';
const PRODUCT = '40000000-0000-4000-8000-000000000002';
const HISTORY = `GET /internal/v1/device?product.id=${PRODUCT}`;
const OLD = '356938035643809';
const NEW = '351111116543216';
const BAD = '990000011234566';
const device = (imei: string, over: Record<string, unknown> = {}) => ({
  id: `d-${imei}`,
  product: { id: PRODUCT },
  imei,
  compatibility: 'compatible',
  reasons: [],
  checkedAt: '2026-10-01T09:00:00Z',
  startedAt: '2026-10-01T09:00:00Z',
  endedAt: null,
  recordedByItem: { id: 'i1' },
  ...over,
});

function dialog(props: { canChange?: boolean; onDone?: () => void } = {}) {
  const onDone = props.onDone ?? vi.fn();
  render(
    <SystemContextProvider value={CONTEXT}>
      <ToastProvider>
        <DeviceDialog
          accountId={ACCOUNT}
          productId={PRODUCT}
          canChange={props.canChange ?? true}
          onClose={vi.fn()}
          onDone={onDone}
        />
      </ToastProvider>
    </SystemContextProvider>,
  );
  return onDone;
}

describe('IMEI check digit', () => {
  it('accepts 15 digits with a correct Luhn digit only', () => {
    expect(imeiProblem(OLD)).toBeNull();
    expect(imeiProblem(NEW)).toBeNull();
    expect(imeiProblem('35693803564380')).toBe('An IMEI is 15 digits.');
    expect(imeiProblem('35693803564380X')).toBe('An IMEI is 15 digits.');
    expect(imeiProblem('356938035643801')).toBe('The check digit (the last one) is wrong.');
  });
});

describe('Device dialog', () => {
  it('shows the current device, an unconfirmed one with its reasons, and the history', async () => {
    stubFetch({
      [HISTORY]: () =>
        json([
          device(NEW, {
            compatibility: 'unknown',
            reasons: ['the network could not check the device'],
            startedAt: '2026-10-05T09:00:00Z',
          }),
          device(OLD, { endedAt: '2026-10-05T09:00:00Z' }),
        ]),
    });
    dialog();
    expectUi('ui.subscriber.device');
    expect(await screen.findByText('the network could not check the device')).toBeTruthy();
    const history = expectUi('ui.subscriber.deviceHistory');
    const rows = within(history).getAllByText(/–/);
    expect(rows.map((r) => r.textContent)).toEqual([
      `${NEW} · Not confirmed by the network · 2026-10-05 – now`,
      `${OLD} · Works on the network · 2026-10-01 – 2026-10-05`,
    ]);
  });

  it('checks a device without storing anything and shows the network reasons', async () => {
    const { fetchMock } = stubFetch({
      [HISTORY]: () => json([]),
      [`GET /internal/v1/device/check?imei=${BAD}`]: () =>
        json({ imei: BAD, result: 'incompatible', reasons: ['band 71', 'carrier lock'] }),
    });
    dialog({ canChange: false });
    expect(await screen.findByText('No device is recorded for this line.')).toBeTruthy();
    const check = expectUi('ui.subscriber.checkDevice') as HTMLButtonElement;
    expect(check.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Change device' })).toBeNull();
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: BAD } });
    fireEvent.click(check);
    expect(await screen.findByText(`${BAD}: Does not work on the network.`)).toBeTruthy();
    expect(screen.getByText('band 71; carrier lock')).toBeTruthy();
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true);
  });

  it('changes the device with a modify item carrying only the imei', async () => {
    const { fetchMock } = stubFetch({
      [HISTORY]: () => json([device(OLD)]),
      [`POST ${PATHS.orders}`]: () =>
        json({ id: 'o1', state: 'completed', productOrderItem: [] }, { status: 201 }),
    });
    const onDone = dialog();
    await screen.findByText(/since 2026-10-01/);
    const change = expectUi('ui.subscriber.changeDevice') as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: OLD } });
    expect(change.disabled).toBe(true); // already the current device
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: NEW } });
    fireEvent.click(change);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('Device changed through an order'));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: ACCOUNT, '@type': 'BillingAccountRef' },
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: {
            '@type': 'ProductRefOrValue',
            id: PRODUCT,
            productCharacteristic: [
              { '@type': 'StringCharacteristic', name: 'imei', valueType: 'string', value: NEW },
            ],
          },
        },
      ],
    });
  });

  it('keeps the dialog open with the deviceIncompatible refusal', async () => {
    stubFetch({
      [HISTORY]: () => json([device(OLD)]),
      [`POST ${PATHS.orders}`]: () =>
        json(
          {
            id: 'o2',
            state: 'rejected',
            productOrderErrorMessage: [
              {
                code: 'deviceIncompatible',
                reason: 'the device does not work on the network: band 71',
              },
            ],
          },
          { status: 201 },
        ),
    });
    const onDone = dialog();
    await screen.findByText(/since 2026-10-01/);
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: BAD } });
    fireEvent.click(screen.getByRole('button', { name: 'Change device' }));
    expect(await screen.findByText(/does not work on the network/)).toBeTruthy();
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Device' })).toBeTruthy();
  });

  it('reports an accepted change with the unconfirmed-device warning', async () => {
    stubFetch({
      [HISTORY]: () => json([]),
      [`POST ${PATHS.orders}`]: () =>
        json(
          {
            id: 'o3',
            state: 'completed',
            productOrderItem: [
              {
                product: {
                  productCharacteristic: [
                    { name: 'imei', value: NEW },
                    { name: 'deviceCompatibility', value: 'unknown' },
                    { name: 'deviceReasons', value: 'not in the device database' },
                  ],
                },
              },
            ],
          },
          { status: 201 },
        ),
    });
    const onDone = dialog();
    await screen.findByText('No device is recorded for this line.');
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: NEW } });
    fireEvent.click(screen.getByRole('button', { name: 'Change device' }));
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith(
        'Device changed through an order · The network could not confirm the device works: not in the device database.',
      ),
    );
  });
});

// #498: a failed check, an accepted change and an unknown outcome
describe('Device dialog outcomes', () => {
  it('shows a failed check and keeps the typed IMEI', async () => {
    stubFetch({
      [HISTORY]: () => json([]),
      [`GET /internal/v1/device/check?imei=${NEW}`]: () => tmfError(503, 'device database down'),
    });
    dialog();
    await screen.findByText('No device is recorded for this line.');
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: NEW } });
    fireEvent.click(expectUi('ui.subscriber.checkDevice'));
    expect(await screen.findByText(/device database down/)).toBeTruthy();
    expect((screen.getByLabelText('IMEI') as HTMLInputElement).value).toBe(NEW);
    expect((expectUi('ui.subscriber.checkDevice') as HTMLButtonElement).disabled).toBe(false);
  });

  it('reports an accepted change with the order to follow', async () => {
    stubFetch({
      [HISTORY]: () => json([device(OLD)]),
      [`POST ${PATHS.orders}`]: () =>
        json({ id: 'o4', state: 'inProgress', productOrderItem: [] }, { status: 201 }),
    });
    const onDone = vi.fn();
    dialog({ onDone });
    await screen.findByText(/since 2026-10-01/);
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: NEW } });
    fireEvent.click(screen.getByRole('button', { name: 'Change device' }));
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith(
        'Device change accepted, the billing worker completes it shortly',
        { id: 'o4', label: 'Device change' },
      ),
    );
  });

  it('offers only Refresh after an unanswered change', async () => {
    const { fetchMock } = stubFetch({
      [HISTORY]: () => json([device(OLD)]),
      [`POST ${PATHS.orders}`]: () => tmfError(502, 'bad gateway'),
    });
    const onDone = vi.fn();
    dialog({ onDone });
    await screen.findByText(/since 2026-10-01/);
    fireEvent.change(screen.getByLabelText('IMEI'), { target: { value: NEW } });
    fireEvent.click(screen.getByRole('button', { name: 'Change device' }));
    expect(await screen.findByText('Unsure whether it was saved.')).toBeTruthy();
    // no second change goes out blind: Refresh (which closes) is the way on
    expect(screen.queryByRole('button', { name: 'Change device' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(onDone).not.toHaveBeenCalled();
  });
});
