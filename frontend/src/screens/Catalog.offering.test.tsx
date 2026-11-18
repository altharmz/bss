import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  toSpecification,
  characteristicFields,
  characteristicDocuments,
  specificationChanges,
} from '../api/bss';
import { expectUi } from '../test/expectUi';
import { mockWidth } from '../test/matchMedia';
import { json, stubFetch, tmfError, type Handler } from '../test/fetch';
import { CAT, usd, ids, OFFERINGS, SPECS, backend, open, row } from '../test/catalogKit';

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

  it('narrows a network profile value per offering and keeps the other value uses', async () => {
    const other = { name: 'Color', '@type': 'ProductSpecificationCharacteristicValueUse' };
    const { fetchMock } = tvBackend({
      [`GET ${CAT}/productSpecification?offset=0&limit=100`]: () =>
        json(
          [
            ...SPECS.slice(0, 2),
            {
              id: 's3',
              name: 'TV',
              productSpecCharacteristic: [
                {
                  '@type': 'CharacteristicSpecification',
                  name: 'dataAllowanceMb',
                  valueType: 'integer',
                  characteristicValueSpecification: [5000, 10000].map((value, index) => ({
                    '@type': 'IntegerCharacteristicValueSpecification',
                    valueType: 'integer',
                    isDefault: index === 0,
                    value,
                  })),
                },
              ],
            },
          ],
          { total: 3 },
        ),
      [`GET ${CAT}/productOffering/${ids(3)}`]: () =>
        json({ ...DETAIL, prodSpecCharValueUse: [other] }),
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
    const edit = screen.getByRole('dialog', { name: 'Edit TV Basic' });
    expectUi('ui.catalog.offeringNetworkProfile');
    const select = within(edit).getByLabelText('Network dataAllowanceMb') as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(within(select).getByRole('option', { name: 'Default (5000)' })).toBeTruthy();
    expect(within(edit).queryByLabelText('Network dataCapMb')).toBeNull(); // not declared
    fireEvent.change(select, { target: { value: '10000' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(JSON.parse(bodyOf(fetchMock, 'PATCH'))).toEqual({
      prodSpecCharValueUse: [
        other,
        {
          '@type': 'ProductSpecificationCharacteristicValueUse',
          name: 'dataAllowanceMb',
          valueType: 'integer',
          productSpecCharacteristicValue: [
            {
              '@type': 'IntegerCharacteristicValueSpecification',
              valueType: 'integer',
              value: 10000,
            },
          ],
        },
      ],
    });
  });

  it('patches only the changed fields and keeps the other identifiers', async () => {
    const { fetchMock } = tvBackend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
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

  it('shows the offering kind and the one-time prices of the offering', async () => {
    tvBackend({
      [`GET ${CAT}/productOffering/${ids(3)}`]: () =>
        json({
          ...DETAIL,
          productOfferingCharacteristic: [
            { name: 'offeringKind', characteristicValueSpecification: [{ value: 'addOn' }] },
          ],
          productOfferingPrice: [{ id: 'fee1' }],
        }),
      [`GET ${CAT}/productOfferingPrice?priceType=oneTime&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'fee1',
              name: 'Activation fee',
              priceType: 'oneTime',
              price: { unit: 'USD', value: 5 },
              validFor: { startDateTime: '2026-10-01T00:00:00+03:00' },
            },
          ],
          { total: 1 },
        ),
    });
    const dialog = await details('TV Basic');
    expect(within(dialog).getByText('Add-on')).toBe(expectUi('ui.catalog.kind'));
    const fees = await waitFor(() => expectUi('ui.catalog.oneTimePrices'));
    expect(fees.textContent).toContain('5.00 USD');
  });

  it('sets the offering kind as the offeringKind characteristic, keeping the others', async () => {
    const { fetchMock } = tvBackend({
      [`GET ${CAT}/productOffering/${ids(3)}`]: () =>
        json({
          ...DETAIL,
          productOfferingCharacteristic: [{ name: 'colour', '@type': 'Other' }],
        }),
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
    const edit = screen.getByRole('dialog', { name: 'Edit TV Basic' });
    const kind = within(edit).getByLabelText(/^Offering kind/);
    expect(kind).toBe(expectUi('ui.catalog.offeringKind'));
    expect((kind as HTMLSelectElement).value).toBe('plan');
    fireEvent.change(kind, { target: { value: 'addOn' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(JSON.parse(bodyOf(fetchMock, 'PATCH'))).toEqual({
      productOfferingCharacteristic: [
        { name: 'colour', '@type': 'Other' },
        {
          '@type': 'CharacteristicSpecification',
          name: 'offeringKind',
          valueType: 'string',
          characteristicValueSpecification: [
            {
              '@type': 'StringCharacteristicValueSpecification',
              valueType: 'string',
              isDefault: true,
              value: 'addOn',
            },
          ],
        },
      ],
    });
  });

  it('sends the specification, sellable and an emptied description', async () => {
    const { fetchMock } = tvBackend({
      [`PATCH ${CAT}/productOffering/${ids(3)}`]: () => json({ id: ids(3) }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    expectUi('ui.catalog.deleteOffering');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect((await within(dialog).findByRole('alert')).textContent).toMatch(/retire it instead/);
  });

  it('deletes with no body and reports it', async () => {
    const { fetchMock } = tvBackend({
      [`DELETE ${CAT}/productOffering/${ids(3)}`]: () => new Response(null, { status: 204 }),
    });
    const dialog = await details('TV Basic');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    expectUi('ui.catalog.deleteOffering');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
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
    fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
    await screen.findByRole('menuitem', { name: 'Edit offering' });
    expect(screen.queryByRole('menuitem', { name: 'Delete' })).toBeNull();
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
      expect(within(price).getByRole('button', { name: 'Save' })).toBe(
        expectUi('ui.catalog.editPrice'),
      );
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
    fireEvent.click(screen.getByRole('tab', { name: 'Import / export' }));
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
    expect(within(dialog).getByText('Choose a file exported from a catalog export')).toBeTruthy();
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

it('preserves unedited TMF documents and unrelated resource references', () => {
  const original = {
    '@type': 'CharacteristicSpecification',
    id: 'characteristic',
    name: 'Color',
    valueType: 'string',
    configurable: false,
    characteristicValueSpecification: [
      {
        '@type': 'StringCharacteristicValueSpecification',
        value: 'blue',
        isDefault: false,
        regex: 'blue',
      },
    ],
  };
  const specification = toSpecification({
    '@type': 'ProductSpecification',
    id: 'spec',
    name: 'Numbered',
    lifecycleStatus: 'Active',
    resourceSpecification: [{ '@type': 'ResourceSpecificationRef', id: 'other' }],
    productSpecCharacteristic: [original],
  });
  const characteristics = characteristicFields(specification);
  expect(characteristicDocuments(characteristics)).toEqual([original]);
  expect(
    specificationChanges(specification, {
      name: 'Numbered',
      description: '',
      brand: '',
      version: '',
      productNumber: '',
      lifecycleStatus: 'Active',
      requiresNumber: true,
      characteristics,
    }),
  ).toEqual({
    resourceSpecification: [
      { '@type': 'ResourceSpecificationRef', id: 'other' },
      { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
    ],
  });
});

describe('catalog inspector (>= 1536 px)', () => {
  it('shows the dialog below 1536 px', async () => {
    mockWidth(false);
    backend();
    await open();
    await waitFor(() => row('TV Basic'));
    fireEvent.click(row('TV Basic'));
    expectUi('ui.catalog.offeringDetail');
    expect(document.querySelector('[data-ui="ui.catalog.inspector"]')).toBeNull();
  });

  it('shows the same detail as an inspector, with the same detail request', async () => {
    mockWidth(true);
    const { fetchMock } = backend();
    await open();
    await waitFor(() => row('TV Basic'));
    fireEvent.click(row('TV Basic'));
    const inspector = expectUi('ui.catalog.inspector');
    expect(document.querySelector('[data-ui="ui.catalog.offeringDetail"]')).toBeNull();
    expect(row('TV Basic').getAttribute('aria-selected')).toBe('true');
    expect(
      await within(inspector).findByText('399.00 USD / month · 2026-10-05 – open-ended'),
    ).toBeTruthy();
    const detailCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith(`/productOffering/${ids(3)}`),
    );
    expect(detailCalls).toHaveLength(1);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.querySelector('[data-ui="ui.catalog.inspector"]')).toBeNull();
  });
});
