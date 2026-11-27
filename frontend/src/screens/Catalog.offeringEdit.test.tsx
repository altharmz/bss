// #498: the offering edit's validation, category and specification changes, and the price
// dialog's answers No and a completed delete.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, type Handler } from '../test/fetch';
import { CAT, SPECS, ids, OFFERINGS, usd, backend, open, row } from '../test/catalogKit';

const dataAllowance = (values: number[]) => ({
  '@type': 'CharacteristicSpecification',
  name: 'dataAllowanceMb',
  valueType: 'integer',
  characteristicValueSpecification: values.map((value, index) => ({
    '@type': 'IntegerCharacteristicValueSpecification',
    valueType: 'integer',
    isDefault: index === 0,
    value,
  })),
});
const NARROWED = {
  '@type': 'ProductSpecificationCharacteristicValueUse',
  name: 'dataAllowanceMb',
  valueType: 'integer',
  productSpecCharacteristicValue: [
    { '@type': 'IntegerCharacteristicValueSpecification', valueType: 'integer', value: 10000 },
  ],
};
const DETAIL = {
  ...OFFERINGS[2],
  description: 'Basic TV',
  isSellable: true,
  category: [{ id: 'c1', name: 'TV bundles' }],
  externalIdentifier: [
    { id: 'TV-BASE', externalIdentifierType: 'offeringCode', '@type': 'ExternalIdentifier' },
  ],
  productSpecification: { id: 's3', name: 'TV' },
  validFor: { startDateTime: '2026-10-04T21:00:00Z', endDateTime: '2026-12-31T21:00:00Z' },
  productOfferingPrice: [],
};
const LIST = OFFERINGS.map((o) => (o.id === ids(3) ? { ...o, productOfferingPrice: [] } : o));

function tvBackend(routes: Record<string, Handler> = {}) {
  return backend(
    {
      [`GET ${CAT}/productOffering/${ids(3)}`]: () => json(DETAIL),
      [`GET ${CAT}/category?offset=0&limit=100`]: () =>
        json([{ id: 'c1', name: 'TV bundles' }], { total: 1 }),
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
      ...routes,
    },
    LIST,
  );
}
async function editTv() {
  await open();
  await waitFor(() => row('TV Basic'));
  fireEvent.click(row('TV Basic'));
  const dialog = screen.getByRole('dialog', { name: 'TV Basic' });
  await within(dialog).findByText('Basic TV');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
  return screen.getByRole('dialog', { name: 'Edit TV Basic' });
}
const patchOf = (fetchMock: ReturnType<typeof backend>['fetchMock']) =>
  JSON.parse(
    String(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body),
  ) as Record<string, unknown>;
const save = (edit: HTMLElement) =>
  within(edit).getByRole('button', { name: 'Save' }) as HTMLButtonElement;

