import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SimsScreen } from './Sims';
import { CrumbsProvider } from '../app/context';
import { RouterProvider } from '../app/router';
import { json, stubFetch, tmfError } from '../test/fetch';
import { NUMBERS } from '../api/numbers';
import { PATHS, orderAdd } from '../api/bss';
import { ToastProvider } from '../ui/kit';
import { expectUi } from '../test/expectUi';

const E1 = '20000000-0000-4000-8000-000000000001';
const P1 = '20000000-0000-4000-8000-000000000002';
const CODE = 'LPA:1$smdp.example.com$MATCH0001';
const characteristics = (state: string, type: string, extra: object[] = []) => [
  { '@type': 'StringCharacteristic', name: 'iccid', value: '8900000000000000001' },
  { '@type': 'StringCharacteristic', name: 'simType', value: type },
  { '@type': 'StringCharacteristic', name: 'simState', value: state },
  ...extra,
];
const esim = {
  id: E1,
  '@type': 'LogicalResource',
  value: '001010000000001',
  batchNumber: 'B1',
  administrativeState: 'unlocked',
  resourceSpecification: { id: 'imsi' },
  resourceCharacteristic: characteristics('active', 'esim', [
    { '@type': 'StringCharacteristic', name: 'activationCode', value: CODE },
  ]),
};
const physical = {
  ...esim,
  id: P1,
  value: '001010000000002',
  resourceCharacteristic: characteristics('in_stock', 'physical'),
};
const batch = {
  name: 'B1',
  simType: 'esim',
  source: 'import',
  createdAt: '2026-11-01T00:00:00Z',
  total: 2,
  counts: { in_stock: 1, reserved: 0, active: 1, retired: 0 },
};
const LIST = `GET ${NUMBERS}?resourceSpecification.id=imsi&offset=0&limit=50`;
const BATCHES = 'GET /internal/v1/sim/batch';

function stock() {
  return render(
    <RouterProvider>
      <CrumbsProvider>
        <ToastProvider>
          <SimsScreen />
        </ToastProvider>
      </CrumbsProvider>
    </RouterProvider>,
  );
}

