import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// The Party and Settlement tabs of the Accounts screen (docs/ui.md).

const ACC = '/tmf-api/accountManagement/v5';
const PAGE = 'offset=0&limit=100';
const ROMASHKA = 'p1';
const OLGA = 'aaaa1111-0000-4000-8000-000000000002';
const FIN1 = 'f0000001-0000-4000-8000-000000000001';
const FIN2 = 'f0000002-0000-4000-8000-000000000002';
const A1 = 'a0000001-0000-4000-8000-000000000001';
const A2 = 'a0000002-0000-4000-8000-000000000002';

const owner = (id: string, name: string, type: string) => [
  {
    role: 'owner',
    partyOrPartyRole: { id, name, '@type': 'PartyRef', '@referredType': type },
    '@type': 'RelatedPartyRefOrPartyRoleRef',
  },
];
const account = (kind: string, id: string, name: string, extra: object = {}) => ({
  id,
  href: `${ACC}/${kind === 'PartyAccount' ? 'partyAccount' : 'settlementAccount'}/${id}`,
  name,
  state: 'Active',
  relatedParty: owner(ROMASHKA, 'Romashka LLC', 'Organization'),
  accountBalance: [],
  '@type': kind,
  ...extra,
});
const LINKED = account('PartyAccount', A1, 'Payroll', {
  accountType: 'Business',
  description: 'Salaries',
  financialAccount: { id: FIN1, name: 'Group receivables', '@type': 'FinancialAccountRef' },
});
const FREE = account('PartyAccount', A2, 'Petty cash', { state: 'Suspended' });
const PAYOUT = account('SettlementAccount', A1, 'Partner payouts');

const OWNER_P1 = {
  role: 'owner',
  partyOrPartyRole: { id: ROMASHKA, '@type': 'PartyRef', '@referredType': 'Organization' },
  '@type': 'RelatedPartyRefOrPartyRoleRef',
};
const ref = (id: string) => ({
  id,
  '@type': 'FinancialAccountRef',
  '@referredType': 'FinancialAccount',
});

function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    [`GET ${ACC}/financialAccount?limit=1&fields=id`]: () => json([], { total: 2 }),
    [`GET ${ACC}/billingAccount?limit=1&fields=id`]: () => json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [`GET ${ACC}/financialAccount?${PAGE}`]: () => json([], { total: 0 }),
    [`GET ${ACC}/partyAccount?${PAGE}`]: () => json([LINKED, FREE], { total: 2 }),
    [`GET ${ACC}/settlementAccount?${PAGE}`]: () => json([PAYOUT], { total: 1 }),
    // TEST-CHANGE (#314): the owner picker searches parties on the server as typed
    'GET /internal/v1/party?*': (url) => {
      const q = (new URL(url ?? '', 'http://test').searchParams.get('q') ?? '').toLowerCase();
      return json(
        [
          { id: OLGA, kind: 'Individual', name: 'Olga Kuznetsova' },
          { id: ROMASHKA, kind: 'Organization', name: 'Romashka LLC' },
        ].filter((p) => p.name.toLowerCase().startsWith(q)),
      );
    },
    [`GET ${ACC}/financialAccount?relatedParty.id=${ROMASHKA}&${PAGE}`]: () =>
      json(
        [
          { id: FIN1, name: 'Group receivables', state: 'Active' },
          { id: FIN2, name: 'Old group', state: 'Closed' },
        ],
        { total: 2 },
      ),
    [`GET ${ACC}/financialAccount?relatedParty.id=${OLGA}&${PAGE}`]: () => json([], { total: 0 }),
    ...extra,
  });
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

async function open(tab: 'Party' | 'Settlement') {
  window.history.replaceState(null, '', '/financial-accounts');
  render(<App />);
  await screen.findByRole('heading', { name: /^Accounts/ });
  fireEvent.click(
    within(screen.getByRole('group', { name: 'Account sections' })).getByRole('button', {
      name: tab,
    }),
  );
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name)) as HTMLElement;

