// #498: the search of the Specifications, Categories and Catalogs tabs resets from "No
// matches"; their dialogs answer No, cancel and refuse an end before the start.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, type Handler } from '../test/fetch';
import { CAT, backend, open, row } from '../test/catalogKit';

const CATEGORIES = [
  { id: 'c1', name: 'Mobile', lifecycleStatus: 'Active', subCategory: [], productOffering: [] },
];
const CATALOGS = [
  { id: 'k1', name: 'Retail', lifecycleStatus: 'Active', category: [{ id: 'c1', name: 'Mobile' }] },
];
const SPEC_ROWS = [
  { id: 's1', name: 'Mobile Voice & Data', lifecycleStatus: 'Active' },
  { id: 's9', name: 'Unused', lifecycleStatus: 'In Design' },
];
function classification(routes: Record<string, Handler> = {}) {
  return backend({
    [`GET ${CAT}/productSpecification?offset=0&limit=100`]: () =>
      json(SPEC_ROWS, { total: SPEC_ROWS.length }),
    [`GET ${CAT}/category?offset=0&limit=100`]: () =>
      json(CATEGORIES, { total: CATEGORIES.length }),
    [`GET ${CAT}/productCatalog?offset=0&limit=100`]: () =>
      json(CATALOGS, { total: CATALOGS.length }),
    ...routes,
  });
}
async function openTab(name: string, shown: string) {
  await open();
  await waitFor(() => row('Mobile L'));
  fireEvent.click(screen.getByRole('tab', { name }));
  await screen.findByText(shown);
}
const writes = (fetchMock: ReturnType<typeof backend>['fetchMock']) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method !== undefined && init.method !== 'GET');

describe('classification search', () => {
  it.each([
    ['Specifications', 'Search specifications', 'Unused'],
    ['Categories', 'Search categories', 'Mobile'],
    ['Catalogs', 'Search catalogs', 'Retail'],
  ])('resets the %s search from No matches', async (tab, label, shown) => {
    classification();
    await openTab(tab, shown);
    const search = screen.getByRole('textbox', { name: label });
    fireEvent.change(search, { target: { value: 'nothing like it' } });
    expect(await screen.findByText('No matches')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(await screen.findByText(shown)).toBeTruthy();
    expect((screen.getByRole('textbox', { name: label }) as HTMLInputElement).value).toBe('');
  });
});

describe('specification dialog', () => {
  it('answers No to deleting an unused specification and sends nothing', async () => {
    const { fetchMock } = classification();
    await openTab('Specifications', 'Unused');
    fireEvent.click(screen.getByText('Unused'));
    const dialog = screen.getByRole('dialog', { name: 'Unused' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(within(dialog).queryByRole('button', { name: 'Yes' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('creates a specification that requires an IMSI, with its description', async () => {
    const { fetchMock } = classification({
      [`POST ${CAT}/productSpecification`]: () =>
        json({ id: 's10', name: 'IoT SIM' }, { status: 201 }),
    });
    await openTab('Specifications', 'Unused');
    fireEvent.click(screen.getByRole('button', { name: 'New specification' }));
    const dialog = screen.getByRole('dialog', { name: 'New product specification' });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'IoT SIM' } });
    fireEvent.click(within(dialog).getByLabelText('Requires an IMSI'));
    fireEvent.change(within(dialog).getByLabelText(/^Description/), {
      target: { value: ' Data-only SIM ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Specification IoT SIM created',
    );
    const call = writes(fetchMock)[0];
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      '@type': 'ProductSpecification',
      name: 'IoT SIM',
      lifecycleStatus: 'In Design',
      description: 'Data-only SIM',
      resourceSpecification: [{ id: 'imsi', '@type': 'ResourceSpecificationRef' }],
    });
  });
});

describe('catalog dialog', () => {
  async function retail() {
    await openTab('Catalogs', 'Retail');
    fireEvent.click(screen.getByText('Retail'));
    return screen.getByRole('dialog', { name: 'Retail' });
  }

  it('cancels a catalog edit and sends nothing', async () => {
    const { fetchMock } = classification();
    const dialog = await retail();
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Shop' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('refuses an end before the start', async () => {
    const { fetchMock } = classification();
    const dialog = await retail();
    fireEvent.change(within(dialog).getByLabelText(/^Valid from/), {
      target: { value: '2026-12-01' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Valid to/), {
      target: { value: '2026-11-01' },
    });
    expect(within(dialog).getByRole('alert').textContent).toBe('End date is before start date');
    expect(
      (within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(writes(fetchMock)).toEqual([]);
  });

  it('answers No to deleting a catalog and sends nothing', async () => {
    const { fetchMock } = classification();
    const dialog = await retail();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(within(dialog).queryByRole('button', { name: 'Yes' })).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });
});