describe('SIM stock', () => {
  it('filters by state, type and batch with the TMF639 attributes', async () => {
    const { calls } = stubFetch({
      [LIST]: () => json([esim, physical], { total: 2 }),
      // the filters change one at a time: each step is its own request
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&simState=in_stock&offset=0&limit=50`]: () =>
        json([physical], { total: 1 }),
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&simState=in_stock&simType=physical&offset=0&limit=50`]:
        () => json([physical], { total: 1 }),
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&simState=in_stock&simType=physical&batchNumber=B1&offset=0&limit=50`]:
        () => json([physical], { total: 1 }),
      [BATCHES]: () => json([batch]),
    });
    stock();
    expectUi('ui.sims.list');
    expect(await screen.findByText('001010000000001')).toBeTruthy();
    await screen.findByRole('option', { name: 'B1 (2)' });
    fireEvent.change(screen.getByLabelText('State'), { target: { value: 'in_stock' } });
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'physical' } });
    fireEvent.change(screen.getByLabelText('Batch'), { target: { value: 'B1' } });
    await waitFor(() => expect(screen.queryByText('001010000000001')).toBeNull());
    expect(screen.getByText('001010000000002')).toBeTruthy();
    expect(calls.some((c) => c.includes('simState=in_stock&simType=physical&batchNumber=B1'))).toBe(
      true,
    );
  });

  // TEST-CHANGE (#314): the search is an exact IMSI on the server; ICCID search is gone
  // (the API has no filter for it, and a page cannot be filtered locally)
  it('searches an exact IMSI on the server and pages with Previous and Next', async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      ...physical,
      id: `s${i}`,
      value: `00101000000${String(i).padStart(4, '0')}`,
    }));
    const { calls } = stubFetch({
      [LIST]: () => json(many.slice(0, 50), { more: true }),
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&offset=50&limit=50`]: () =>
        json(many.slice(50)),
      [`GET ${NUMBERS}?resourceSpecification.id=imsi&value=001010000000050&offset=0&limit=50`]:
        () => json(many.slice(50)),
      [BATCHES]: () => json([batch]),
    });
    stock();
    expect(await screen.findByText('001010000000000')).toBeTruthy();
    expectUi('ui.sims.previous');
    expectUi('ui.sims.next');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 51–51')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Search SIMs'), {
      target: { value: '001010000000050' },
    });
    await waitFor(() =>
      expect(calls).toContain(
        `GET ${NUMBERS}?resourceSpecification.id=imsi&value=001010000000050&offset=0&limit=50`,
      ),
    );
    expect(await screen.findByText('Showing 1–1')).toBeTruthy();
  });

  it('shows an assigned eSIM activation code as text and QR, and none for a stocked profile', async () => {
    stubFetch({
      [LIST]: () =>
        json([esim, { ...physical, resourceCharacteristic: characteristics('in_stock', 'esim') }], {
          total: 2,
        }),
      [BATCHES]: () => json([batch]),
      [`GET ${NUMBERS}/${E1}`]: () => json(esim),
      [`GET ${PATHS.products}?realizingResource.id=${E1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${NUMBERS}/${P1}`]: () =>
        json({ ...physical, resourceCharacteristic: characteristics('in_stock', 'esim') }),
      [`GET ${PATHS.products}?realizingResource.id=${P1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
    });
    stock();
    fireEvent.click(await screen.findByRole('button', { name: '001010000000001' }));
    let detail = screen.getByRole('dialog');
    expectUi('ui.sims.detail');
    expect(await within(detail).findByText(CODE)).toBeTruthy();
    expect(within(detail).getByRole('img', { name: /Activation code QR/ })).toBeTruthy();
    fireEvent.click(within(detail).getByText('Close'));
    fireEvent.click(await screen.findByRole('button', { name: '001010000000002' }));
    detail = screen.getByRole('dialog');
    expect(await within(detail).findByText(/never exposes it/)).toBeTruthy();
    expect(within(detail).queryByRole('img')).toBeNull();
  });

  it('imports a CSV as text/csv with the batch and type in the query', async () => {
    const csv = 'iccid,imsi\n8900000000000000001,001010000000001\n';
    const { fetchMock } = stubFetch({
      [LIST]: () => json([], { total: 0 }),
      [BATCHES]: () => json([]),
      'POST /internal/v1/sim/batch?batch=B2&simType=physical': () =>
        json({ ...batch, name: 'B2', simType: 'physical', total: 1 }, { status: 201 }),
    });
    stock();
    fireEvent.click(await screen.findByText('Import CSV'));
    const dialog = screen.getByRole('dialog');
    expectUi('ui.sims.importDialog');
    const primary = within(dialog).getByText('Import');
    expect(primary.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Batch name'), { target: { value: 'B2' } });
    fireEvent.change(within(dialog).getByLabelText('CSV file'), {
      target: { files: [new File([csv], 'sims.csv', { type: 'text/csv' })] },
    });
    await waitFor(() => expect(primary.hasAttribute('disabled')).toBe(false));
    fireEvent.click(primary);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(call?.[1]?.body).toBe(csv);
    expect((call?.[1]?.headers as Record<string, string>)['Content-Type']).toBe('text/csv');
  });

  it('keeps a refused import open with the server text', async () => {
    stubFetch({
      [LIST]: () => json([], { total: 0 }),
      [BATCHES]: () => json([]),
      'POST /internal/v1/sim/batch?batch=B2&simType=esim': () => tmfError(409, 'batch exists'),
    });
    stock();
    fireEvent.click(await screen.findByText('Import CSV'));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Batch name'), { target: { value: 'B2' } });
    fireEvent.change(within(dialog).getByLabelText('Type'), { target: { value: 'esim' } });
    fireEvent.change(within(dialog).getByLabelText('CSV file'), {
      target: { files: [new File(['x'], 'sims.csv')] },
    });
    const primary = within(dialog).getByText('Import');
    await waitFor(() => expect(primary.hasAttribute('disabled')).toBe(false));
    fireEvent.click(primary);
    expect(await within(dialog).findByText(/batch exists/)).toBeTruthy();
    expect(primary.hasAttribute('disabled')).toBe(false);
  });

  it('generates eSIMs with the exact body', async () => {
    const { fetchMock } = stubFetch({
      [LIST]: () => json([], { total: 0 }),
      [BATCHES]: () => json([]),
      'POST /internal/v1/sim/esim-generate': () =>
        json({ ...batch, name: 'G1', source: 'generated', total: 5 }, { status: 201 }),
    });
    stock();
    fireEvent.click(await screen.findByText('Generate eSIMs'));
    const dialog = screen.getByRole('dialog');
    expectUi('ui.sims.generateDialog');
    fireEvent.change(within(dialog).getByLabelText('Batch name'), { target: { value: 'G1' } });
    fireEvent.change(within(dialog).getByLabelText('Number of profiles'), {
      target: { value: '5' },
    });
    fireEvent.click(within(dialog).getByText('Generate'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ batch: 'G1', count: 5 });
  });
});

describe('add order with a SIM type', () => {
  it('sends the simType characteristic instead of an IMSI', async () => {
    const { fetchMock } = stubFetch({
      [`POST ${PATHS.orders}`]: () => json({ id: 'o1', state: 'completed' }, { status: 201 }),
    });
    await orderAdd('acc', 'off', '2026-11-07', 'UTC', ['msisdn-id'], undefined, 'esim');
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.productOrderItem[0].product).toEqual({
      '@type': 'ProductRefOrValue',
      realizingResource: [
        { id: 'msisdn-id', '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
      ],
      productCharacteristic: [
        { '@type': 'StringCharacteristic', name: 'simType', valueType: 'string', value: 'esim' },
      ],
    });
  });
});
