import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const CAT = '/tmf-api/productCatalogManagement/v5';
const rub = (value: number) => ({ unit: 'RUB', value });
const ids = (n: number) => `0000000${n}-0000-4000-8000-00000000000${n}`;

const OFFERINGS = [
  {
    id: ids(1),
    name: 'Mobile L',
    lifecycleStatus: 'Active',
    externalIdentifier: [{ id: 'MOB-L' }],
    productSpecification: { name: 'Mobile Voice & Data' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr1' }],
  },
  {
    id: ids(2),
    name: 'Fiber 100',
    lifecycleStatus: 'Active',
    externalIdentifier: [{ id: 'NET-100' }],
    productSpecification: { name: 'Home Internet' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-12-31T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr2' }, { id: 'pr3' }],
  },
  {
    id: ids(3),
    name: 'TV Basic',
    lifecycleStatus: 'In Design',
    externalIdentifier: [{ id: 'TV-BASE' }],
    productSpecification: { name: 'TV' },
    validFor: { startDateTime: '2026-10-04T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr4' }],
  },
  {
    id: ids(4),
    name: 'Old Mobile',
    lifecycleStatus: 'Retired',
    productSpecification: { name: 'Mobile Voice & Data' },
    validFor: { startDateTime: '2026-01-01T21:00:00Z', endDateTime: '2026-07-01T21:00:00Z' },
    productOfferingPrice: [],
  },
];
const PRICES = [
  { id: 'pr1', price: rub(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
  // 650 now, 700 from tomorrow: the current one is shown
  {
    id: 'pr2',
    price: rub(650),
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-10-01T21:00:00Z' },
  },
  { id: 'pr3', price: rub(700), validFor: { startDateTime: '2026-10-01T21:00:00Z' } },
  // only a future price: shown with its start
  { id: 'pr4', price: rub(399), validFor: { startDateTime: '2026-10-04T21:00:00Z' } },
];
const SPECS = [
  { id: 's1', name: 'Mobile Voice & Data' },
  { id: 's2', name: 'Home Internet' },
  { id: 's3', name: 'TV' },
];

function backend(routes: Record<string, Handler> = {}, offerings: unknown[] = OFFERINGS) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    [`GET ${CAT}/productOffering?limit=1&fields=id`]: () => json([], { total: offerings.length }),
    [`GET ${CAT}/productOffering?offset=0&limit=100`]: () =>
      json(offerings, { total: offerings.length }),
    [`GET ${CAT}/productSpecification?offset=0&limit=100`]: () =>
      json(SPECS, { total: SPECS.length }),
    [`GET ${CAT}/productOfferingPrice?priceType=recurring&offset=0&limit=100`]: () =>
      json(PRICES, { total: PRICES.length }),
    ...routes,
  });
}

async function open() {
  window.history.replaceState(null, '', '/catalog');
  render(<App />);
  await screen.findByRole('heading', { name: 'Product catalog' });
}

const dataRows = () =>
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 7);

function row(name: string): HTMLElement {
  const found = dataRows().find((r) => within(r).queryByText(name));
  if (!found) throw new Error(`no row ${name}`);
  return found;
}

const cellsOf = (name: string) =>
  within(row(name))
    .getAllByRole('cell')
    .map((c) => c.textContent);

