import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { offeringCode } from '../api/bss';
import { App } from '../app/App';
import { validity } from './Catalog';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const CAT = '/tmf-api/productCatalogManagement/v5';
const usd = (value: number) => ({ unit: 'USD', value });
const ids = (n: number) => `0000000${n}-0000-4000-8000-00000000000${n}`;
const code = (id: string) => [{ id, externalIdentifierType: 'offeringCode' }];

const OFFERINGS = [
  {
    id: ids(1),
    name: 'Mobile L',
    lifecycleStatus: 'Active',
    externalIdentifier: code('MOB-L'),
    productSpecification: { name: 'Mobile Voice & Data' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr1' }],
  },
  {
    id: ids(2),
    name: 'Fiber 100',
    lifecycleStatus: 'Active',
    externalIdentifier: code('NET-100'),
    productSpecification: { name: 'Home Internet' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-12-31T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr2' }, { id: 'pr3' }],
  },
  {
    id: ids(3),
    name: 'TV Basic',
    lifecycleStatus: 'In Design',
    externalIdentifier: code('TV-BASE'),
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
  { id: 'pr1', price: usd(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
  // 650 now, 700 from tomorrow: the current one is shown
  {
    id: 'pr2',
    price: usd(650),
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-10-01T21:00:00Z' },
  },
  { id: 'pr3', price: usd(700), validFor: { startDateTime: '2026-10-01T21:00:00Z' } },
  // only a future price: shown with its start
  { id: 'pr4', price: usd(399), validFor: { startDateTime: '2026-10-04T21:00:00Z' } },
];
const SPECS = [
  { id: 's1', name: 'Mobile Voice & Data' },
  { id: 's2', name: 'Home Internet' },
  { id: 's3', name: 'TV' },
];

function backend(routes: Record<string, Handler> = {}, offerings: unknown[] = OFFERINGS) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
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
    // the detail dialog loads the offering itself
    ...Object.fromEntries(
      (offerings as { id: string }[]).map((o) => [
        `GET ${CAT}/productOffering/${o.id}`,
        () => json(o),
      ]),
    ),
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
  it('shows a non-monthly price interval in the list and offering details', async () => {
    backend({
      [`GET ${CAT}/productOfferingPrice?priceType=recurring&offset=0&limit=100`]: () =>
        json(
          PRICES.map((p) => ({
            ...p,
            recurringChargePeriodType: 'weekly',
            recurringChargePeriodLength: 2,
          })),
          { total: PRICES.length },
        ),
    });
    await open();
    await waitFor(() => expect(cellsOf('Mobile L')[4]).toBe('2 weeks'));
    fireEvent.click(row('Mobile L'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/USD \/ 2 weeks/)).toBeTruthy();
  });

  it('lists offerings with code, specification, price, period, status and validity', async () => {
    backend();
    await open();
    await waitFor(() => row('Mobile L'));
    expectUi('ui.catalog.list');
    expect(screen.getByRole('columnheader', { name: 'Price, USD' })).toBeTruthy();
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
    fireEvent.change(field(dialog, /Price, USD/), { target: { value: '0' } });
    expect(within(dialog).getByRole('alert').textContent).toBe('Price must be a positive number');
    fireEvent.change(field(dialog, /Price, USD/), { target: { value: '1,990.5' } });
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-10-01' } });
    expect(within(dialog).getByRole('alert').textContent).toBe('End date is before start date');
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '' } });
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(create(dialog).disabled).toBe(false);
    expect((field(dialog, /Recurrence/) as HTMLSelectElement).disabled).toBe(false);
    expect(
      within(field(dialog, /Recurrence/))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['day', 'week', 'month', 'year']);
  });

  it.each([
    ['monthly', 1],
    ['daily', 3],
    ['weekly', 2],
    ['monthly', 3],
    ['yearly', 1],
  ] as const)('creates an offering priced every %s × %s', async (unit, length) => {
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
    fireEvent.change(field(dialog, /Price, USD/), { target: { value: '1,990.5' } });
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-12-31' } });
    fireEvent.change(field(dialog, /Recurrence/), { target: { value: unit } });
    fireEvent.change(field(dialog, /Charge every/), { target: { value: String(length) } });
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
    expect(String(priceCall?.[1]?.body)).toContain('"price":{"unit":"USD","value":1990.50}');
    expect(JSON.parse(String(priceCall?.[1]?.body))).toEqual({
      '@type': 'ProductOfferingPrice',
      name: `Mobile XL ${unit}`,
      lifecycleStatus: 'Active',
      priceType: 'recurring',
      recurringChargePeriodType: unit,
      ...(unit !== 'monthly' || length !== 1 ? { recurringChargePeriodLength: length } : {}),
      price: { unit: 'USD', value: 1990.5 },
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
    fireEvent.change(field(dialog, /Price, USD/), { target: { value: '10' } });
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
    expect(within(dialog).getByText('399.00 USD / month · 2026-10-05 – open-ended')).toBeTruthy();
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
  const priceField = (form: HTMLElement) => within(form).getByLabelText(/New price, USD/);
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
            newPrice: { id: 'pr9', href: '/p9', price: usd(1390), validFrom: '2026-11-01' },
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
      'Price of MOB-L changes to 1,390.00 USD from 2026-11-01',
    );
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/priceChange'));
    expect(String(call?.[1]?.body)).toBe(
      '{"amount":1390.00,"currency":"USD","validFrom":"2026-11-01"}',
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
            message: 'a recurring price in USD already starts on or after 2026-11-01',
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
        'a recurring price in USD already starts on or after 2026-11-01',
      ),
    ).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});

describe('offering attributes TMF620 makes optional', () => {
  it('shows an offering without validFor as valid at any time', () => {
    const offering = {
      id: ids(9),
      name: 'Any time',
      code: null,
      status: 'Active' as const,
      specification: '',
      validFrom: null,
      validTo: null,
      prices: [],
    };
    expect(validity(offering, 'Europe/Moscow')).toBe('any time – open-ended');
  });

  it('takes the code from the offeringCode entry, not the first identifier', () => {
    const entry = (id: string, externalIdentifierType: string) => ({
      '@type': 'ExternalIdentifier',
      id,
      externalIdentifierType,
    });
    const externalIdentifier = [entry('SKU-1', 'sku'), entry('MOB-L', 'offeringCode')];
    expect(offeringCode({ externalIdentifier })).toBe('MOB-L');
    expect(offeringCode({ externalIdentifier: [entry('SKU-1', 'sku')] })).toBeNull();
  });
});

describe('specifications', () => {
  const SPECS_URL = `GET ${CAT}/productSpecification?offset=0&limit=100`;
  const SPEC_ROWS = [
    {
      id: 's1',
      name: 'Mobile Voice & Data',
      description: 'Voice and data',
      lifecycleStatus: 'Active',
      brand: 'Acme',
      version: '2.1',
      productNumber: 'PN-1',
      lastUpdate: '2026-09-30T21:30:00Z',
    },
    { id: 's2', name: 'Home Internet', lifecycleStatus: 'In Design' },
    { id: 's3', name: 'TV', lifecycleStatus: 'Retired' },
  ];
  // Mobile L uses s1; Fiber 100 and TV Basic use s2 and s3 is unused
  const USING = OFFERINGS.map((o, i) => ({
    ...o,
    productSpecification: { id: i === 0 ? 's1' : 's2', name: o.productSpecification.name },
  }));
  const specBackend = (routes: Record<string, Handler> = {}) =>
    backend({ [SPECS_URL]: () => json(SPEC_ROWS, { total: 3 }), ...routes }, USING);

  async function openTab() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Specifications' }));
    await screen.findByText('Mobile Voice & Data');
  }
  const specRow = (name: string) =>
    screen
      .getAllByRole('row')
      .find(
        (r) => within(r).queryByText(name) && within(r).queryAllByRole('cell').length === 6,
      ) as HTMLElement;
  const field = (dialog: HTMLElement, label: RegExp) => within(dialog).getByLabelText(label);

  it('lists specifications with status, brand, version, offerings and update', async () => {
    specBackend();
    await openTab();
    expectUi('ui.catalog.specifications');
    expect(
      within(specRow('Mobile Voice & Data'))
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Mobile Voice & Data', 'Active', 'Acme', '2.1', '1', '2026-10-01']);
    expect(within(specRow('Home Internet')).getAllByRole('cell')[4]?.textContent).toBe('3');
    expect(within(specRow('TV')).getAllByRole('cell')[4]?.textContent).toBe('0');
  });

  it('creates a specification with the exact body', async () => {
    const { fetchMock } = specBackend({
      [`POST ${CAT}/productSpecification`]: () =>
        json({ id: 's9', name: 'Roaming' }, { status: 201 }),
    });
    await openTab();
    fireEvent.click(screen.getByRole('button', { name: '+ New specification' }));
    const dialog = screen.getByRole('dialog', { name: 'New product specification' });
    expectUi('ui.catalog.newSpecification');
    const create = within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect((field(dialog, /Lifecycle status/) as HTMLSelectElement).value).toBe('In Design');
    fireEvent.change(field(dialog, /^Name/), { target: { value: ' Roaming ' } });
    fireEvent.change(field(dialog, /Brand/), { target: { value: 'Acme' } });
    fireEvent.change(field(dialog, /Version/), { target: { value: '1.0' } });
    fireEvent.change(field(dialog, /Product number/), { target: { value: 'PN-9' } });
    fireEvent.click(create);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Specification Roaming created',
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      '@type': 'ProductSpecification',
      name: 'Roaming',
      lifecycleStatus: 'In Design',
      brand: 'Acme',
      version: '1.0',
      productNumber: 'PN-9',
    });
  });

  it('edits with a merge patch of only the changed fields, forward lifecycle only', async () => {
    const { fetchMock } = specBackend({
      [`PATCH ${CAT}/productSpecification/s1`]: () => json({ id: 's1', name: 'Mobile' }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile Voice & Data' });
    expectUi('ui.catalog.editSpecification');
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed
    expect(
      within(field(dialog, /Lifecycle status/))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Active', 'Retired']);
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Mobile' } });
    fireEvent.change(field(dialog, /Brand/), { target: { value: '' } });
    fireEvent.change(field(dialog, /Lifecycle status/), { target: { value: 'Retired' } });
    fireEvent.click(save);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Specification Mobile saved',
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      name: 'Mobile',
      brand: null,
      lifecycleStatus: 'Retired',
    });
    expect((call?.[1]?.headers as Record<string, string>)['Content-Type']).toBe(
      'application/merge-patch+json',
    );
  });

  it('offers no lifecycle step after Retired', async () => {
    specBackend();
    await openTab();
    fireEvent.click(specRow('TV'));
    const dialog = screen.getByRole('dialog', { name: 'TV' });
    expect(
      within(field(dialog, /Lifecycle status/))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Retired']);
  });

  it('shows a refused save and keeps the entered values', async () => {
    specBackend({
      [`PATCH ${CAT}/productSpecification/s1`]: () => tmfError(409, 'Retired is final'),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(field(dialog, /Brand/), { target: { value: 'Other' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect((field(dialog, /Brand/) as HTMLInputElement).value).toBe('Other');
  });

  it('deletes an unused specification and hides Delete for a used one', async () => {
    const { fetchMock } = specBackend({
      [`DELETE ${CAT}/productSpecification/s3`]: () => new Response(null, { status: 204 }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    let dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(dialog).getByText(/Used by 1 offering/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.click(specRow('TV'));
    dialog = screen.getByRole('dialog', { name: 'TV' });
    expectUi('ui.catalog.deleteSpecification');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Specification TV deleted',
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(call?.[1]?.body).toBeUndefined();
  });

  it('shows the 409 of a delete the server refuses', async () => {
    specBackend({
      [`DELETE ${CAT}/productSpecification/s3`]: () => tmfError(409, 'still referenced'),
    });
    await openTab();
    fireEvent.click(specRow('TV'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
  });

  it('creates a specification from New offering and preselects it', async () => {
    const { fetchMock } = specBackend({
      [`POST ${CAT}/productSpecification`]: () =>
        json({ id: 's9', name: 'Roaming', lifecycleStatus: 'In Design' }, { status: 201 }),
    });
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: '+ New offering' }));
    const offering = screen.getByRole('dialog', { name: 'New product offering' });
    fireEvent.click(within(offering).getByRole('button', { name: 'New specification…' }));
    const dialog = screen.getByRole('dialog', { name: 'New product specification' });
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Roaming' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'New product specification' })).toBeNull(),
    );
    const select = within(offering).getByLabelText(/Specification/) as HTMLSelectElement;
    expect(select.value).toBe('s9');
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      '@type': 'ProductSpecification',
      name: 'Roaming',
      lifecycleStatus: 'In Design',
    });
    // the offering itself is not created yet
    expect(fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(1);
  });
});

describe('offering detail, edit and delete', () => {
  const DETAIL = {
    ...OFFERINGS[2],
    description: 'Basic TV',
    isSellable: true,
    category: [{ id: 'c1', name: 'TV bundles' }],
    externalIdentifier: [
      {
        id: 'TV-BASE',
        externalIdentifierType: 'offeringCode',
        owner: 'bss',
        '@type': 'ExternalIdentifier',
      },
      { id: 'CRM-7', externalIdentifierType: 'crm', '@type': 'ExternalIdentifier' },
    ],
    productSpecification: { id: 's3', name: 'TV' },
    validFor: { startDateTime: '2026-10-04T21:00:00Z', endDateTime: '2026-12-31T21:00:00Z' },
    productOfferingPrice: [],
  };
  // TV Basic has no price at all, so Delete is offered; the list agrees
  const LIST = OFFERINGS.map((o) => (o.id === ids(3) ? { ...o, productOfferingPrice: [] } : o));
  const CATEGORIES = [
    { id: 'c1', name: 'TV bundles' },
    { id: 'c2', name: 'Promo' },
  ];
  const tvBackend = (routes: Record<string, Handler> = {}) =>
    backend(
      {
        [`GET ${CAT}/productOffering/${ids(3)}`]: () => json(DETAIL),
        [`GET ${CAT}/category?offset=0&limit=100`]: () => json(CATEGORIES, { total: 2 }),
        ...routes,
      },
      LIST,
    );

  async function details(name: string) {
    await open();
    await waitFor(() => row(name));
    fireEvent.click(row(name));
    const dialog = screen.getByRole('dialog', { name });
    await within(dialog).findByText('Basic TV');
    return dialog;
  }
  const bodyOf = (fetchMock: { mock: { calls: unknown[][] } }, method: string) => {
    const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === method);
    return String((call?.[1] as RequestInit | undefined)?.body);
  };

  it('shows the fields, specification, code and categories of the offering', async () => {
    tvBackend();
    const dialog = await details('TV Basic');
    expectUi('ui.catalog.offeringDetail');
    expect(within(dialog).getByText('TV bundles')).toBeTruthy();
    expect(within(dialog).getByText('TV-BASE')).toBeTruthy();
    expect(within(dialog).getByText('TV')).toBeTruthy();
    expect(within(dialog).getByText('yes')).toBeTruthy();
  });

  it('patches only the changed fields and keeps the other identifiers', async () => {
    const { fetchMock } = tvBackend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit offering' }));
    const edit = screen.getByRole('dialog', { name: 'Edit TV Basic' });
    expectUi('ui.catalog.editOffering');
    expect(within(edit).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    await within(edit).findByRole('checkbox', { name: 'Promo' });
    fireEvent.change(within(edit).getByLabelText(/^Name/), { target: { value: 'TV Basic 2' } });
    fireEvent.change(within(edit).getByLabelText(/^Code/), { target: { value: 'tv-2' } });
    fireEvent.change(within(edit).getByLabelText(/^Valid to/), { target: { value: '2027-01-31' } });
    fireEvent.click(within(edit).getByRole('checkbox', { name: 'Promo' }));
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Offering TV-2 saved');
    expect(JSON.parse(bodyOf(fetchMock, 'PATCH'))).toEqual({
      name: 'TV Basic 2',
      // the start is sent as loaded; the end is the midnight after the last valid day
      validFor: {
        startDateTime: '2026-10-04T21:00:00Z',
        endDateTime: '2027-02-01T00:00:00+03:00',
      },
      // the offeringCode entry changes, the crm entry stays as loaded
      externalIdentifier: [
        {
          id: 'TV-2',
          externalIdentifierType: 'offeringCode',
          owner: 'bss',
          '@type': 'ExternalIdentifier',
        },
        { id: 'CRM-7', externalIdentifierType: 'crm', '@type': 'ExternalIdentifier' },
      ],
      category: [
        { id: 'c1', '@type': 'CategoryRef' },
        { id: 'c2', '@type': 'CategoryRef' },
      ],
    });
  });

  it('sends the specification, sellable and an emptied description', async () => {
    const { fetchMock } = tvBackend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit offering' }));
    const edit = screen.getByRole('dialog', { name: 'Edit TV Basic' });
    fireEvent.change(within(edit).getByLabelText(/^Specification/), { target: { value: 's1' } });
    fireEvent.change(within(edit).getByLabelText(/^Description/), { target: { value: '' } });
    fireEvent.click(within(edit).getByRole('checkbox', { name: 'Sellable' }));
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(JSON.parse(bodyOf(fetchMock, 'PATCH'))).toEqual({
      description: null,
      isSellable: false,
      productSpecification: { id: 's1', '@type': 'ProductSpecificationRef' },
    });
  });

  it('shows the refusal of an edit and keeps the entered values', async () => {
    tvBackend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () =>
        tmfError(409, 'productSpecification cannot change: the offering was ordered'),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit offering' }));
    const edit = screen.getByRole('dialog', { name: 'Edit TV Basic' });
    fireEvent.change(within(edit).getByLabelText(/^Specification/), { target: { value: 's1' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    expect((await within(edit).findByRole('alert')).textContent).toMatch(/was ordered/);
    expect((within(edit).getByLabelText(/^Specification/) as HTMLSelectElement).value).toBe('s1');
  });

  it('deletes an offering with no prices after confirming, and shows a refusal', async () => {
    const { fetchMock } = tvBackend({
      [`DELETE ${CAT}/productOffering/${ids(3)}`]: () =>
        tmfError(409, 'productOffering is referenced: retire it instead'),
    });
    const dialog = await details('TV Basic');
    expectUi('ui.catalog.deleteOffering');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect((await within(dialog).findByRole('alert')).textContent).toMatch(/retire it instead/);
  });

  it('deletes with no body and reports it', async () => {
    const { fetchMock } = tvBackend({
      [`DELETE ${CAT}/productOffering/${ids(3)}`]: () => new Response(null, { status: 204 }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering TV-BASE deleted',
    );
    const call = fetchMock.mock.calls.find(([, i]) => i?.method === 'DELETE');
    expect(call?.[1]?.body).toBeUndefined();
  });

  it('offers no delete for an Active offering or one with prices', async () => {
    tvBackend();
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(row('Mobile L')); // Active
    const dialog = screen.getByRole('dialog', { name: 'Mobile L' });
    await within(dialog).findByRole('button', { name: 'Edit offering' });
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
  });

  describe('prices', () => {
    const PRICE = {
      id: 'pr1',
      name: 'Mobile L monthly',
      priceType: 'recurring',
      recurringChargePeriodType: 'monthly',
      price: usd(1290),
      validFor: { startDateTime: '2026-06-01T21:00:00Z' },
    };
    const priceBackend = (routes: Record<string, Handler> = {}) =>
      backend({
        [`GET ${CAT}/productOfferingPrice/pr1`]: () => json(PRICE),
        ...routes,
      });
    async function openPrice() {
      await open();
      await waitFor(() => row('Mobile L'));
      fireEvent.click(row('Mobile L'));
      const dialog = screen.getByRole('dialog', { name: 'Mobile L' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Open price 1,290.00' }));
      return screen.findByRole('dialog', { name: 'Mobile L monthly' });
    }

    it('opens a price and patches the changed name and the exact amount', async () => {
      const { fetchMock } = priceBackend({
        [`PATCH ${CAT}/productOfferingPrice/pr1`]: () => json(PRICE),
      });
      const price = await openPrice();
      expectUi('ui.catalog.priceDetail');
      expect(within(price).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
      fireEvent.change(within(price).getByLabelText(/^Name/), { target: { value: 'Mobile L 2' } });
      fireEvent.change(within(price).getByLabelText(/^Price,/), { target: { value: '1300.5' } });
      fireEvent.click(within(price).getByRole('button', { name: 'Save' }));
      await screen.findByRole('status');
      expect(bodyOf(fetchMock, 'PATCH')).toBe(
        '{"name":"Mobile L 2","price":{"unit":"USD","value":1300.50}}',
      );
    });

    it('shows the 409 for a charge change and points to Change price', async () => {
      priceBackend({
        [`PATCH ${CAT}/productOfferingPrice/pr1`]: () =>
          tmfError(409, 'the charge cannot change: end its validFor and attach a new price'),
      });
      const price = await openPrice();
      fireEvent.change(within(price).getByLabelText(/^Price,/), { target: { value: '1400' } });
      fireEvent.click(within(price).getByRole('button', { name: 'Save' }));
      const alert = await within(price).findByRole('alert');
      expect(alert.textContent).toMatch(/end its validFor and attach a new price/);
      expect(alert.textContent).toMatch(/Change price/);
      expect((within(price).getByLabelText(/^Price,/) as HTMLInputElement).value).toBe('1400');
    });

    it('ends a price on a chosen day, from tomorrow, keeping its start', async () => {
      const { fetchMock } = priceBackend({
        [`PATCH ${CAT}/productOfferingPrice/pr1`]: () => json(PRICE),
      });
      const price = await openPrice();
      expectUi('ui.catalog.endPrice');
      fireEvent.click(within(price).getByRole('button', { name: 'End price' }));
      const day = within(price).getByLabelText('Price ends on') as HTMLInputElement;
      expect(day.value).toBe('2026-10-02');
      fireEvent.change(day, { target: { value: '2026-11-01' } });
      fireEvent.click(within(price).getByRole('button', { name: 'End on this date' }));
      await screen.findByRole('status');
      expect(JSON.parse(bodyOf(fetchMock, 'PATCH'))).toEqual({
        validFor: {
          startDateTime: '2026-06-01T21:00:00Z',
          endDateTime: '2026-11-01T00:00:00+03:00',
        },
      });
    });

    it('deletes a price after confirming and shows the refusal', async () => {
      const { fetchMock } = priceBackend({
        [`DELETE ${CAT}/productOfferingPrice/pr1`]: () =>
          tmfError(409, 'a price of an ordered offering stays; end its validFor instead'),
      });
      const price = await openPrice();
      expectUi('ui.catalog.deletePrice');
      fireEvent.click(within(price).getByRole('button', { name: 'Delete' }));
      expect(fetchMock.mock.calls.some(([, i]) => i?.method === 'DELETE')).toBe(false);
      fireEvent.click(within(price).getByRole('button', { name: 'Yes' }));
      expect((await within(price).findByRole('alert')).textContent).toMatch(/stays/);
    });
  });
});

describe('categories and catalogs', () => {
  const CATS_URL = `GET ${CAT}/category?offset=0&limit=100`;
  const CATALOGS_URL = `GET ${CAT}/productCatalog?offset=0&limit=100`;
  const ref = (id: string, name: string) => ({ id, name });
  const CATS = [
    {
      id: 'c1',
      name: 'Mobile',
      lifecycleStatus: 'Active',
      validFor: { startDateTime: '2026-06-01T21:00:00Z' },
      subCategory: [ref('c2', 'Plans')],
      productOffering: [ref(ids(1), 'Mobile L')],
    },
    {
      id: 'c2',
      name: 'Plans',
      lifecycleStatus: 'In Design',
      parent: ref('c1', 'Mobile'),
      subCategory: [ref('c3', 'Promo')],
      productOffering: [],
    },
    {
      id: 'c3',
      name: 'Promo',
      lifecycleStatus: 'In Design',
      parent: ref('c2', 'Plans'),
      subCategory: [],
      productOffering: [],
    },
    { id: 'c4', name: 'Home', lifecycleStatus: 'Active', subCategory: [], productOffering: [] },
  ];
  const CATALOGS = [
    {
      id: 'k1',
      name: 'Retail',
      catalogType: 'sales',
      lifecycleStatus: 'Active',
      category: [ref('c1', 'Mobile')],
    },
    { id: 'k2', name: 'Draft', lifecycleStatus: 'In Design', category: [] },
  ];
  const routes = (more: Record<string, Handler> = {}) =>
    backend({
      [CATS_URL]: () => json(CATS, { total: CATS.length }),
      [CATALOGS_URL]: () => json(CATALOGS, { total: CATALOGS.length }),
      ...more,
    });
  const field = (dialog: HTMLElement, label: RegExp) => within(dialog).getByLabelText(label);
  const bodyOf = (fetchMock: ReturnType<typeof stubFetch>['fetchMock'], method: string) => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === method);
    return call?.[1]?.body === undefined ? undefined : JSON.parse(String(call[1].body));
  };
  const rowOf = (name: string): HTMLElement => {
    const found = screen
      .getAllByRole('row')
      .find((r) => within(r).queryByText(name) && within(r).queryAllByRole('cell').length === 4);
    if (!found) throw new Error(`no row ${name}`);
    return found;
  };
  const treeRow = rowOf;

  async function openCategories() {
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: 'Categories' }));
    await screen.findByText('Promo');
  }
  async function openCatalogs() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Catalogs' }));
    await screen.findByText('Retail');
  }

  it('shows the categories as a tree, roots first, with counts', async () => {
    routes();
    await openCategories();
    expectUi('ui.catalog.categories');
    const names = screen
      .getAllByRole('row')
      .filter((r) => within(r).queryAllByRole('cell').length === 4)
      .map((r) => within(r).getAllByRole('cell')[0]?.textContent);
    expect(names).toEqual(['Home', 'Mobile', 'Plans', 'Promo']);
    const cells = (name: string) =>
      within(treeRow(name))
        .getAllByRole('cell')
        .map((c) => c.textContent);
    expect(cells('Mobile')).toEqual(['Mobile', 'Active', '1', '1']);
    expect(cells('Plans')).toEqual(['Plans', 'In design', '0', '1']);
    const indent = (name: string) =>
      (within(treeRow(name)).getByText(name) as HTMLElement).style.getPropertyValue(
        '--category-indent',
      );
    expect([indent('Mobile'), indent('Plans'), indent('Promo')]).toEqual(['0px', '20px', '40px']);
  });

  it('creates a category with the exact body', async () => {
    const { fetchMock } = routes({
      [`POST ${CAT}/category`]: () => json({ id: 'c9', name: 'Roaming' }, { status: 201 }),
    });
    await openCategories();
    fireEvent.click(screen.getByRole('button', { name: '+ New category' }));
    const dialog = screen.getByRole('dialog', { name: 'New category' });
    expectUi('ui.catalog.newCategory');
    const create = within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(field(dialog, /^Name/), { target: { value: ' Roaming ' } });
    fireEvent.change(field(dialog, /Description/), { target: { value: 'Abroad' } });
    fireEvent.change(field(dialog, /Parent category/), { target: { value: 'c4' } });
    // an end without a start is refused by the server for sure: Create stays disabled
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-12-31' } });
    expect(create.disabled).toBe(true);
    expect(within(dialog).getByText(/needs a start date/)).toBeTruthy();
    fireEvent.change(field(dialog, /Valid from/), { target: { value: '2026-11-01' } });
    fireEvent.click(within(dialog).getByLabelText('Mobile L'));
    fireEvent.click(create);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Category Roaming created',
    );
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      '@type': 'Category',
      name: 'Roaming',
      description: 'Abroad',
      lifecycleStatus: 'In Design',
      validFor: {
        startDateTime: '2026-11-01T00:00:00+03:00',
        endDateTime: '2027-01-01T00:00:00+03:00',
      },
      parent: { id: 'c4', '@type': 'CategoryRef' },
      productOffering: [{ id: ids(1), '@type': 'ProductOfferingRef' }],
    });
  });

  it('keeps the parent select free of the category and everything below it', async () => {
    routes();
    await openCategories();
    fireEvent.click(treeRow('Mobile'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile' });
    expectUi('ui.catalog.editCategory');
    const options = within(field(dialog, /Parent category/)).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['None (a root)', 'Home']);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.click(treeRow('Plans'));
    const plans = screen.getByRole('dialog', { name: 'Plans' });
    expect(
      within(field(plans, /Parent category/))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['None (a root)', 'Home', 'Mobile']);
  });

  it('patches only the changed fields and replaces the membership as a whole', async () => {
    const { fetchMock } = routes({
      [`PATCH ${CAT}/category/c1`]: () => json({ id: 'c1', name: 'Mobile 2' }),
    });
    await openCategories();
    fireEvent.click(treeRow('Mobile'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile' });
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Mobile 2' } });
    // forward lifecycle only: Active -> Retired
    expect(
      within(field(dialog, /Lifecycle status/))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Active', 'Retired']);
    fireEvent.change(field(dialog, /Lifecycle status/), { target: { value: 'Retired' } });
    fireEvent.click(within(dialog).getByLabelText('Mobile L')); // out
    fireEvent.click(within(dialog).getByLabelText('Fiber 100')); // in
    fireEvent.click(within(dialog).getByLabelText('TV Basic')); // in
    fireEvent.click(save);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Category Mobile 2 saved',
    );
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({
      name: 'Mobile 2',
      lifecycleStatus: 'Retired',
      productOffering: [
        { id: ids(2), '@type': 'ProductOfferingRef' },
        { id: ids(3), '@type': 'ProductOfferingRef' },
      ],
    });
  });

  it('moves a category under a parent and back to a root with a null parent', async () => {
    const { fetchMock } = routes({
      [`PATCH ${CAT}/category/c3`]: () => json({ id: 'c3', name: 'Promo' }),
    });
    await openCategories();
    fireEvent.click(treeRow('Promo'));
    const dialog = screen.getByRole('dialog', { name: 'Promo' });
    fireEvent.change(field(dialog, /Parent category/), { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({ parent: null });
  });

  it('shows the refusal of a save and keeps the entered values', async () => {
    routes({ [`PATCH ${CAT}/category/c1`]: () => tmfError(409, 'category hierarchy has a cycle') });
    await openCategories();
    fireEvent.click(treeRow('Mobile'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile' });
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Other' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect((field(dialog, /^Name/) as HTMLInputElement).value).toBe('Other');
  });

  it('offers Delete only for a category without subcategories and offerings', async () => {
    const { fetchMock } = routes({
      [`DELETE ${CAT}/category/c3`]: () => new Response(null, { status: 204 }),
    });
    await openCategories();
    fireEvent.click(treeRow('Mobile'));
    let dialog = screen.getByRole('dialog', { name: 'Mobile' });
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.click(treeRow('Promo'));
    dialog = screen.getByRole('dialog', { name: 'Promo' });
    expectUi('ui.catalog.deleteCategory');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Category Promo deleted',
    );
    expect(bodyOf(fetchMock, 'DELETE')).toBeUndefined();
  });

  it('shows the 409 of a delete the server refuses (catalog membership)', async () => {
    routes({ [`DELETE ${CAT}/category/c4`]: () => tmfError(409, 'used by a catalog') });
    await openCategories();
    fireEvent.click(treeRow('Home'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
  });

  it('lists catalogs with type, status and root category names', async () => {
    routes();
    await openCatalogs();
    expectUi('ui.catalog.catalogs');
    const cells = (name: string) =>
      within(rowOf(name))
        .getAllByRole('cell')
        .map((c) => c.textContent);
    expect(cells('Retail')).toEqual(['Retail', 'sales', 'Active', 'Mobile']);
    expect(cells('Draft')).toEqual(['Draft', '—', 'In design', '—']);
  });

  it('creates a catalog with root categories only and the exact body', async () => {
    const { fetchMock } = routes({
      [`POST ${CAT}/productCatalog`]: () => json({ id: 'k9', name: 'Wholesale' }, { status: 201 }),
    });
    await openCatalogs();
    fireEvent.click(screen.getByRole('button', { name: '+ New catalog' }));
    const dialog = screen.getByRole('dialog', { name: 'New product catalog' });
    expectUi('ui.catalog.newCatalog');
    // only the roots are listed
    const group = within(dialog).getByRole('group', { name: 'Root categories' });
    expect(
      within(group)
        .getAllByRole('checkbox')
        .map((c) => c.parentElement?.textContent?.trim()),
    ).toEqual(['Mobile', 'Home']);
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Wholesale' } });
    fireEvent.change(field(dialog, /Catalog type/), { target: { value: 'b2b' } });
    fireEvent.change(field(dialog, /Valid from/), { target: { value: '2026-11-01' } });
    fireEvent.click(within(group).getByLabelText('Home'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Catalog Wholesale created',
    );
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      '@type': 'ProductCatalog',
      name: 'Wholesale',
      lifecycleStatus: 'In Design',
      validFor: { startDateTime: '2026-11-01T00:00:00+03:00' },
      catalogType: 'b2b',
      category: [{ id: 'c4', '@type': 'CategoryRef' }],
    });
  });

  it('patches only the changed catalog fields, nulling an emptied type', async () => {
    const { fetchMock } = routes({
      [`PATCH ${CAT}/productCatalog/k1`]: () => json({ id: 'k1', name: 'Retail' }),
    });
    await openCatalogs();
    fireEvent.click(screen.getByText('Retail'));
    const dialog = screen.getByRole('dialog', { name: 'Retail' });
    expectUi('ui.catalog.editCatalog');
    fireEvent.change(field(dialog, /Catalog type/), { target: { value: '' } });
    fireEvent.click(within(dialog).getByLabelText('Mobile')); // out
    fireEvent.click(within(dialog).getByLabelText('Home')); // in
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(bodyOf(fetchMock, 'PATCH')).toEqual({
      catalogType: null,
      category: [{ id: 'c4', '@type': 'CategoryRef' }],
    });
  });

  it('always offers Delete for a catalog and deletes with no body', async () => {
    const { fetchMock } = routes({
      [`DELETE ${CAT}/productCatalog/k1`]: () => new Response(null, { status: 204 }),
    });
    await openCatalogs();
    fireEvent.click(screen.getByText('Retail'));
    const dialog = screen.getByRole('dialog', { name: 'Retail' });
    expectUi('ui.catalog.deleteCatalog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Catalog Retail deleted',
    );
    expect(bodyOf(fetchMock, 'DELETE')).toBeUndefined();
  });
});

describe('import and export jobs', () => {
  const JOBS = `${CAT}/importJob?offset=0&limit=100`;
  const EXPORTS = `${CAT}/exportJob?offset=0&limit=100`;
  const CATALOG_LIST = `GET ${CAT}/productCatalog?offset=0&limit=100`;
  const DOC = '{"@type":"BssCatalogDocument","version":1}';
  const DOC_URL =
    'data:application/json;base64,eyJAdHlwZSI6IkJzc0NhdGFsb2dEb2N1bWVudCIsInZlcnNpb24iOjF9';
  const base = '/tmf-api/productCatalogManagement/v5';
  const IMPORTS = [
    {
      id: 'i1',
      status: 'Failed',
      creationDate: '2026-10-20T09:00:00Z',
      completionDate: '2026-10-20T09:00:01Z',
      errorLog: 'category X: parents form a cycle\nsecond line',
    },
    { id: 'i2', status: 'Succeeded', creationDate: '2026-10-22T09:00:00Z' },
  ];
  const EXPORT_LIST = [
    { id: 'e1', status: 'Succeeded', creationDate: '2026-10-21T09:00:00Z', url: DOC_URL },
  ];
  const routes = (more: Record<string, Handler> = {}) =>
    backend({
      [`GET ${JOBS}`]: () => json(IMPORTS, { total: IMPORTS.length }),
      [`GET ${EXPORTS}`]: () => json(EXPORT_LIST, { total: EXPORT_LIST.length }),
      [CATALOG_LIST]: () => json([{ id: 'k1', name: 'Retail' }], { total: 1 }),
      ...more,
    });
  const bodyOf = (fetchMock: ReturnType<typeof stubFetch>['fetchMock'], method: string) => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === method);
    return call?.[1]?.body === undefined ? undefined : JSON.parse(String(call[1].body));
  };
  async function openJobs() {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Import / export' }));
    await screen.findByText('3 jobs');
  }

  it('lists both kinds newest first with the first line of the error log', async () => {
    routes();
    await openJobs();
    expectUi('ui.catalog.jobs');
    const rows = screen
      .getAllByRole('row')
      .filter((r) => within(r).queryAllByRole('cell').length === 5)
      .map((r) =>
        within(r)
          .getAllByRole('cell')
          .map((c) => c.textContent),
      );
    expect(rows.map((r) => r[0])).toEqual(['Import', 'Export', 'Import']);
    expect(rows[2]?.[1]).toBe('Failed');
    expect(rows[2]?.[4]).toBe('category X: parents form a cycle');
    expect(rows[0]?.[2]).toBe('2026-10-22 12:00:00');
  });

  it('exports with the exact body, then downloads and copies the document as an import', async () => {
    const created: unknown[] = [];
    const { fetchMock } = routes({
      [`POST ${base}/exportJob`]: () => {
        created.push(1);
        return json(
          { id: 'e9', status: 'Succeeded', creationDate: '2026-10-25T09:00:00Z', url: DOC_URL },
          { status: 201 },
        );
      },
    });
    const blobs: Blob[] = [];
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      blobs.push(blob as Blob);
      return 'blob:x';
    };
    URL.revokeObjectURL = () => undefined;
    const names: string[] = [];
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      names.push(this.download);
    };
    try {
      await openJobs();
      fireEvent.click(screen.getByRole('button', { name: 'Export...' }));
      const dialog = screen.getByRole('dialog', { name: 'Export the catalog' });
      expectUi('ui.catalog.export');
      await within(dialog).findByRole('option', { name: 'Retail' });
      fireEvent.change(within(dialog).getByLabelText('Scope'), { target: { value: 'k1' } });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Export' }));
      fireEvent.click(await within(dialog).findByRole('button', { name: 'Download' }));
      expect(bodyOf(fetchMock, 'POST')).toEqual({
        '@type': 'ExportJob',
        path: base,
        contentType: 'application/json',
        query: 'productCatalog.id=k1',
      });
      expect(names).toEqual(['bss-catalog-2026-10-25.json']);
      expect(blobs[0]?.type).toBe('application/json');
      expect(await blobs[0]?.text()).toBe(DOC);
      fireEvent.click(within(dialog).getByRole('button', { name: 'Copy as import' }));
      const importing = screen.getByRole('dialog', { name: 'Import a catalog document' });
      expect(
        (within(importing).getByLabelText(/paste the document/i) as HTMLTextAreaElement).value,
      ).toBe(DOC);
    } finally {
      HTMLAnchorElement.prototype.click = click;
    }
  });

  it('exports everything with no query', async () => {
    const { fetchMock } = routes({
      [`POST ${base}/exportJob`]: () =>
        json({ id: 'e9', status: 'Succeeded', url: DOC_URL }, { status: 201 }),
    });
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Export...' }));
    const dialog = screen.getByRole('dialog', { name: 'Export the catalog' });
    await within(dialog).findByRole('option', { name: 'Retail' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Export' }));
    await within(dialog).findByRole('button', { name: 'Download' });
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      '@type': 'ExportJob',
      path: base,
      contentType: 'application/json',
    });
  });

  it('imports a pasted document as a base64 data URL', async () => {
    const { fetchMock } = routes({
      [`POST ${base}/importJob`]: () =>
        json({ id: 'i9', status: 'Succeeded', url: DOC_URL }, { status: 201 }),
    });
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    expectUi('ui.catalog.import');
    const button = within(dialog).getByRole('button', { name: 'Import' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    const box = within(dialog).getByLabelText(/paste the document/i);
    fireEvent.change(box, { target: { value: '{oops' } });
    expect(within(dialog).getByText('The document is not valid JSON')).toBeTruthy();
    expect(button.disabled).toBe(true);
    fireEvent.change(box, { target: { value: DOC } });
    fireEvent.click(button);
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Import job succeeded');
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      '@type': 'ImportJob',
      path: base,
      contentType: 'application/json',
      url: DOC_URL,
    });
  });

  it('encodes non-ASCII text as UTF-8', async () => {
    const { fetchMock } = routes({
      [`POST ${base}/importJob`]: () => json({ id: 'i9', status: 'Succeeded' }, { status: 201 }),
    });
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    fireEvent.change(within(dialog).getByLabelText(/paste the document/i), {
      target: { value: '{"name":"Тариф"}' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
    await screen.findByRole('status');
    expect(bodyOf(fetchMock, 'POST').url).toBe(
      'data:application/json;base64,eyJuYW1lIjoi0KLQsNGA0LjRhCJ9',
    );
  });

  it('reads a chosen .json file into the box', async () => {
    routes();
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    const file = new File([DOC], 'catalog.json', { type: 'application/json' });
    fireEvent.change(within(dialog).getByLabelText('JSON file'), { target: { files: [file] } });
    await waitFor(() =>
      expect(
        (within(dialog).getByLabelText(/paste the document/i) as HTMLTextAreaElement).value,
      ).toBe(DOC),
    );
  });

  it('refuses a document over 1 MB without sending it', async () => {
    const { fetchMock } = routes();
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    fireEvent.change(within(dialog).getByLabelText(/paste the document/i), {
      target: { value: JSON.stringify({ pad: 'x'.repeat(1_000_000) }) },
    });
    expect(within(dialog).getByText(/larger than 1,000,000 bytes/)).toBeTruthy();
    expect(
      (within(dialog).getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('shows the full error log of a failed import in the dialog', async () => {
    routes({
      [`POST ${base}/importJob`]: () =>
        json(
          { id: 'i9', status: 'Failed', errorLog: 'category X: nope\nmore detail' },
          { status: 201 },
        ),
    });
    await openJobs();
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    fireEvent.change(within(dialog).getByLabelText(/paste the document/i), {
      target: { value: DOC },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
    const log = await within(dialog).findByRole('alert');
    expect(log.textContent).toBe('category X: nope\nmore detail');
    expectUi('ui.catalog.errorLog');
  });

  it('deletes a job record and says nothing imported is undone', async () => {
    const { fetchMock } = routes({
      [`DELETE ${base}/importJob/i1`]: () => new Response(null, { status: 204 }),
    });
    await openJobs();
    const failed = screen.getByText('category X: parents form a cycle');
    fireEvent.click(failed);
    const dialog = screen.getByRole('dialog', { name: 'Import job' });
    expectUi('ui.catalog.job');
    expect(within(dialog).getByRole('alert').textContent).toContain('second line');
    expectUi('ui.catalog.deleteJob');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(within(dialog).getByText(/Nothing it imported is undone/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Import job deleted');
    expect(bodyOf(fetchMock, 'DELETE')).toBeUndefined();
  });
});