describe('offering edit', () => {
  it('refuses a malformed code and an end before the start, sending nothing', async () => {
    const { fetchMock } = tvBackend();
    const edit = await editTv();
    fireEvent.change(within(edit).getByLabelText(/^Code/), { target: { value: 'TV BASE!' } });
    expect(within(edit).getByRole('alert').textContent).toBe(
      'Code may use A-Z, 0-9, "-" and "_" (up to 32 characters)',
    );
    expect(save(edit).disabled).toBe(true);
    fireEvent.change(within(edit).getByLabelText(/^Code/), { target: { value: 'tv-2' } });
    // the loaded end is 2026-12-31: a start after it is refused
    fireEvent.change(within(edit).getByLabelText(/^Valid from/), {
      target: { value: '2027-01-15' },
    });
    expect(within(edit).getByRole('alert').textContent).toBe('End date is before start date');
    expect(save(edit).disabled).toBe(true);
    fireEvent.click(save(edit));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });

  it('removes the only category with an empty list', async () => {
    const { fetchMock } = tvBackend();
    const edit = await editTv();
    const bundles = await within(edit).findByRole('checkbox', { name: 'TV bundles' });
    expect((bundles as HTMLInputElement).checked).toBe(true);
    fireEvent.click(bundles);
    expect((bundles as HTMLInputElement).checked).toBe(false);
    fireEvent.click(save(edit));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering TV-BASE saved',
    );
    expect(patchOf(fetchMock)).toEqual({ category: [] });
  });

  it('drops a network narrowing the new specification does not declare', async () => {
    const { fetchMock } = tvBackend({
      [`GET ${CAT}/productSpecification?offset=0&limit=100`]: () =>
        json(
          [
            SPECS[0],
            { ...SPECS[1], productSpecCharacteristic: [dataAllowance([5000])] },
            { ...SPECS[2], productSpecCharacteristic: [dataAllowance([5000, 10000])] },
          ],
          { total: 3 },
        ),
      [`GET ${CAT}/productOffering/${ids(3)}`]: () =>
        json({ ...DETAIL, prodSpecCharValueUse: [NARROWED] }),
    });
    const edit = await editTv();
    const narrowing = within(edit).getByLabelText('Network dataAllowanceMb') as HTMLSelectElement;
    expect(narrowing.value).toBe('10000');
    // Home Internet declares the characteristic, but not the value 10000
    fireEvent.change(within(edit).getByLabelText(/^Specification/), { target: { value: 's2' } });
    expect(
      (within(edit).getByLabelText('Network dataAllowanceMb') as HTMLSelectElement).value,
    ).toBe('');
    fireEvent.click(save(edit));
    await screen.findByRole('status');
    expect(patchOf(fetchMock)).toEqual({
      productSpecification: { id: 's2', '@type': 'ProductSpecificationRef' },
      prodSpecCharValueUse: [],
    });
  });
});

describe('price dialog answers', () => {
  const PRICE = {
    id: 'pr1',
    name: 'Mobile L monthly',
    priceType: 'recurring',
    recurringChargePeriodType: 'monthly',
    price: usd(1290),
    validFor: { startDateTime: '2026-06-01T21:00:00Z' },
  };
  async function openPrice(routes: Record<string, Handler> = {}) {
    const stub = backend({ [`GET ${CAT}/productOfferingPrice/pr1`]: () => json(PRICE), ...routes });
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(row('Mobile L'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile L' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Open price 1,290.00' }));
    return { ...stub, price: await screen.findByRole('dialog', { name: 'Mobile L monthly' }) };
  }
  const written = (fetchMock: ReturnType<typeof backend>['fetchMock']) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method !== undefined && init.method !== 'GET');

  it('answers No to ending a price and sends nothing', async () => {
    const { fetchMock, price } = await openPrice();
    fireEvent.click(within(price).getByRole('button', { name: 'End price' }));
    fireEvent.click(within(price).getByRole('button', { name: 'No' }));
    expect(within(price).queryByLabelText('Price ends on')).toBeNull();
    expect(within(price).getByRole('button', { name: 'End price' })).toBeTruthy();
    expect(written(fetchMock)).toEqual([]);
  });

  it('answers No to deleting a price, then deletes it with no body', async () => {
    const { fetchMock, price } = await openPrice({
      [`DELETE ${CAT}/productOfferingPrice/pr1`]: () => new Response(null, { status: 204 }),
    });
    fireEvent.click(within(price).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(price).getByRole('button', { name: 'No' }));
    expect(within(price).queryByText('Delete this price?')).toBeNull();
    expect(written(fetchMock)).toEqual([]);
    fireEvent.click(within(price).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(price).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Price Mobile L monthly deleted',
    );
    expect(screen.queryByRole('dialog', { name: 'Mobile L monthly' })).toBeNull();
    const [call] = written(fetchMock);
    expect(call?.[0]).toBe(`${CAT}/productOfferingPrice/pr1`);
    expect(call?.[1]?.body).toBeUndefined();
  });
});
