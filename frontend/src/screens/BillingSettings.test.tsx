import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// The Billing settings and Dunning settings tabs of Settings (docs/ui.md). TEST-CHANGE (#467):
// the screen moved into System's Settings page (/settings/billing, /settings/dunning); the
// requests and the data-ui markers are unchanged.

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
    ...extra,
  });
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

type Tab = 'Cycle specifications' | 'Bill formats' | 'Presentation media' | 'Dunning';

async function open(tab: Tab = 'Cycle specifications') {
  window.history.replaceState(
    null,
    '',
    tab === 'Dunning' ? '/settings/dunning' : '/settings/billing',
  );
  render(<App />);
  await screen.findByRole('heading', { name: /^Settings/ });
  if (tab === 'Dunning') return;
  fireEvent.click(
    within(screen.getByRole('group', { name: 'Billing settings sections' })).getByRole('button', {
      name: tab,
    }),
  );
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name)) as HTMLElement;
/** The row once the list (reloaded after a write) shows it. */
const shown = async (name: string) => waitFor(() => expect(rowOf(name)).toBeTruthy());

describe('billing settings', () => {
  // TEST-CHANGE (#414): the prepaid lifecycle moved to its own screen under Prepaid
  // (LifecycleSettings.test.tsx); Billing settings keeps the three TMF678 tabs.
  // TEST-CHANGE (#386): plus the postpaid Dunning tab
  // TEST-CHANGE (#467): Dunning settings and Prepaid lifecycle are tabs of the Settings page
  it('has the three billing configuration tabs under the Settings tabs', async () => {
    backend();
    await open();
    const page = within(screen.getByRole('group', { name: 'Settings tabs' }));
    expect(page.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Billing settings',
      'Prepaid lifecycle',
      'Dunning settings',
    ]);
    const tabs = within(screen.getByRole('group', { name: 'Billing settings sections' }));
    expect(tabs.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Cycle specifications',
      'Bill formats',
      'Presentation media',
    ]);
  });

  it('lists the cycle specifications; the built-in one is read-only', async () => {
    backend();
    await open();
    await screen.findByText('Corporate monthly');
    expectUi('ui.billingSettings.cycles');
    const builtIn = rowOf('Monthly billing');
    expect(within(builtIn).getByText('built in')).toBeTruthy();
    expect(within(builtIn).queryByRole('button')).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit Corporate monthly' })).toBeTruthy();
    // the sidebar links to the page (System > Settings, #467)
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Settings' }).getAttribute('aria-current')).toBe(
      'page',
    );
  });

  // TEST-CHANGE (#314): no "used by" count, which read every billing account (#312: no
  // counts); Delete is offered for every stored setting and the server refuses one in use
  // (the 409 test below)
  it('offers Delete for every stored setting and reads no billing accounts', async () => {
    const { calls } = backend();
    await open();
    await screen.findByText('Corporate monthly');
    expect(screen.queryByRole('columnheader', { name: 'Used by' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete Corporate monthly' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete Spare' })).toBeTruthy();
    expect(calls.filter((c) => c.startsWith(`GET ${ACC}/billingAccount?offset`))).toEqual([]);
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
    fireEvent.click(screen.getByRole('button', { name: 'New cycle specification' }));
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

    await shown('Spare');
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
    fireEvent.click(screen.getByRole('button', { name: 'New bill format' }));
    expectUi('ui.billingSettings.formats.new');
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
    expectUi('ui.billingSettings.formats.edit');
    dialog = await screen.findByRole('dialog', { name: 'Edit JSON summary' });
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: 'Totals' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('JSON summary saved');

    await shown('JSON summary');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete JSON summary' }));
    expectUi('ui.billingSettings.formats.delete');
    dialog = await screen.findByRole('dialog', { name: 'Delete JSON summary' });
    expect(dialog).toBe(expectUi('ui.billingSettings.formats.confirmDelete'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'New presentation medium' }));
    expectUi('ui.billingSettings.media.new');
    let dialog = await screen.findByRole('dialog', { name: 'New presentation medium' });
    expect(dialog).toBe(expectUi('ui.billingSettings.media.form'));
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'SMS' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('SMS created');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Post' }));
    expectUi('ui.billingSettings.media.edit');
    dialog = await screen.findByRole('dialog', { name: 'Edit Post' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Paper post' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Paper post saved');
    await shown('Post');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Post' }));
    expectUi('ui.billingSettings.media.delete');
    dialog = await screen.findByRole('dialog', { name: 'Delete Post' });
    expect(dialog).toBe(expectUi('ui.billingSettings.media.confirmDelete'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
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
    await shown('Spare');
    fireEvent.click(screen.getByRole('button', { name: 'Delete Spare' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete Spare' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('billStructure first');
    expect(screen.getByRole('dialog', { name: 'Delete Spare' })).toBeTruthy();
  });
});

// #386: the postpaid dunning steps. The PATCH bodies are replayed against the real API by
// tests/internal/test_ui_requests.py - change both together.
describe('dunning settings', () => {
  const path = '/internal/v1/dunning/settings';
  const settings = {
    reminder1Days: 1,
    reminder2Days: 7,
    suspendDays: 14,
    minimumOverdue: 5,
    updatedAt: null,
  };

  it('shows the steps and the minimum, and saves only what changed, the amount exact', async () => {
    const { fetchMock } = backend({
      [`GET ${path}`]: () => json(settings),
      [`PATCH ${path}`]: () => json({ ...settings, reminder1Days: 0, minimumOverdue: 7.5 }),
    });
    await open('Dunning');
    await screen.findByText('14 days after the first overdue date');
    expectUi('ui.billingSettings.dunning');
    expect(screen.getByText('1 day after the first overdue date')).toBeTruthy();
    expect(screen.getByText('5.00 USD')).toBeTruthy();
    expect(screen.getByText('Never (defaults)')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit dunning settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit dunning settings' });
    expectUi('ui.billingSettings.dunning.form');
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    fireEvent.change(within(dialog).getByLabelText('First reminder'), { target: { value: '0' } });
    fireEvent.change(within(dialog).getByLabelText('Minimum past due'), {
      target: { value: '7.5' },
    });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await screen.findByText('Dunning settings saved');
    expect(writes(fetchMock)).toEqual([
      ['PATCH', path, '{"reminder1Days":0,"minimumOverdue":7.50}'],
    ]);
  });

  it('shows each validation error at its field and blocks Save', async () => {
    backend({ [`GET ${path}`]: () => json(settings) });
    await open('Dunning');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit dunning settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit dunning settings' });
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    const field = (label: string) =>
      within(dialog).getByLabelText(label).closest('.bss-field-control') as HTMLElement;
    const set = (label: string, value: string) =>
      fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

    for (const value of ['', '-1', '1.5', '366']) {
      set('First reminder', value);
      expect(
        within(field('First reminder')).getByText('Enter whole days from 0 to 365.'),
      ).toBeTruthy();
      expect(save.disabled).toBe(true);
    }
    set('First reminder', '7'); // not before the second reminder (7)
    expect(
      within(field('Second reminder')).getByText('Must be later than the first reminder.'),
    ).toBeTruthy();
    expect(within(field('First reminder')).queryByText(/Enter whole days/)).toBeNull();
    expect(save.disabled).toBe(true);
    set('First reminder', '1');
    set('Suspension', '7');
    expect(
      within(field('Suspension')).getByText('Must be later than the second reminder.'),
    ).toBeTruthy();
    expect(within(field('Second reminder')).queryByText(/Must be later/)).toBeNull();
    set('Suspension', '30');
    for (const value of ['', '-1', '1.234', 'abc']) {
      set('Minimum past due', value);
      expect(
        within(field('Minimum past due')).getByText(
          'Enter an amount of 0 or more with at most 2 decimals.',
        ),
      ).toBeTruthy();
      expect(save.disabled).toBe(true);
    }
    set('Minimum past due', '0');
    expect(within(dialog).queryByText(/Enter|Must be/)).toBeNull();
    expect(save.disabled).toBe(false);
  });

  it('keeps the values and shows the server refusal', async () => {
    backend({
      [`GET ${path}`]: () => json(settings),
      [`PATCH ${path}`]: () => tmfError(422, 'the steps must be increasing'),
    });
    await open('Dunning');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit dunning settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit dunning settings' });
    fireEvent.change(within(dialog).getByLabelText('Suspension'), { target: { value: '20' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await within(dialog).findByText(/the steps must be increasing/)).toBeTruthy();
    expect((within(dialog).getByLabelText('Suspension') as HTMLInputElement).value).toBe('20');
  });

  it('retries a failed read', async () => {
    let reads = 0;
    backend({
      [`GET ${path}`]: () => (++reads === 1 ? tmfError(503, 'Unavailable') : json(settings)),
    });
    await open('Dunning');
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('14 days after the first overdue date')).toBeTruthy();
  });
});