describe('catalog', () => {
  it('lists offerings with code, specification, price, period, status and validity', async () => {
    backend();
    await open();
    await waitFor(() => row('Mobile L'));
    expectUi('ui.catalog.list');
    expect(screen.getByRole('columnheader', { name: 'Price, RUB' })).toBeTruthy();
    expect(cellsOf('Mobile L')).toEqual([
      'MOB-L',
      'Mobile L',
      'Mobile Voice & Data',
      '1,290.00',
      'month',
      'Active',
      '2026-06-02 – open-ended',
    ]);
    // the end is exclusive (midnight of Jan 1 in Moscow): the last valid day is shown
    expect(cellsOf('Fiber 100')[6]).toBe('2026-06-02 – 2026-12-31');
    expect(cellsOf('Fiber 100')[3]).toBe('650.00'); // valid now, not tomorrow's 700.00
    expect(cellsOf('TV Basic')[3]).toBe('399.00from 2026-10-05'); // only a future price
    expect(cellsOf('Old Mobile')[0]).toBe('—');
    expect(cellsOf('Old Mobile')[3]).toBe('—');
    expect(within(row('Old Mobile')).getByText('Old Mobile').className).toContain('bss-retired');
    expect(screen.getByText('Showing 4 of 4')).toBeTruthy();
    expect(screen.getByText('Prices exclude taxes · taxes are not applied')).toBeTruthy();
    const group = screen.getByRole('group', { name: 'Lifecycle status' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All4', 'In Design1', 'Active2', 'Retired1']);
  });

  it('searches code or name, filters by status, and resets', async () => {
    backend();
    await open();
    await waitFor(() => row('Mobile L'));
    const search = screen.getByRole('textbox', { name: 'Search offerings' });
    fireEvent.change(search, { target: { value: 'net-1' } });
    expect(dataRows()).toHaveLength(1);
    fireEvent.change(search, { target: { value: 'mobile' } });
    expect(dataRows()).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /^Retired/ }));
    expect(dataRows().map((r) => within(r).getAllByRole('cell')[1]?.textContent)).toEqual([
      'Old Mobile',
    ]);
    fireEvent.change(search, { target: { value: 'fiber' } });
    expect(screen.getByText('No matches')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(dataRows()).toHaveLength(4);
  });

  it('explains an empty catalog, and shows a failing call', async () => {
    backend({}, []);
    await open();
    expect(await screen.findByText('Catalog is empty')).toBeTruthy();
    expect(
      screen.getByText('Create the first product offering. It starts in In Design.'),
    ).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '+ New offering' })).toHaveLength(2);
  });

  it('shows the failing call', async () => {
    backend({
      [`GET ${CAT}/productOffering?offset=0&limit=100`]: () => tmfError(503, 'Service Unavailable'),
    });
    await open();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load the catalog')).toBeTruthy();
    expect(
      within(alert).getByText(
        `GET ${CAT}/productOffering?offset=0&limit=100 → 503 Service Unavailable`,
      ),
    ).toBeTruthy();
  });
});

