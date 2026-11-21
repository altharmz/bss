import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectUi } from '../test/expectUi';
import { json, stubFetch, tmfError, type Handler } from '../test/fetch';
import { CAT, ids, OFFERINGS, backend, open, row } from '../test/catalogKit';

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
    fireEvent.click(screen.getByRole('tab', { name: 'Specifications' }));
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

  const profileEntry = (name: string, values: number[]) => ({
    '@type': 'CharacteristicSpecification',
    name,
    valueType: 'integer',
    configurable: false,
    characteristicValueSpecification: values.map((value, index) => ({
      '@type': 'IntegerCharacteristicValueSpecification',
      valueType: 'integer',
      isDefault: index === 0,
      value,
    })),
  });

  it('creates a specification with the network profile as integer characteristics', async () => {
    const { fetchMock } = specBackend({
      [`POST ${CAT}/productSpecification`]: () =>
        json({ id: 's9', name: 'Roaming' }, { status: 201 }),
    });
    await openTab();
    fireEvent.click(screen.getByRole('button', { name: '+ New specification' }));
    const dialog = screen.getByRole('dialog', { name: 'New product specification' });
    expectUi('ui.catalog.networkProfile');
    fireEvent.change(field(dialog, /^Name/), { target: { value: 'Roaming' } });
    fireEvent.change(field(dialog, /^Network dataAllowanceMb/), {
      target: { value: '5000, 10000' },
    });
    fireEvent.change(field(dialog, /^Network hotspot/), { target: { value: '2' } });
    const create = within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(true); // a flag is 0 or 1
    fireEvent.change(field(dialog, /^Network hotspot/), { target: { value: '1, 0' } });
    fireEvent.click(create);
    await screen.findByRole('status');
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      '@type': 'ProductSpecification',
      name: 'Roaming',
      lifecycleStatus: 'In Design',
      productSpecCharacteristic: [
        { ...profileEntry('dataAllowanceMb', [5000, 10000]), description: undefined },
        { ...profileEntry('hotspot', [1, 0]), description: undefined },
      ].map((entry) => JSON.parse(JSON.stringify(entry))),
    });
  });

  it('edits the network profile in place and keeps the other characteristics as loaded', async () => {
    const color = {
      '@type': 'CharacteristicSpecification',
      name: 'Color',
      valueType: 'string',
      characteristicValueSpecification: [
        { '@type': 'StringCharacteristicValueSpecification', value: 'blue', isDefault: true },
      ],
    };
    const cap = profileEntry('dataCapMb', [20000]);
    const { fetchMock } = specBackend({
      [SPECS_URL]: () =>
        json([{ ...SPEC_ROWS[0], productSpecCharacteristic: [color, cap] }], { total: 1 }),
      [`PATCH ${CAT}/productSpecification/s1`]: () => json({ id: 's1', name: 'Mobile' }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile Voice & Data' });
    // the profile entry is in its own section, not in the generic list
    expect(within(dialog).getAllByRole('group', { name: /^Characteristic \d/ })).toHaveLength(1);
    expect((field(dialog, /^Network dataCapMb/) as HTMLInputElement).value).toBe('20000');
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    fireEvent.change(field(dialog, /^Network dataCapMb/), { target: { value: '30000' } });
    fireEvent.change(field(dialog, /^Network voiceMinutes/), { target: { value: '300' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    const sent = JSON.parse(String(call?.[1]?.body)).productSpecCharacteristic;
    expect(sent.map((c: { name: string }) => c.name)).toEqual([
      'Color',
      'dataCapMb',
      'voiceMinutes',
    ]);
    expect(sent[0]).toEqual(color);
    expect(sent[1].characteristicValueSpecification.map((v: { value: number }) => v.value)).toEqual(
      [30000],
    );
    expect(sent[2].characteristicValueSpecification.map((v: { value: number }) => v.value)).toEqual(
      [300],
    );
  });

  it('shows and edits the dataPool flag with the other profile flags (#380)', async () => {
    const pool = profileEntry('dataPool', [0]);
    const { fetchMock } = specBackend({
      [SPECS_URL]: () =>
        json([{ ...SPEC_ROWS[0], productSpecCharacteristic: [pool] }], { total: 1 }),
      [`PATCH ${CAT}/productSpecification/s1`]: () => json({ id: 's1', name: 'Mobile' }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog', { name: 'Mobile Voice & Data' });
    const flag = field(dialog, /^Network dataPool/) as HTMLInputElement;
    expect(flag.value).toBe('0');
    expect(expectUi('ui.catalog.networkProfile').textContent).toContain('Data pool (0 or 1)');
    fireEvent.change(flag, { target: { value: '2' } });
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    fireEvent.change(flag, { target: { value: '1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect(JSON.parse(String(call?.[1]?.body)).productSpecCharacteristic).toEqual([
      JSON.parse(JSON.stringify({ ...profileEntry('dataPool', [1]), description: undefined })),
    ]);
  });

  it.each(['string', 'integer', 'boolean'])(
    'creates numbered specifications with %s characteristics',
    async (valueType) => {
      const { fetchMock } = specBackend({
        [`POST ${CAT}/productSpecification`]: () =>
          json({ id: 's9', name: 'Numbered' }, { status: 201 }),
      });
      await openTab();
      fireEvent.click(screen.getByRole('button', { name: '+ New specification' }));
      const dialog = screen.getByRole('dialog');
      fireEvent.change(field(dialog, /^Name/), { target: { value: 'Numbered' } });
      fireEvent.click(field(dialog, /Requires a subscriber number/));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Add characteristic' }));
      fireEvent.change(field(dialog, /Characteristic name/), { target: { value: 'Option' } });
      fireEvent.change(field(dialog, /Characteristic description/), {
        target: { value: 'Choices' },
      });
      fireEvent.change(field(dialog, /Value type/), { target: { value: valueType } });
      fireEvent.click(field(dialog, /Configurable/));
      const values =
        valueType === 'integer' ? [1, 2] : valueType === 'boolean' ? [] : ['red', 'blue'];
      if (valueType === 'boolean') {
        expect((field(dialog, /^Values/) as HTMLInputElement).disabled).toBe(true);
        expect(
          within(dialog).getByText(/Predefined boolean values are not supported/),
        ).toBeTruthy();
      } else fireEvent.change(field(dialog, /^Values/), { target: { value: values.join(', ') } });
      expectUi('ui.catalog.specificationNumberRequirement');
      expectUi('ui.catalog.specificationCharacteristics');
      expectUi('ui.catalog.specificationCharacteristic');
      expectUi('ui.catalog.addCharacteristic');
      expectUi('ui.catalog.removeCharacteristic');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
      await screen.findByRole('status');
      const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        '@type': 'ProductSpecification',
        name: 'Numbered',
        lifecycleStatus: 'In Design',
        resourceSpecification: [{ id: 'msisdn', '@type': 'ResourceSpecificationRef' }],
        productSpecCharacteristic: [
          {
            '@type': 'CharacteristicSpecification',
            name: 'Option',
            description: 'Choices',
            configurable: true,
            valueType,
            characteristicValueSpecification: values.map((value, index) => ({
              '@type': `${valueType[0]?.toUpperCase()}${valueType.slice(1)}CharacteristicValueSpecification`,
              valueType,
              isDefault: index === 0,
              value,
            })),
          },
        ],
      });
    },
  );

  it('patches the number requirement and characteristics', async () => {
    const { fetchMock } = specBackend({
      [`PATCH ${CAT}/productSpecification/s1`]: () => json({ id: 's1', name: 'Mobile' }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(field(dialog, /Requires a subscriber number/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add characteristic' }));
    fireEvent.change(field(dialog, /Characteristic name/), { target: { value: 'Enabled' } });
    fireEvent.change(field(dialog, /Value type/), { target: { value: 'integer' } });
    fireEvent.change(field(dialog, /^Values/), { target: { value: 'invalid' } });
    expect(
      (within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.change(field(dialog, /^Values/), { target: { value: '1, 2' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body),
      ),
    ).toEqual({
      resourceSpecification: [{ id: 'msisdn', '@type': 'ResourceSpecificationRef' }],
      productSpecCharacteristic: [
        {
          '@type': 'CharacteristicSpecification',
          name: 'Enabled',
          description: '',
          configurable: false,
          valueType: 'integer',
          characteristicValueSpecification: [1, 2].map((value, index) => ({
            '@type': 'IntegerCharacteristicValueSpecification',
            valueType: 'integer',
            isDefault: index === 0,
            value,
          })),
        },
      ],
    });
  });

  it('loads and removes the requirement and characteristic list with exact empty arrays', async () => {
    const { fetchMock } = specBackend({
      [SPECS_URL]: () =>
        json(
          [
            {
              ...SPEC_ROWS[0],
              resourceSpecification: [{ id: 'msisdn', '@type': 'ResourceSpecificationRef' }],
              productSpecCharacteristic: [
                {
                  '@type': 'CharacteristicSpecification',
                  name: 'Color',
                  valueType: 'string',
                  characteristicValueSpecification: [
                    {
                      '@type': 'StringCharacteristicValueSpecification',
                      value: 'blue',
                      isDefault: true,
                    },
                  ],
                },
              ],
            },
          ],
          { total: 1 },
        ),
      [`PATCH ${CAT}/productSpecification/s1`]: () => json({ id: 's1', name: 'Mobile' }),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog');
    expect((field(dialog, /Requires a subscriber number/) as HTMLInputElement).checked).toBe(true);
    expect((field(dialog, /Characteristic name/) as HTMLInputElement).value).toBe('Color');
    expect((field(dialog, /^Values/) as HTMLInputElement).value).toBe('blue');
    expect(
      (within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(field(dialog, /Requires a subscriber number/));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove characteristic' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body),
      ),
    ).toEqual({ resourceSpecification: [], productSpecCharacteristic: [] });
  });

  it('locks the requirement on an ordering conflict, preserves other edits and shows server text', async () => {
    specBackend({
      [`PATCH ${CAT}/productSpecification/s1`]: () =>
        tmfError(409, 'resourceSpecification: the msisdn requirement cannot change after ordering'),
    });
    await openTab();
    fireEvent.click(specRow('Mobile Voice & Data'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(field(dialog, /Requires a subscriber number/));
    fireEvent.change(field(dialog, /Brand/), { target: { value: 'Other' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'the msisdn requirement cannot change after ordering',
    );
    expect((field(dialog, /Requires a subscriber number/) as HTMLInputElement).disabled).toBe(true);
    expect((field(dialog, /Requires a subscriber number/) as HTMLInputElement).checked).toBe(false);
    expect((field(dialog, /Brand/) as HTMLInputElement).value).toBe('Other');
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
    fireEvent.click(screen.getByRole('tab', { name: 'Categories' }));
    await screen.findByText('Promo');
  }
  async function openCatalogs() {
    await open();
    fireEvent.click(screen.getByRole('tab', { name: 'Catalogs' }));
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
