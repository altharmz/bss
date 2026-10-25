import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';
import { accountBody } from '../test/fixtures';

// The Billing settings screen (docs/next-slices-design.md section 18).

const ACC = '/tmf-api/accountManagement/v5';
const PAGE = 'offset=0&limit=100';
const CY1 = 'c1000000-0000-4000-8000-000000000001';
const CY2 = 'c2000000-0000-4000-8000-000000000002';
const BF1 = 'b1000000-0000-4000-8000-000000000001';
const BF2 = 'b2000000-0000-4000-8000-000000000002';
const BM1 = 'e1000000-0000-4000-8000-000000000001';
const BM2 = 'e2000000-0000-4000-8000-000000000002';

const MONTHLY = { id: 'monthly', name: 'Monthly billing', description: 'Built in policy' };
const CYCLES = [
  MONTHLY,
  { id: CY1, name: 'Corporate monthly', description: 'For companies' },
  { id: CY2, name: 'Spare', '@type': 'BillingCycleSpecification' },
];
const FORMATS = [
  { id: BF1, name: 'JSON detailed', description: 'Every line' },
  { id: BF2, name: 'JSON summary' },
];
const MEDIA = [
  { id: BM1, name: 'Email', description: 'PDF by mail' },
  { id: BM2, name: 'Post' },
];

// one account names Corporate monthly, JSON detailed and Email
const USER = accountBody(
  'aaaa1111-0000-4000-8000-0000000000a1',
  { id: 'c1', name: 'Olga' },
  {
    billStructure: {
      cycleSpecification: { id: CY1 },
      format: { id: BF1 },
      presentationMedia: [{ id: BM1 }],
    },
  },
);
const PLAIN = accountBody(
  'aaaa2222-0000-4000-8000-0000000000a2',
  { id: 'c2', name: 'Boris' },
  { billStructure: { cycleSpecification: { id: 'monthly' } } },
);

function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    [`GET ${ACC}/financialAccount?limit=1&fields=id`]: () => json([], { total: 0 }),
    [`GET ${ACC}/billingAccount?limit=1&fields=id`]: () => json([], { total: 2 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [`GET ${ACC}/billingCycleSpecification?${PAGE}`]: () => json(CYCLES, { total: 3 }),
    [`GET ${ACC}/billFormat?${PAGE}`]: () => json(FORMATS, { total: 2 }),
    [`GET ${ACC}/billPresentationMedia?${PAGE}`]: () => json(MEDIA, { total: 2 }),
    [`GET ${ACC}/billingAccount?${PAGE}`]: () => json([USER, PLAIN], { total: 2 }),
    ...extra,
  });
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

type Tab = 'Cycle specifications' | 'Bill formats' | 'Presentation media';

async function open(tab: Tab = 'Cycle specifications') {
  window.history.replaceState(null, '', '/billing-settings');
  render(<App />);
  await screen.findByRole('heading', { name: /^Billing settings/ });
  fireEvent.click(
    within(screen.getByRole('group', { name: 'Billing settings sections' })).getByRole('button', {
      name: tab,
    }),
  );
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name)) as HTMLElement;
/** The row once the list (reloaded after a write) and the account usage are shown. */
const usage = async (name: string, count: string) =>
  waitFor(() => expect(within(rowOf(name)).getByText(count)).toBeTruthy());