describe('party and settlement accounts', () => {
  it('lists party accounts with owner, type, state and financial account, no balance', async () => {
    backend();
    await open('Party');
    await screen.findByText('Payroll');
    expectUi('ui.accounts.party');
    const linked = rowOf('Payroll');
    expect(within(linked).getByText(/Romashka LLC/)).toBeTruthy();
    expect(within(linked).getByText('Business')).toBeTruthy();
    expect(within(linked).getByText('Group receivables')).toBeTruthy();
    expect(within(rowOf('Petty cash')).getByText('none')).toBeTruthy();
    expect(within(rowOf('Petty cash')).getByText('Suspended')).toBeTruthy();
    expect(screen.queryByText('Balance')).toBeNull();
  });

  it('lists settlement accounts on their tab', async () => {
    backend();
    await open('Settlement');
    await screen.findByText('Partner payouts');
    expectUi('ui.accounts.settlement');
    expect(expectUi('ui.accounts.settlement.edit')).toBe(
      screen.getByRole('button', { name: 'Edit Partner payouts' }),
    );
    expect(screen.queryByText('Payroll')).toBeNull();
  });

  it('creates a party account: POST with @type PartyAccount, the owner and the link', async () => {
    const { fetchMock } = backend({
      [`POST ${ACC}/partyAccount`]: () => json({ id: A1 }, { status: 201 }),
    });
    await open('Party');
    await screen.findByText('Payroll');
    fireEvent.click(screen.getByRole('button', { name: '+ New party account' }));
    expectUi('ui.accounts.party.new');
    const dialog = await screen.findByRole('dialog', { name: 'New party account' });
    expectUi('ui.accounts.party.form');
    expect(within(dialog).getByLabelText('Financial account')).toBe(
      expectUi('ui.accounts.party.financialLink'),
    );
    expect((within(dialog).getByLabelText('Financial account') as HTMLSelectElement).disabled).toBe(
      true,
    ); // no owner yet
    const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
    fireEvent.focus(owner);
    fireEvent.click(await within(dialog).findByRole('option', { name: /Romashka LLC/ }));
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Treasury' } });
    await within(dialog).findByRole('option', { name: 'Group receivables' });
    expect(within(dialog).getByRole('option', { name: 'Old group (closed)' })).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Financial account'), {
      target: { value: FIN1 },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('Treasury created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACC}/partyAccount`,
        JSON.stringify({
          '@type': 'PartyAccount',
          name: 'Treasury',
          relatedParty: [OWNER_P1],
          state: 'Active',
          financialAccount: ref(FIN1),
        }),
      ],
    ]);
  });

  it('creates a settlement account: POST with @type SettlementAccount and no link', async () => {
    const { fetchMock } = backend({
      [`POST ${ACC}/settlementAccount`]: () => json({ id: A1 }, { status: 201 }),
    });
    await open('Settlement');
    await screen.findByText('Partner payouts');
    fireEvent.click(screen.getByRole('button', { name: '+ New settlement account' }));
    expectUi('ui.accounts.settlement.new');
    const dialog = await screen.findByRole('dialog', { name: 'New settlement account' });
    expectUi('ui.accounts.settlement.form');
    expect(within(dialog).getByLabelText('Financial account')).toBe(
      expectUi('ui.accounts.settlement.financialLink'),
    );
    const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
    fireEvent.focus(owner);
    fireEvent.click(await within(dialog).findByRole('option', { name: /Romashka LLC/ }));
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Payouts' } });
    fireEvent.change(within(dialog).getByLabelText('Credit limit currency'), {
      target: { value: 'eur' },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Credit limit \(/), {
      target: { value: '10' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('Payouts created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACC}/settlementAccount`,
        '{"@type":"SettlementAccount","name":"Payouts","relatedParty":[' +
          JSON.stringify(OWNER_P1) +
          '],"state":"Active","creditLimit":{"unit":"EUR","value":10.00}}',
      ],
    ]);
  });

  it('edits: PATCH of the changed fields, then unlinks with null', async () => {
    const { fetchMock } = backend({
      [`PATCH ${ACC}/partyAccount/${A1}`]: () => json(LINKED),
      [`PATCH ${ACC}/partyAccount/${A2}`]: () => json(FREE),
    });
    await open('Party');
    await screen.findByText('Payroll');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Petty cash' }));
    expectUi('ui.accounts.party.edit');
    let dialog = await screen.findByRole('dialog', { name: 'Edit Petty cash' });
    await within(dialog).findByRole('option', { name: 'Group receivables' });
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Financial account'), {
      target: { value: FIN1 },
    });
    fireEvent.click(save);
    await screen.findByText('Petty cash saved');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Payroll' }));
    dialog = await screen.findByRole('dialog', { name: 'Edit Payroll' });
    await within(dialog).findByRole('option', { name: 'Group receivables' });
    expect((within(dialog).getByLabelText('Financial account') as HTMLSelectElement).value).toBe(
      FIN1,
    );
    fireEvent.change(within(dialog).getByLabelText('Financial account'), { target: { value: '' } });
    fireEvent.change(within(dialog).getByLabelText('Description'), { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Payroll saved');
    expect(writes(fetchMock)).toEqual([
      ['PATCH', `${ACC}/partyAccount/${A2}`, JSON.stringify({ financialAccount: ref(FIN1) })],
      [
        'PATCH',
        `${ACC}/partyAccount/${A1}`,
        JSON.stringify({ description: null, financialAccount: null }),
      ],
    ]);
  });

  it('a new owner starts without a link, and the server refusal is shown', async () => {
    const { fetchMock } = backend({
      [`PATCH ${ACC}/partyAccount/${A1}`]: () => tmfError(422, 'financialAccount: another owner'),
    });
    await open('Party');
    await screen.findByText('Payroll');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Payroll' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit Payroll' });
    const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
    fireEvent.focus(owner);
    fireEvent.change(owner, { target: { value: '' } });
    fireEvent.click(await within(dialog).findByRole('option', { name: /Olga Kuznetsova/ }));
    expect((within(dialog).getByLabelText('Financial account') as HTMLSelectElement).value).toBe(
      '',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('another owner');
    expect(owner).toHaveProperty('value', 'Olga Kuznetsova · person');
    const sent = writes(fetchMock)[0]?.[2];
    expect(JSON.parse(String(sent))).toEqual({
      relatedParty: [
        {
          role: 'owner',
          partyOrPartyRole: { id: OLGA, '@type': 'PartyRef', '@referredType': 'Individual' },
          '@type': 'RelatedPartyRefOrPartyRoleRef',
        },
      ],
      financialAccount: null,
    });
  });

  it('deletes any party or settlement account: DELETE, no body', async () => {
    const { fetchMock } = backend({
      [`DELETE ${ACC}/partyAccount/${A1}`]: () => new Response(null, { status: 204 }),
      [`DELETE ${ACC}/settlementAccount/${A1}`]: () => new Response(null, { status: 204 }),
    });
    await open('Party');
    await screen.findByText('Payroll');
    expect(screen.getAllByRole('button', { name: /^Delete / })).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Delete Payroll' }));
    expectUi('ui.accounts.party.delete');
    const dialog = await screen.findByRole('dialog', { name: 'Delete Payroll' });
    expectUi('ui.accounts.party.confirmDelete');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await screen.findByText('Payroll deleted');
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account sections' })).getByRole('button', {
        name: 'Settlement',
      }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Partner payouts' }));
    expectUi('ui.accounts.settlement.delete');
    const confirm = await screen.findByRole('dialog', { name: 'Delete Partner payouts' });
    expect(confirm).toBe(expectUi('ui.accounts.settlement.confirmDelete'));
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await screen.findByText('Partner payouts deleted');
    expect(writes(fetchMock)).toEqual([
      ['DELETE', `${ACC}/partyAccount/${A1}`, null],
      ['DELETE', `${ACC}/settlementAccount/${A1}`, null],
    ]);
  });
});