describe('new offering', () => {
  async function openForm() {
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: '+ New offering' }));
    return screen.getByRole('dialog', { name: 'New product offering' });
  }
  const field = (dialog: HTMLElement, label: RegExp) => within(dialog).getByLabelText(label);
  const create = (dialog: HTMLElement) =>
    within(dialog).getByRole('button', { name: 'Create in In Design' }) as HTMLButtonElement;

  it('validates as the design says, and enables Create only when complete', async () => {
    backend();
    const dialog = await openForm();
    expect(create(dialog).disabled).toBe(true);
    expect((field(dialog, /Valid from/) as HTMLInputElement).value).toBe('2026-10-02'); // tomorrow
    fireEvent.change(field(dialog, /Code/), { target: { value: 'mob-l' } });
    expect(within(dialog).getByRole('alert').textContent).toBe('Code MOB-L is already in use');
    fireEvent.change(field(dialog, /Code/), { target: { value: 'mob-xl' } });
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.change(field(dialog, /Price, RUB/), { target: { value: '0' } });
    expect(within(dialog).getByRole('alert').textContent).toBe('Price must be a positive number');
    fireEvent.change(field(dialog, /Price, RUB/), { target: { value: '1,990.5' } });
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-10-01' } });
    expect(within(dialog).getByRole('alert').textContent).toBe('End date is before start date');
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '' } });
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(create(dialog).disabled).toBe(false);
    expect((field(dialog, /Recurrence/) as HTMLSelectElement).disabled).toBe(true); // month only
  });

  it('creates the price, then the In Design offering that references it', async () => {
    let created = false;
    const { fetchMock } = backend({
      [`POST ${CAT}/productOfferingPrice`]: () => json({ id: 'new-price' }, { status: 201 }),
      [`POST ${CAT}/productOffering`]: () => {
        created = true;
        return json({ id: ids(9) }, { status: 201 });
      },
    });
    const dialog = await openForm();
    expect(dialog).toBe(expectUi('ui.catalog.newOffering'));
    fireEvent.change(field(dialog, /Code/), { target: { value: 'mob-xl' } });
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.change(field(dialog, /Specification/), { target: { value: 's1' } });
    fireEvent.change(field(dialog, /Price, RUB/), { target: { value: '1,990.5' } });
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-12-31' } });
    fireEvent.click(create(dialog));

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering MOB-XL created in In Design',
    );
    expect(created).toBe(true);
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    const [priceCall, offeringCall] = posts;
    const validFor = {
      startDateTime: '2026-10-02T00:00:00+03:00',
      endDateTime: '2027-01-01T00:00:00+03:00', // "valid to 2026-12-31" is inclusive
    };
    // the amount is an exact number token in the JSON text, not a float
    expect(String(priceCall?.[1]?.body)).toContain('"price":{"unit":"RUB","value":1990.50}');
    expect(JSON.parse(String(priceCall?.[1]?.body))).toEqual({
      '@type': 'ProductOfferingPrice',
      name: 'Mobile XL monthly',
      lifecycleStatus: 'Active',
      priceType: 'recurring',
      recurringChargePeriodType: 'monthly',
      price: { unit: 'RUB', value: 1990.5 },
      validFor,
    });
    expect(JSON.parse(String(offeringCall?.[1]?.body))).toEqual({
      '@type': 'ProductOffering',
      name: 'Mobile XL',
      lifecycleStatus: 'In Design',
      validFor,
      productSpecification: { id: 's1', '@type': 'ProductSpecificationRef' },
      productOfferingPrice: [{ id: 'new-price', '@type': 'ProductOfferingPriceRef' }],
      externalIdentifier: [
        { id: 'MOB-XL', externalIdentifierType: 'offeringCode', '@type': 'ExternalIdentifier' },
      ],
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows the backend refusal and keeps the form', async () => {
    backend({
      [`POST ${CAT}/productOfferingPrice`]: () => json({ id: 'p' }, { status: 201 }),
      [`POST ${CAT}/productOffering`]: () =>
        json(
          {
            '@type': 'Error',
            code: '409',
            reason: 'Conflict',
            message: 'offering code MOB-XL is already in use',
            status: '409',
          },
          { status: 409 },
        ),
    });
    const dialog = await openForm();
    fireEvent.change(field(dialog, /Code/), { target: { value: 'MOB-XL' } });
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.change(field(dialog, /Price, RUB/), { target: { value: '10' } });
    fireEvent.click(create(dialog));
    expect(await within(dialog).findByText('offering code MOB-XL is already in use')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});

describe('lifecycle', () => {
  async function details(name: string) {
    await open();
    await waitFor(() => row(name));
    fireEvent.click(row(name));
    return screen.getByRole('dialog', { name });
  }

  it('activates an In Design offering with a merge patch, after confirming', async () => {
    let patched = 0;
    const { fetchMock } = backend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => {
        patched++;
        return json({ id: ids(3), lifecycleStatus: 'Active' });
      },
    });
    const dialog = await details('TV Basic');
    expect(within(dialog).getByText('399.00 RUB / mo · 2026-10-05 – open-ended')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Activate' })).toBe(
      expectUi('ui.catalog.lifecycle'),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }));
    expect(within(dialog).getByText('Activate? It becomes available to order.')).toBeTruthy();
    expect(patched).toBe(0);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering TV-BASE is now Active',
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ lifecycleStatus: 'Active' });
    expect((call?.[1]?.headers as Record<string, string>)['Content-Type']).toBe(
      'application/merge-patch+json',
    );
  });

  it('retires an Active offering only after the warning', async () => {
    const { fetchMock } = backend({
      [`PATCH ${CAT}/productOffering/${ids(1)}`]: () =>
        json({ id: ids(1), lifecycleStatus: 'Retired' }),
    });
    const dialog = await details('Mobile L');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire' }));
    expect(
      within(dialog).getByText('Retire? New orders are refused; existing products keep billing.'),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering MOB-L is now Retired',
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ lifecycleStatus: 'Retired' });
  });

  it('offers nothing after Retired', async () => {
    backend();
    const dialog = await details('Old Mobile');
    expect(within(dialog).queryByRole('button', { name: /Activate|Retire/ })).toBeNull();
    expect(
      within(dialog).getByText('Retired is final: the offering cannot be ordered again.'),
    ).toBeTruthy();
    expect(within(dialog).getByText('no recurring price')).toBeTruthy();
  });

  it('shows a refused step', async () => {
    backend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () =>
        json(
          {
            '@type': 'Error',
            code: '422',
            reason: 'Unprocessable Entity',
            message: 'lifecycleStatus: invalid',
            status: '422',
          },
          { status: 422 },
        ),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByText('lifecycleStatus: invalid')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('change price', () => {
  const CHANGE = `POST /internal/v1/productOffering/${ids(1)}/priceChange`;

  async function openChange() {
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(row('Mobile L'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile L' });
    expect(dialog).toBe(expectUi('ui.catalog.offeringDetail'));
    const change = within(dialog).getByRole('button', { name: 'Change price' });
    expect(change).toBe(expectUi('ui.catalog.changePrice'));
    fireEvent.click(change);
    return within(dialog).getByRole('region', { name: 'Change price' });
  }
  const priceField = (form: HTMLElement) => within(form).getByLabelText(/New price, RUB/);
  const submit = (form: HTMLElement) =>
    within(form).getByRole('button', { name: 'Change price' }) as HTMLButtonElement;

  it('is offered only where there is an open-ended price to replace', async () => {
    backend();
    await open();
    await waitFor(() => row('Old Mobile'));
    fireEvent.click(row('Old Mobile')); // no price at all
    const dialog = screen.getByRole('dialog', { name: 'Old Mobile' });
    expect(within(dialog).queryByRole('button', { name: 'Change price' })).toBeNull();
  });

  it('says when existing products and new orders pick up the new price', async () => {
    backend();
    const form = await openChange();
    expect((within(form).getByLabelText('From') as HTMLInputElement).value).toBe('2026-10-02');
    expect(form.textContent).toContain(
      'Existing products pick up the new price from their next billing period starting on' +
        ' or after 2026-10-02; new orders from 2026-10-02.',
    );
    fireEvent.change(within(form).getByLabelText('From'), { target: { value: '2026-11-01' } });
    expect(form.textContent).toContain(
      'starting on or after 2026-11-01; new orders from 2026-11-01',
    );
  });

  it('validates the price and the date', async () => {
    backend();
    const form = await openChange();
    expect(submit(form).disabled).toBe(true);
    fireEvent.change(priceField(form), { target: { value: '0' } });
    expect(within(form).getByRole('alert').textContent).toBe('Price must be a positive number');
    fireEvent.change(priceField(form), { target: { value: '1,390' } });
    fireEvent.change(within(form).getByLabelText('From'), { target: { value: '2026-10-01' } });
    expect(within(form).getByRole('alert').textContent).toBe(
      'The new price can start on 2026-10-02 at the earliest',
    );
    expect(submit(form).disabled).toBe(true);
  });

  it('sends one atomic change with the exact amount and closes', async () => {
    const { fetchMock } = backend({
      [CHANGE]: () =>
        json(
          {
            productOffering: { id: ids(1), href: '/o' },
            endedPrice: { id: 'pr1', href: '/p1', endsOn: '2026-11-01' },
            newPrice: { id: 'pr9', href: '/p9', price: rub(1390), validFrom: '2026-11-01' },
          },
          { status: 201 },
        ),
    });
    const form = await openChange();
    fireEvent.change(priceField(form), { target: { value: '1,390' } });
    fireEvent.change(within(form).getByLabelText('From'), { target: { value: '2026-11-01' } });
    fireEvent.click(submit(form));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Price of MOB-L changes to 1,390.00 RUB from 2026-11-01',
    );
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/priceChange'));
    expect(String(call?.[1]?.body)).toBe(
      '{"amount":1390.00,"currency":"RUB","validFrom":"2026-11-01"}',
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows a refusal from the backend and keeps the form', async () => {
    backend({
      [CHANGE]: () =>
        json(
          {
            '@type': 'Error',
            code: '409',
            reason: 'Conflict',
            message: 'a recurring price in RUB already starts on or after 2026-11-01',
            status: '409',
          },
          { status: 409 },
        ),
    });
    const form = await openChange();
    fireEvent.change(priceField(form), { target: { value: '1390' } });
    fireEvent.change(within(form).getByLabelText('From'), { target: { value: '2026-11-01' } });
    fireEvent.click(submit(form));
    expect(
      await within(form).findByText(
        'a recurring price in RUB already starts on or after 2026-11-01',
      ),
    ).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});
