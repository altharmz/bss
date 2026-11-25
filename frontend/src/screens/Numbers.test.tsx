import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { pickOption } from '../test/combobox';
import { NumbersScreen, NumberPicker } from './Numbers';
import { ProductNumber } from './ProductNumber';
import { CrumbsProvider } from '../app/context';
import { RouterProvider } from '../app/router';
import { json, stubFetch, tmfError, type Handler } from '../test/fetch';
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
type Stock = typeof stock & { resourceSpecification?: { id: string } };

/** The inventory as the server lists it (#314): exact filters, then the page. Each number
 * is also readable by id (a product's own numbers). */
function inventoryOf(numbers: Partial<Stock>[]): Record<string, Handler> {
  const listed: Handler = (url) => {
    const query = new URL(url ?? '', 'http://test').searchParams;
    const field: Record<string, (n: Partial<Stock>) => string | undefined> = {
      value: (n) => n.value,
      'resourceSpecification.id': (n) => n.resourceSpecification?.id ?? 'msisdn',
      administrativeState: (n) => n.administrativeState,
      allocationStatus: (n) => n.allocationStatus,
    };
    const offset = Number(query.get('offset') ?? 0);
    const limit = Number(query.get('limit') ?? 20);
    const found = numbers.filter((n) =>
      Object.entries(field).every(([name, of]) => !query.has(name) || of(n) === query.get(name)),
    );
    return json(found.slice(offset, offset + limit), { more: found.length > offset + limit });
  };
  return {
    [`GET ${NUMBERS}?*`]: listed,
    ...Object.fromEntries(numbers.map((n) => [`GET ${NUMBERS}/${n.id}`, () => json(n)])),
  };
}
const requested = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(`${NUMBERS}?`));
const AVAILABLE =
  'resourceSpecification.id=msisdn&administrativeState=unlocked&allocationStatus=available';
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
      ...inventoryOf([stock]),
      [`POST ${NUMBERS}`]: () => json(stock),
      [`GET ${PATHS.products}?realizingResource.id=${N1}&offset=0&limit=100`]: () =>
        json([], { total: 0 }),
      [`GET ${NUMBERS}/${N1}`]: () => json(stock),
      [`PATCH ${NUMBERS}/${N1}`]: () => json({ ...stock, administrativeState: 'locked' }),
    });
    inventory();
    expectUi('ui.numbers.list');
    expect(screen.getByRole('button', { name: 'Request numbers' })).toBe(
      expectUi('ui.numbers.pool'),
    );
    fireEvent.change(screen.getByLabelText('Search numbers'), { target: { value: '999' } });
    expect(await screen.findByText('No matches')).toBeTruthy();
    // TEST-CHANGE (#314): an exact value on the server (the API has no partial match)
    expect(requested(fetchMock)).toContain(`${NUMBERS}?value=999&offset=0&limit=50`);
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
      ...inventoryOf([stock, used]),
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
    const assigned = await screen.findByText('Active');
    expect(assigned.classList.contains('pill-live')).toBe(true);
    expect(screen.queryByText('Delete number')).toBeNull();
    fireEvent.click(within(screen.getByRole('dialog')).getByText('Close'));
    fireEvent.click(await screen.findByRole('button', { name: stock.value }));
    const detail = screen.getByRole('dialog');
    fireEvent.click(await within(detail).findByText('Delete number'));
    expectUi('ui.numbers.delete');
    expect(within(detail).getByRole('button', { name: 'Confirm delete' })).toBe(
      expectUi('ui.numbers.deleteConfirm'),
    );
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
    stubFetch({
      [`GET ${NUMBERS}?*`]: () => (failed ? tmfError(503, 'Unavailable') : json([])),
    });
    inventory();
    expect(await screen.findByText('Failed to load numbers')).toBeTruthy();
    failed = false;
    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByText('No numbers yet')).toBeTruthy();
  });

  it('only offers unlocked available numbers', async () => {
    const { fetchMock } = stubFetch({
      ...inventoryOf([
        stock,
        { ...stock, id: 'n2', value: '12025550124', administrativeState: 'locked' },
        { ...stock, id: 'n3', value: '12025550125', allocationStatus: 'reserved' },
      ]),
    });
    render(<NumberPicker value="" onChange={() => {}} />);
    const input = screen.getByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(input.hasAttribute('disabled')).toBe(false));
    expect(input.getAttribute('placeholder')).toBe('Choose a number');
    fireEvent.focus(input);
    expect(screen.getByRole('option', { name: stock.value })).toBeTruthy();
    expect(screen.queryByRole('option', { name: '12025550124' })).toBeNull();
    expect(screen.queryByRole('option', { name: '12025550125' })).toBeNull();
    // TEST-CHANGE (#314): the server filters the picker's numbers, one page of them
    expect(requested(fetchMock)).toEqual([`${NUMBERS}?${AVAILABLE}&offset=0&limit=50`]);
  });

  it('finds a typed number outside the first page, keeping the list while it looks', async () => {
    const far = { ...stock, id: 'far', value: '12025559999', name: 'MSISDN 12025559999' };
    const { fetchMock } = stubFetch({
      [`GET ${NUMBERS}?${AVAILABLE}&offset=0&limit=50`]: () => json([stock], { more: true }),
      [`GET ${NUMBERS}?${AVAILABLE}&value=12025559999&limit=1`]: () => json([far]),
    });
    render(<NumberPicker value="" onChange={() => {}} />);
    const input = screen.getByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(input.hasAttribute('disabled')).toBe(false));
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '+1 202 555 9999' } });
    expect(input.hasAttribute('disabled')).toBe(false);
    expect(await screen.findByRole('option', { name: far.value })).toBeTruthy();
    expect(requested(fetchMock)).toContain(`${NUMBERS}?${AVAILABLE}&value=12025559999&limit=1`);
  });

  it('filters by typing, moves with the arrows, picks with Enter and restores on Escape', async () => {
    stubFetch({
      ...inventoryOf([
        { ...stock, id: 'a', value: '12025550123', name: '12025550123' },
        { ...stock, id: 'b', value: '12025550199', name: 'Sales line' },
      ]),
    });
    const picked: string[] = [];
    function Host() {
      const [id, setId] = useState('');
      return (
        <NumberPicker
          value={id}
          onChange={(next) => {
            picked.push(next);
            setId(next);
          }}
        />
      );
    }
    render(<Host />);
    const input = screen.getByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(input.hasAttribute('disabled')).toBe(false));
    expect(input.getAttribute('aria-expanded')).toBe('false');
    fireEvent.focus(input);
    expect(input.getAttribute('aria-expanded')).toBe('true');
    // the name shows only when it differs from the number
    const options = screen.getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['12025550123', '12025550199 · Sales line']);
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0]?.id);
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(input.getAttribute('aria-activedescendant')).toBe(options[1]?.id);
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0]?.id);
    fireEvent.change(input, { target: { value: 'sales' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(picked).toEqual(['b']);
    expect((input as HTMLInputElement).value).toBe('12025550199 · Sales line');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '0123' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect((input as HTMLInputElement).value).toBe('12025550199 · Sales line');
    expect(picked).toEqual(['b']);
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

  it('opens filtered to the number a link names (#477)', async () => {
    const { fetchMock } = stubFetch(inventoryOf([stock]));
    window.history.replaceState(null, '', `/numbers?value=${stock.value}&kind=msisdn`);
    inventory();
    expect(await screen.findByRole('button', { name: stock.value })).toBeTruthy();
    expect((screen.getByLabelText('Search numbers') as HTMLInputElement).value).toBe(stock.value);
    expect((screen.getByLabelText('Kind') as HTMLSelectElement).value).toBe('msisdn');
    expect(requested(fetchMock)[0]).toBe(
      `${NUMBERS}?value=${stock.value}&resourceSpecification.id=msisdn&offset=0&limit=50`,
    );
    window.history.replaceState(null, '', '/');
  });

  it('shows the digits and says why a number held outside an order cannot change (#477)', async () => {
    stubFetch({
      [`GET ${PATHS.products}/product`]: () =>
        json({
          id: 'product',
          status: 'active',
          productSpecification: { id: 'spec' },
          realizingResource: [{ id: N1, '@referredType': 'LogicalResource' }],
        }),
      [`GET ${PATHS.specifications}/spec`]: () => json({ resourceSpecification: [] }),
      ...inventoryOf([stock]),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    expect(await screen.findByText(`MSISDN ${stock.value}`)).toBeTruthy();
    expect(screen.queryByText(N1)).toBeNull();
    expect(await screen.findByText(/This product holds a number, but its offering/)).toBeTruthy();
    expect(screen.queryByText(/does not use subscriber numbers/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Change number' })).toBeNull();
  });

  it('explains why a product without a number requirement cannot acquire one', async () => {
    stubFetch({
      [`GET ${PATHS.products}/product`]: () =>
        json({ id: 'product', status: 'active', productSpecification: { id: 'spec' } }),
      [`GET ${PATHS.specifications}/spec`]: () => json({ resourceSpecification: [] }),
      ...inventoryOf([]),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    expect(
      await screen.findByText(
        "This product's offering does not use subscriber numbers. Offerings whose specification requires a number ask for one when the product is added.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Change number' })).toBeNull();
  });

  it('does not claim the offering has no numbers while the product is still loading', async () => {
    stubFetch({
      [`GET ${PATHS.products}/product`]: () => new Promise<Response>(() => {}),
      ...inventoryOf([]),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    expect(await screen.findByText('Loading number…')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(/does not use subscriber numbers/)).toBeNull());
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
      ...inventoryOf([
        stock,
        { ...stock, id: 'old', value: '12025550122', allocationStatus: 'allocated' },
      ]),
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
    const picker = await screen.findByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(picker.hasAttribute('disabled')).toBe(false));
    pickOption(picker, stock.value);
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

  it('creates an IMSI with its ICCID and filters the inventory by kind', async () => {
    const sim = {
      ...stock,
      id: 'sim1',
      name: 'IMSI 001010123456789',
      value: '001010123456789',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
    };
    const { fetchMock } = stubFetch({
      ...inventoryOf([stock, sim]),
      [`POST ${NUMBERS}`]: () => json(sim),
    });
    inventory();
    await screen.findByRole('button', { name: sim.value });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'imsi' } });
    await waitFor(() => expect(screen.queryByRole('button', { name: stock.value })).toBeNull());
    expect(requested(fetchMock)).toContain(
      `${NUMBERS}?resourceSpecification.id=imsi&offset=0&limit=50`,
    );
    expect(screen.getByRole('button', { name: sim.value })).toBeTruthy();
    fireEvent.click(screen.getByText('New number'));
    const create = screen.getByRole('dialog');
    fireEvent.change(within(create).getByLabelText('Kind'), { target: { value: 'imsi' } });
    fireEvent.change(within(create).getByLabelText('IMSI'), { target: { value: sim.value } });
    fireEvent.change(within(create).getByLabelText('ICCID (optional)'), {
      target: { value: '123' },
    });
    expect(within(create).getByText('Create number').hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(create).getByLabelText('ICCID (optional)'), {
      target: { value: '8900000000000000001' },
    });
    fireEvent.click(within(create).getByText('Create number'));
    await screen.findByText('New number');
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body),
      ),
    ).toEqual({
      '@type': 'LogicalResource',
      name: sim.name,
      value: sim.value,
      administrativeState: 'unlocked',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
      resourceCharacteristic: [
        {
          '@type': 'StringCharacteristic',
          name: 'iccid',
          valueType: 'string',
          value: '8900000000000000001',
        },
      ],
    });
  });

  it('changes the SIM with the complete resulting set', async () => {
    const msisdn = { ...stock, id: 'num', value: '12025550122' };
    const oldSim = {
      ...stock,
      id: 'oldsim',
      value: '001010123456780',
      allocationStatus: 'allocated',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
      resourceCharacteristic: [
        { '@type': 'StringCharacteristic', name: 'iccid', value: '8900000000000000009' },
      ],
    };
    const newSim = {
      ...stock,
      id: 'newsim',
      name: 'IMSI 001010123456781',
      value: '001010123456781',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
    };
    const { fetchMock } = stubFetch({
      [`GET ${PATHS.products}/product`]: () =>
        json({
          id: 'product',
          status: 'active',
          productSpecification: { id: 'spec' },
          realizingResource: [{ id: 'num' }, { id: 'oldsim' }],
        }),
      ['GET /tmf-api/productCatalogManagement/v5/productSpecification/spec']: () =>
        json({ resourceSpecification: [{ id: 'msisdn' }, { id: 'imsi' }] }),
      ...inventoryOf([msisdn, oldSim, newSim]),
      [`POST ${PATHS.orders}`]: () => json({ state: 'completed' }),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    expect(await screen.findByText('ICCID 8900000000000000009')).toBeTruthy();
    const imsi = await screen.findByRole('combobox', { name: 'IMSI' });
    await waitFor(() => expect(imsi.hasAttribute('disabled')).toBe(false));
    pickOption(imsi, newSim.value);
    fireEvent.click(screen.getByText('Change SIM'));
    await screen.findByRole('button', { name: 'Subscriber number' });
    const body = JSON.parse(
      String(fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body),
    );
    expect(body.productOrderItem[0].product.realizingResource).toEqual([
      { id: 'num', '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
      { id: 'newsim', '@type': 'ResourceRef', '@referredType': 'LogicalResource' },
    ]);
  });

  it('sends the account PIN with a SIM swap and explains a PIN refusal', async () => {
    const msisdn = { ...stock, id: 'num', value: '12025550122' };
    const oldSim = {
      ...stock,
      id: 'oldsim',
      value: '001010123456780',
      allocationStatus: 'allocated',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
    };
    const newSim = {
      ...stock,
      id: 'newsim',
      name: 'IMSI 001010123456781',
      value: '001010123456781',
      resourceSpecification: { id: 'imsi', '@type': 'ResourceSpecificationRef' },
    };
    const { fetchMock } = stubFetch({
      [`GET ${PATHS.products}/product`]: () =>
        json({
          id: 'product',
          status: 'active',
          productSpecification: { id: 'spec' },
          realizingResource: [{ id: 'num' }, { id: 'oldsim' }],
        }),
      ['GET /tmf-api/productCatalogManagement/v5/productSpecification/spec']: () =>
        json({ resourceSpecification: [{ id: 'msisdn' }, { id: 'imsi' }] }),
      ...inventoryOf([msisdn, oldSim, newSim]),
      [`POST ${PATHS.orders}`]: () =>
        json({
          state: 'rejected',
          productOrderErrorMessage: [
            { code: 'pinInvalid', reason: 'the account PIN is not valid' },
          ],
        }),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    const imsi = await screen.findByRole('combobox', { name: 'IMSI' });
    await waitFor(() => expect(imsi.hasAttribute('disabled')).toBe(false));
    pickOption(imsi, newSim.value);
    expectUi('ui.security.accountPin');
    fireEvent.change(screen.getByLabelText('Account PIN'), { target: { value: ' 482913 ' } });
    fireEvent.click(screen.getByText('Change SIM'));
    expect(
      await screen.findByText(
        'The account PIN is wrong. Repeated wrong PINs lock the verification.',
      ),
    ).toBeTruthy();
    expect(screen.getByText('pinInvalid')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect((post?.[1]?.headers as Record<string, string>)['X-Account-Pin']).toBe('482913');
    // the PIN never travels in the body
    expect(String(post?.[1]?.body)).not.toContain('482913');
  });

  it('sends no PIN header when no account PIN is typed', async () => {
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
      ...inventoryOf([
        stock,
        { ...stock, id: 'old', value: '12025550122', allocationStatus: 'allocated' },
      ]),
      [`POST ${PATHS.orders}`]: () =>
        json({
          state: 'rejected',
          productOrderErrorMessage: [{ code: 'pinRequired', reason: 'needs the PIN' }],
        }),
    });
    render(<ProductNumber accountId="account" productId="product" onDone={() => {}} />);
    fireEvent.click(screen.getByText('Subscriber number'));
    const picker = await screen.findByRole('combobox', { name: 'Subscriber number' });
    await waitFor(() => expect(picker.hasAttribute('disabled')).toBe(false));
    pickOption(picker, stock.value);
    fireEvent.click(screen.getByText('Change number'));
    expect(await screen.findByText(/Ask the customer for their account PIN/)).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[1]?.headers as Record<string, string>).not.toHaveProperty('X-Account-Pin');
  });

  it('pages the inventory with Previous and Next', async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      ...stock,
      id: `n${i}`,
      value: `1202555${String(i).padStart(4, '0')}`,
    }));
    stubFetch(inventoryOf(many));
    inventory();
    expect(await screen.findByRole('button', { name: '12025550000' })).toBeTruthy();
    expect(screen.getByText('Showing 1–50')).toBeTruthy();
    expectUi('ui.numbers.previous');
    expectUi('ui.numbers.next');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByRole('button', { name: '12025550050' })).toBeTruthy();
    expect(screen.getByText('Showing 51–51')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