describe('billing settings', () => {
  it('lists the cycle specifications; the built-in one is read-only', async () => {
    backend();
    await open();
    await screen.findByText('Corporate monthly');
    expectUi('ui.billingSettings.cycles');
    const builtIn = rowOf('Monthly billing');
    expect(within(builtIn).getByText('built in')).toBeTruthy();
    expect(within(builtIn).queryByRole('button')).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit Corporate monthly' })).toBeTruthy();
    // the sidebar links to the screen
    expect(screen.getByRole('link', { name: /Billing settings/ })).toBeTruthy();
  });

  it('hides Delete while a loaded account uses the setting', async () => {
    backend();
    await open();
    await screen.findByText('Corporate monthly');
    await usage('Corporate monthly', '1'); // accounts loaded
    expect(screen.queryByRole('button', { name: 'Delete Corporate monthly' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete Spare' })).toBeTruthy();
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Billing settings sections' })).getByRole('button', {
        name: 'Bill formats',
      }),
    );
    await screen.findByText('JSON summary');
    await usage('JSON detailed', '1');
    expect(screen.queryByRole('button', { name: 'Delete JSON detailed' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete JSON summary' })).toBeTruthy();
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Billing settings sections' })).getByRole('button', {
        name: 'Presentation media',
      }),
    );
    await screen.findByText('Post');
    await usage('Email', '1');
    expect(screen.queryByRole('button', { name: 'Delete Email' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete Post' })).toBeTruthy();
  });

  it('creates, edits and deletes a cycle specification: bodies pinned', async () => {
    const { fetchMock } = backend({
      [`POST ${ACC}/billingCycleSpecification`]: () => json({ id: CY2 }, { status: 201 }),
      [`PATCH ${ACC}/billingCycleSpecification/${CY1}`]: () => json(CYCLES[1]),
      [`PATCH ${ACC}/billingCycleSpecification/${CY2}`]: () => json(CYCLES[2]),
      [`DELETE ${ACC}/billingCycleSpecification/${CY2}`]: () => new Response(null, { status: 204 }),
    });
    await open();
    await screen.findByText('Corporate monthly');
    fireEvent.click(screen.getByRole('button', { name: '+ New cycle specification' }));
    expectUi('ui.billingSettings.cycles.new');
    let dialog = await screen.findByRole('dialog', { name: 'New cycle specification' });
    expectUi('ui.billingSettings.cycles.form');
    const create = within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Quarter end' } });
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: ' Names only ' },
    });
    fireEvent.click(create);
    await screen.findByText('Quarter end created');

    fireEvent.click(await screen.findByRole('button', { name: 'Edit Corporate monthly' }));
    expectUi('ui.billingSettings.cycles.edit');
    dialog = await screen.findByRole('dialog', { name: 'Edit Corporate monthly' });
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: '' } });
    fireEvent.click(save);
    await screen.findByText('Corporate monthly saved');

    fireEvent.click(await screen.findByRole('button', { name: 'Edit Spare' }));
    dialog = await screen.findByRole('dialog', { name: 'Edit Spare' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Spare 2' } });
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'x' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Spare 2 saved');

    await usage('Spare', '0');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Spare' }));
    expectUi('ui.billingSettings.cycles.delete');
    dialog = await screen.findByRole('dialog', { name: 'Delete Spare' });
    expectUi('ui.billingSettings.cycles.confirmDelete');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await screen.findByText('Spare deleted');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACC}/billingCycleSpecification`,
        JSON.stringify({
          '@type': 'BillingCycleSpecification',
          name: 'Quarter end',
          description: 'Names only',
        }),
      ],
      [
        'PATCH',
        `${ACC}/billingCycleSpecification/${CY1}`,
        JSON.stringify({ '@type': 'BillingCycleSpecification', description: null }),
      ],
      [
        'PATCH',
        `${ACC}/billingCycleSpecification/${CY2}`,
        JSON.stringify({ '@type': 'BillingCycleSpecification', name: 'Spare 2', description: 'x' }),
      ],
      ['DELETE', `${ACC}/billingCycleSpecification/${CY2}`, null],
    ]);
  });

  it('bill formats: the name is one of the two the API stores', async () => {
    const { fetchMock } = backend({
      [`POST ${ACC}/billFormat`]: () => json({ id: BF2 }, { status: 201 }),
      [`PATCH ${ACC}/billFormat/${BF2}`]: () => json(FORMATS[1]),
      [`DELETE ${ACC}/billFormat/${BF2}`]: () => new Response(null, { status: 204 }),
    });
    await open('Bill formats');
    await screen.findByText('JSON summary');
    expectUi('ui.billingSettings.formats');
    fireEvent.click(screen.getByRole('button', { name: '+ New bill format' }));
    let dialog = await screen.findByRole('dialog', { name: 'New bill format' });
    expectUi('ui.billingSettings.formats.form');
    const name = within(dialog).getByLabelText('Name') as HTMLSelectElement;
    expect([...name.options].map((o) => o.text)).toEqual([
      'Choose a format',
      'JSON detailed',
      'JSON summary',
    ]);
    fireEvent.change(name, { target: { value: 'JSON summary' } });
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'Totals' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('JSON summary created');

    fireEvent.click(await screen.findByRole('button', { name: 'Edit JSON summary' }));
    dialog = await screen.findByRole('dialog', { name: 'Edit JSON summary' });
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'Totals' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('JSON summary saved');

    await usage('JSON summary', '0');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete JSON summary' }));
    fireEvent.click(
      within(await screen.findByRole('dialog', { name: 'Delete JSON summary' })).getByRole(
        'button',
        { name: 'Delete' },
      ),
    );
    await screen.findByText('JSON summary deleted');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACC}/billFormat`,
        JSON.stringify({ '@type': 'BillFormat', name: 'JSON summary', description: 'Totals' }),
      ],
      [
        'PATCH',
        `${ACC}/billFormat/${BF2}`,
        JSON.stringify({ '@type': 'BillFormat', description: 'Totals' }),
      ],
      ['DELETE', `${ACC}/billFormat/${BF2}`, null],
    ]);
  });

  it('presentation media: create, rename, delete', async () => {
    const { fetchMock } = backend({
      [`POST ${ACC}/billPresentationMedia`]: () => json({ id: BM2 }, { status: 201 }),
      [`PATCH ${ACC}/billPresentationMedia/${BM2}`]: () => json(MEDIA[1]),
      [`DELETE ${ACC}/billPresentationMedia/${BM2}`]: () => new Response(null, { status: 204 }),
    });
    await open('Presentation media');
    await screen.findByText('Post');
    expectUi('ui.billingSettings.media');
    fireEvent.click(screen.getByRole('button', { name: '+ New presentation medium' }));
    let dialog = await screen.findByRole('dialog', { name: 'New presentation medium' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'SMS' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('SMS created');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Post' }));
    dialog = await screen.findByRole('dialog', { name: 'Edit Post' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Paper post' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Paper post saved');
    await usage('Post', '0');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Post' }));
    fireEvent.click(
      within(await screen.findByRole('dialog', { name: 'Delete Post' })).getByRole('button', {
        name: 'Delete',
      }),
    );
    await screen.findByText('Post deleted');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACC}/billPresentationMedia`,
        JSON.stringify({ '@type': 'BillPresentationMedia', name: 'SMS' }),
      ],
      [
        'PATCH',
        `${ACC}/billPresentationMedia/${BM2}`,
        JSON.stringify({ '@type': 'BillPresentationMedia', name: 'Paper post' }),
      ],
      ['DELETE', `${ACC}/billPresentationMedia/${BM2}`, null],
    ]);
  });

  it('shows the server refusal of a delete and keeps the dialog', async () => {
    backend({
      [`DELETE ${ACC}/billingCycleSpecification/${CY2}`]: () =>
        tmfError(409, 'billingCycleSpecification is in use: change their billStructure first'),
    });
    await open();
    await screen.findByText('Spare');
    await usage('Spare', '0');
    fireEvent.click(screen.getByRole('button', { name: 'Delete Spare' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete Spare' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('billStructure first');
    expect(screen.getByRole('dialog', { name: 'Delete Spare' })).toBeTruthy();
  });
});
