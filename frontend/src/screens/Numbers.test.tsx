import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NumbersScreen, NumberPicker } from './Numbers';
import { ProductNumber } from './ProductNumber';
import { CrumbsProvider } from '../app/context';
import { RouterProvider } from '../app/router';
import { json, stubFetch, tmfError } from '../test/fetch';
import { NUMBERS } from '../api/numbers';
import { orderAdd, PATHS } from '../api/bss';
import { ToastProvider } from '../ui/kit';
import { expectUi } from '../test/expectUi';

const N1 = '10000000-0000-4000-8000-000000000001';
const stock = {
  id: N1,
  '@type': 'LogicalResource',
  name: 'MSISDN 12025550123',
  value: '12025550123',
  allocationStatus: 'available',
  administrativeState: 'unlocked',
  usageState: 'idle',
};
const list = `GET ${NUMBERS}?offset=0&limit=100`;
function inventory() {
  return render(
    <RouterProvider>
      <CrumbsProvider>
        <ToastProvider>
          <NumbersScreen />
        </ToastProvider>
      </CrumbsProvider>
    </RouterProvider>,
  );
}

describe('number inventory', () => {
  it('searches, creates an exact number, inspects assignment and locks', async () => {
    const { fetchMock } = stubFetch({
      [list]: () => json([stock], { total: 1 }),
      [`POST ${NUMBERS}`]: () => json(stock),
      [`GET ${PATHS.products}?realizingResource.id=${N1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${NUMBERS}/${N1}`]: () => json(stock),
      [`PATCH ${NUMBERS}/${N1}`]: () => json({ ...stock, administrativeState: 'locked' }),
    });
    inventory();
    expectUi('ui.numbers.list');
    fireEvent.change(screen.getByLabelText('Search numbers'), { target: { value: '999' } });
    expect(await screen.findByText('No matches')).toBeTruthy();
    fireEvent.click(screen.getByText('Reset filters'));
    fireEvent.click(await screen.findByRole('button', { name: stock.value }));
    const detail = screen.getByRole('dialog');
    expectUi('ui.numbers.detail');
    expect(
      await within(detail).findByText('No current or future product assignment.'),
    ).toBeTruthy();
    fireEvent.click(within(detail).getByRole('button', { name: 'Lock' }));
    expect(await within(detail).findByRole('button', { name: 'Unlock' })).toBeTruthy();
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body),
      ),
    ).toEqual({ administrativeState: 'locked' });
    fireEvent.click(within(detail).getByText('Close'));
    fireEvent.click(screen.getByText('New number'));
    const create = screen.getByRole('dialog');
    expectUi('ui.numbers.create');
    const input = within(create).getByLabelText('Number');
    fireEvent.change(input, { target: { value: '+12025550123' } });
    expect(within(create).getByText('Create number').hasAttribute('disabled')).toBe(true);
    fireEvent.change(input, { target: { value: stock.value } });
    fireEvent.click(within(create).getByText('Create number'));
    await screen.findByText('New number');
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body),
      ),
    ).toEqual({
      '@type': 'LogicalResource',
      name: stock.name,
      value: stock.value,
      administrativeState: 'unlocked',
      resourceSpecification: { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
    });
  });

  it('deletes a never-assigned number: DELETE without a body, and hides it for assigned ones', async () => {
    const used = { ...stock, id: 'n2', value: '12025550124', allocationStatus: 'assigned' };
    let refuse = true;
    const { fetchMock } = stubFetch({
      [list]: () => json([stock, used], { total: 2 }),
      [`GET ${NUMBERS}/${N1}`]: () => json(stock),
      [`GET ${NUMBERS}/n2`]: () => json(used),
      [`GET ${PATHS.products}?realizingResource.id=${N1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${PATHS.products}?realizingResource.id=n2&offset=0&limit=100`]: () =>
        json([{ id: 'p', status: 'active' }], { total: 1 }),
      [`DELETE ${NUMBERS}/${N1}`]: () =>
        refuse
          ? tmfError(409, 'a number with assignment history cannot be deleted')
          : new Response(null, { status: 204 }),
    });
    inventory();
    fireEvent.click(await screen.findByRole('button', { name: used.value }));
    await screen.findByText(/Open subscriber|active/);
    expect(screen.queryByText('Delete number')).toBeNull();
    fireEvent.click(within(screen.getByRole('dialog')).getByText('Close'));
    fireEvent.click(await screen.findByRole('button', { name: stock.value }));
    const detail = screen.getByRole('dialog');
    fireEvent.click(await within(detail).findByText('Delete number'));
    expectUi('ui.numbers.delete');
    fireEvent.click(within(detail).getByText('Confirm delete'));
    // the server's refusal is shown and the dialog stays open
    expect(await within(detail).findByText(/assignment history cannot be deleted/)).toBeTruthy();
    refuse = false;
    fireEvent.click(within(detail).getByText('Confirm delete'));
    expect(await screen.findByText('Number 12025550123 deleted')).toBeTruthy();
    const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE');
    expect(deletes.map(([url, init]) => [url, init?.body])).toEqual([
      [`${NUMBERS}/${N1}`, undefined],
      [`${NUMBERS}/${N1}`, undefined],
    ]);
  });

  it('offers retry on a failed inventory read', async () => {
    let failed = true;
    stubFetch({ [list]: () => (failed ? tmfError(503, 'Unavailable') : json([], { total: 0 })) });
    inventory();
    expect(await screen.findByText('Failed to load numbers')).toBeTruthy();
    failed = false;
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByText('No numbers yet')).toBeTruthy();
  });

  it('only offers unlocked available numbers', async () => {
    stubFetch({
      [list]: () =>
        json(
          [
            stock,
            { ...stock, id: 'n2', value: '12025550124', administrativeState: 'locked' },
            { ...stock, id: 'n3', value: '12025550125', allocationStatus: 'reserved' },
          ],
          { total: 3 },
        ),
    });
    render(<NumberPicker value="" onChange={() => {}} />);
    expect(await screen.findByRole('option', { name: stock.value })).toBeTruthy();
    expect(screen.queryByRole('option', { name: '12025550124' })).toBeNull();
    expect(screen.queryByRole('option', { name: '12025550125' })).toBeNull();
  });

  it('pins number selection in the add order', async () => {
    const { fetchMock } = stubFetch({
      [`POST ${PATHS.orders}`]: () => json({ state: 'completed' }),
    });
    await orderAdd('account', 'offering', '2026-10-02', 'Europe/Moscow', N1);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: 'account', '@type': 'BillingAccountRef' },
      requestedStartDate: '2026-10-02T12:00:00+03:00',
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'add',
          productOffering: { id: 'offering', '@type': 'ProductOfferingRef' },
          product: {
            '@type': 'ProductRefOrValue',
            realizingResource: [
              { id: N1, '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
            ],
          },
        },
      ],
    });
  });

  it('changes a product number through a modify order and shows business rejection', async () => {
    const { fetchMock } = stubFetch({
      [`GET ${PATHS.products}/product`]: () =>
        json({
          id: 'product',
          status: 'active',
          productSpecification: { id: 'spec' },
          realizingResource: [{ id: 'old', name: '12025550122' }],
        }),
      ['GET /tmf-api/productCatalogManagement/v5/productSpecification/spec']: () =>
        json({ resourceSpecification: [{ id: 'msisdn' }] }),
      [list]: () => json([stock], { total: 1 }),
      [`POST ${PATHS.orders}`]: () =>
        json({
          state: 'rejected',
          productOrderErrorMessage: [
            { code: 'numberUnavailable', reason: 'Number no longer available' },
          ],
        }),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    expectUi('ui.subscriber.number');
    await screen.findByRole('option', { name: stock.value });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: N1 } });
    fireEvent.click(screen.getByText('Change number'));
    expect(
      await screen.findByText('This subscriber number is unavailable. Choose another number.'),
    ).toBeTruthy();
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body),
      ),
    ).toEqual({
      '@type': 'ProductOrder',
      billingAccount: { id: 'account', '@type': 'BillingAccountRef' },
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: {
            id: 'product',
            '@type': 'ProductRefOrValue',
            realizingResource: [
              { id: N1, '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
            ],
          },
        },
      ],
    });
  });
});
