import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { offeringCode } from '../api/bss';
import { validity } from './Catalog';
import { expectUi } from '../test/expectUi';
import { json, tmfError, type Handler } from '../test/fetch';
import {
  CAT,
  usd,
  ids,
  code,
  OFFERINGS,
  PRICES,
  backend,
  open,
  dataRows,
  row,
  cellsOf,
} from '../test/catalogKit';

describe('catalog', () => {
  it('groups in lifecycle order with filtered counts, hides empty groups, and explains the lifecycle', async () => {
    backend();
    await open();
    await waitFor(() => row('Mobile L'));
    const headers = () =>
      screen.getAllByRole('rowheader').map((element) => element.parentElement?.textContent);
    expect(headers()).toEqual([
      'In design1Not sellable yet',
      'Active2Sellable',
      'Retired1Billing only',
    ]);
    expect(
      dataRows().map(
        (element) =>
          within(element).getByText(/^(TV Basic|Mobile L|Fiber 100|Old Mobile)$/).textContent,
      ),
    ).toEqual(['TV Basic', 'Mobile L', 'Fiber 100', 'Old Mobile']);
    expectUi('ui.catalog.lifecycleNote');
    expect(
      screen.getByText(
        'Lifecycle runs one way: In design → Active → Retired. Retired offerings stay billable for products already on them and are hidden from Add product.',
      ),
    ).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search offerings' }), {
      target: { value: 'MOB-L' },
    });
    expect(headers()).toEqual(['Active1Sellable']);
    expect(dataRows()).toHaveLength(1);
  });

  it.each([
    ['TV Basic', ['current', 'later', 'later']],
    ['Mobile L', ['done', 'current', 'later']],
    ['Old Mobile', ['done', 'done', 'current']],
  ])('shows lifecycle progress for %s', async (name, states) => {
    backend();
    await open();
    await waitFor(() => row(name as string));
    fireEvent.click(row(name as string));
    const stepper = within(screen.getByRole('dialog')).getByRole('list', { name: 'Lifecycle' });
    expect(stepper).toBe(expectUi('ui.catalog.lifecycleStepper'));
    expect(
      within(stepper)
        .getAllByRole('listitem')
        .map((item) => item.className),
    ).toEqual(states);
    expect(stepper.querySelector('[aria-current="step"]')).toBeTruthy();
    expectUi('ui.catalog.priceFigure');
  });
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
    await waitFor(() => expect(cellsOf('Mobile L')[2]).toBe('Every 2 weeks'));
    fireEvent.click(row('Mobile L'));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(within(dialog).getByRole('table', { name: 'Prices' })).getByText(/USD \/ 2 weeks ·/),
    ).toBeTruthy();
  });

  it('lists offerings with code, specification, price, period, status and validity', async () => {
    backend();
    await open();
    await waitFor(() => row('Mobile L'));
    expectUi('ui.catalog.list');
    expect(screen.getByRole('columnheader', { name: 'Price USD' })).toBeTruthy();
    expect(cellsOf('Mobile L')).toEqual([
      'Mobile LMOB-LMobile Voice & Data2026-06-02 – open-ended',
      'Active',
      'Monthly',
      '1,290.00',
      '—',
    ]);
    // the end is exclusive (midnight of Jan 1 in Moscow): the last valid day is shown
    expect(within(row('Fiber 100')).getByText('2026-06-02 – 2026-12-31')).toBeTruthy();
    expect(cellsOf('Fiber 100')[3]).toBe('650.00'); // valid now, not tomorrow's 700.00
    expect(cellsOf('TV Basic')[3]).toBe('399.00from 2026-10-05'); // only a future price
    expect(within(row('Old Mobile')).getByText('—', { selector: '.catalog-code' })).toBeTruthy();
    expect(cellsOf('Old Mobile')[3]).toBe('—');
    expect(within(row('Old Mobile')).getByText('Old Mobile').className).toContain('bss-retired');
    expect(screen.getByText('Showing 4 of 4')).toBeTruthy();
    expect(screen.getByText('Prices exclude taxes · taxes are not applied')).toBeTruthy();
    const group = screen.getByRole('group', { name: 'Lifecycle status' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All4', 'In design1', 'Active2', 'Retired1']);
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
    expect(dataRows()).toHaveLength(1);
    expect(within(row('Old Mobile')).getByText('Old Mobile')).toBeTruthy();
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
      screen.getByText('Create the first product offering. It starts in In design.'),
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
    const dialog = screen.getByRole('dialog', { name: 'New product offering' });
    await waitFor(() =>
      expect((within(dialog).getByLabelText('Categories') as HTMLSelectElement).disabled).toBe(
        false,
      ),
    );
    return dialog;
  }
  const field = (dialog: HTMLElement, label: RegExp) => within(dialog).getByLabelText(label);
  const create = (dialog: HTMLElement) =>
    within(dialog).getByRole('button', { name: 'Create as In design' }) as HTMLButtonElement;

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
      [`GET ${CAT}/category?offset=0&limit=100`]: () =>
        json(
          [
            { id: 'c1', name: 'Mobile' },
            { id: 'c2', name: 'Promo' },
          ],
          { total: 2 },
        ),
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
    fireEvent.change(field(dialog, /Description/), { target: { value: 'Unlimited data' } });
    const sellable = within(dialog).getByRole('checkbox', { name: 'Sellable' }) as HTMLInputElement;
    expect(sellable.checked).toBe(true);
    fireEvent.click(sellable);
    const categoryField = field(dialog, /Categories/) as HTMLSelectElement;
    const mobile = await within(categoryField).findByRole('option', { name: 'Mobile' });
    const promo = within(categoryField).getByRole('option', { name: 'Promo' });
    (mobile as HTMLOptionElement).selected = true;
    (promo as HTMLOptionElement).selected = true;
    fireEvent.change(categoryField);
    fireEvent.change(field(dialog, /Specification/), { target: { value: 's1' } });
    fireEvent.change(field(dialog, /Price, USD/), { target: { value: '1,990.5' } });
    fireEvent.change(field(dialog, /Valid to/), { target: { value: '2026-12-31' } });
    fireEvent.change(field(dialog, /Recurrence/), { target: { value: unit } });
    fireEvent.change(field(dialog, /Charge every/), { target: { value: String(length) } });
    fireEvent.click(create(dialog));

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering MOB-XL created in In design',
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
      description: 'Unlimited data',
      isSellable: false,
      category: [
        { id: 'c1', '@type': 'CategoryRef' },
        { id: 'c2', '@type': 'CategoryRef' },
      ],
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

describe('catalog sellability', () => {
  it('marks non-sellable offerings in every lifecycle group', async () => {
    backend(
      {},
      OFFERINGS.filter((offering) =>
        ['TV Basic', 'Mobile L', 'Old Mobile'].includes(offering.name),
      ).map((offering) => ({ ...offering, isSellable: false })),
    );
    await open();
    for (const name of ['TV Basic', 'Mobile L', 'Old Mobile']) {
      await waitFor(() => row(name));
      const marker = within(row(name)).getByText('Not sellable').parentElement;
      expect(marker).toHaveProperty('dataset.ui', 'ui.catalog.notSellable');
    }
  });
});

describe('add price', () => {
  const PAYG = {
    ...OFFERINGS[0],
    id: ids(5),
    name: 'Metered',
    externalIdentifier: code('METERED'),
    productOfferingPrice: [{ id: 'pru' }],
    productOfferingCharacteristic: [
      {
        name: 'offeringKind',
        characteristicValueSpecification: [{ value: 'payAsYouGo' }],
      },
    ],
  };
  const usagePrices = (prices: unknown[] = []) => ({
    [`GET ${CAT}/productOfferingPrice?priceType=usage&offset=0&limit=100`]: () =>
      json(prices, { total: prices.length }),
  });
  async function paygDetails(routes: Record<string, Handler>, prices: unknown[] = []) {
    const stub = backend({ ...usagePrices(prices), ...routes }, [PAYG]);
    await open();
    await waitFor(() => row('Metered'));
    fireEvent.click(row('Metered'));
    return { ...stub, detail: screen.getByRole('dialog', { name: 'Metered' }) };
  }

  it.each([
    ['oneTime', 'One time', 'oneTime', {}],
    ['recurring', 'Monthly recurring', 'monthly', { recurringChargePeriodType: 'monthly' }],
  ] as const)(
    'adds a %s price and preserves every attached price',
    async (type, label, suffix, extra) => {
      const { fetchMock } = backend({
        [`POST ${CAT}/productOfferingPrice`]: () => json({ id: 'new-price' }, { status: 201 }),
        [`PATCH ${CAT}/productOffering/${ids(1)}`]: () => json({ id: ids(1) }),
      });
      await open();
      await waitFor(() => row('Mobile L'));
      fireEvent.click(row('Mobile L'));
      const detail = screen.getByRole('dialog', { name: 'Mobile L' });
      fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
      const dialog = screen.getByRole('dialog', { name: 'Add price · Mobile L' });
      expect(dialog).toBe(expectUi('ui.catalog.addPrice'));
      const from = within(dialog).getByLabelText('Valid from') as HTMLInputElement;
      expect(from.value).toBe('2026-10-01');
      expect(from.min).toBe('2026-10-01');
      fireEvent.change(within(dialog).getByLabelText('Charge type'), { target: { value: type } });
      expect(within(dialog).getByRole('option', { name: label })).toBeTruthy();
      fireEvent.change(within(dialog).getByLabelText('Amount, USD'), {
        target: { value: '125.5' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Add price' }));
      expect(await screen.findByRole('status')).toHaveProperty(
        'textContent',
        'Price added to Mobile L',
      );
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(String(post?.[1]?.body)).toContain('"price":{"unit":"USD","value":125.50}');
      expect(JSON.parse(String(post?.[1]?.body))).toEqual({
        '@type': 'ProductOfferingPrice',
        name: `Mobile L ${suffix}`,
        lifecycleStatus: 'Active',
        priceType: type,
        ...extra,
        price: { unit: 'USD', value: 125.5 },
        validFor: { startDateTime: '2026-10-01T00:00:00+03:00' },
      });
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
      expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
        productOfferingPrice: [
          { id: 'pr1', '@type': 'ProductOfferingPriceRef' },
          { id: 'new-price', '@type': 'ProductOfferingPriceRef' },
        ],
      });
    },
  );

  const USAGE_PRICE = {
    id: 'pru',
    name: 'Metered voice usage',
    description: 'Calls rounded to ten minutes',
    priceType: 'usage',
    unitOfMeasure: { amount: 10, units: 'minute' },
    price: usd(0.25),
    validFor: { startDateTime: '2026-06-01T21:00:00Z' },
  };

  it('adds a usage price with its block and preserves every attached price', async () => {
    const { fetchMock, detail } = await paygDetails({
      [`POST ${CAT}/productOfferingPrice`]: () => json({ id: 'new-price' }, { status: 201 }),
      [`PATCH ${CAT}/productOffering/${ids(5)}`]: () => json({ id: ids(5) }),
    });
    fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
    const dialog = screen.getByRole('dialog', { name: 'Add price · Metered' });
    expect(within(dialog).queryByRole('option', { name: 'Monthly recurring' })).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('Charge type'), {
      target: { value: 'usage' },
    });
    fireEvent.change(within(dialog).getByLabelText('Rounding unit'), {
      target: { value: 'minute' },
    });
    fireEvent.change(within(dialog).getByLabelText('Block amount'), { target: { value: '10' } });
    fireEvent.change(within(dialog).getByLabelText('Price per block, USD'), {
      target: { value: '0.25' },
    });
    const add = within(dialog).getByRole('button', { name: 'Add price' }) as HTMLButtonElement;
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Price added to Metered',
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(String(post?.[1]?.body)).toContain('"price":{"unit":"USD","value":0.25}');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      '@type': 'ProductOfferingPrice',
      name: 'Metered voice usage',
      lifecycleStatus: 'Active',
      priceType: 'usage',
      unitOfMeasure: { amount: 10, units: 'minute' },
      price: { unit: 'USD', value: 0.25 },
      validFor: { startDateTime: '2026-10-01T00:00:00+03:00' },
    });
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      productOfferingPrice: [
        { id: 'pru', '@type': 'ProductOfferingPriceRef' },
        { id: 'new-price', '@type': 'ProductOfferingPriceRef' },
      ],
    });
  });

  it('rejects a partial second and an overlapping kind and currency', async () => {
    const { detail } = await paygDetails({}, [
      { ...USAGE_PRICE, unitOfMeasure: { amount: 1, units: 'message' } },
    ]);
    fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
    const dialog = screen.getByRole('dialog', { name: 'Add price · Metered' });
    fireEvent.change(within(dialog).getByLabelText('Charge type'), {
      target: { value: 'usage' },
    });
    fireEvent.change(within(dialog).getByLabelText('Rounding unit'), {
      target: { value: 'second' },
    });
    fireEvent.change(within(dialog).getByLabelText('Block amount'), { target: { value: '0.5' } });
    fireEvent.change(within(dialog).getByLabelText('Price per block, USD'), {
      target: { value: '1' },
    });
    expect(await within(dialog).findByRole('alert')).toHaveProperty(
      'textContent',
      'Block amount must be positive and a whole number of bytes, seconds or messages',
    );
    expect(
      (within(dialog).getByRole('button', { name: 'Add price' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Rounding unit'), {
      target: { value: 'message' },
    });
    fireEvent.change(within(dialog).getByLabelText('Block amount'), { target: { value: '1' } });
    expect(await within(dialog).findByRole('alert')).toHaveProperty(
      'textContent',
      'A sms price in USD already covers 2026-10-01',
    );
    expect(
      (within(dialog).getByRole('button', { name: 'Add price' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('lists a usage price and edits only its name, description and amount', async () => {
    const { fetchMock, detail } = await paygDetails(
      {
        [`GET ${CAT}/productOfferingPrice/pru`]: () => json(USAGE_PRICE),
        [`PATCH ${CAT}/productOfferingPrice/pru`]: () => json(USAGE_PRICE),
      },
      [USAGE_PRICE],
    );
    expect(await within(detail).findByText('Usage prices')).toBeTruthy();
    expectUi('ui.catalog.usagePrices');
    fireEvent.click(within(detail).getByRole('button', { name: 'Open price 0.25 minute' }));
    const dialog = await screen.findByRole('dialog', { name: 'Metered voice usage' });
    expect(within(dialog).getByText('0.25 USD / 10 minute')).toBeTruthy();
    expect(expectUi('ui.catalog.usageUnit').textContent).toBe('10 minute');
    fireEvent.change(within(dialog).getByLabelText(/^Name/), {
      target: { value: 'Metered voice 2' },
    });
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'New rounding description' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Price per block, USD/), {
      target: { value: '0.5' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(String(patch?.[1]?.body)).toBe(
      '{"name":"Metered voice 2","description":"New rounding description","price":{"unit":"USD","value":0.50}}',
    );
  });

  it('offers neither usage nor recurring prices on an add-on', async () => {
    const addOn = {
      ...PAYG,
      productOfferingCharacteristic: [
        {
          name: 'offeringKind',
          characteristicValueSpecification: [{ value: 'addOn' }],
        },
      ],
    };
    const { fetchMock } = backend({}, [addOn]);
    await open();
    await waitFor(() => row('Metered'));
    fireEvent.click(row('Metered'));
    const detail = screen.getByRole('dialog', { name: 'Metered' });
    fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
    const dialog = screen.getByRole('dialog', { name: 'Add price · Metered' });
    expect(within(dialog).getByLabelText('Charge type').querySelectorAll('option')).toHaveLength(1);
    expect(within(dialog).getByRole('option', { name: 'One time' })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('priceType=usage'))).toBe(
      false,
    );
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
    expect(within(dialog).getByRole('button', { name: 'Activate…' })).toBe(
      expectUi('ui.catalog.lifecycle'),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate…' }));
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire…' }));
    expect(
      within(dialog).getByText('Retire? New orders are refused; existing products keep billing.'),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire…' }));
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate…' }));
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
    const change = within(dialog).getByRole('button', { name: 'Change price…' });
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
      isSellable: true,
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
