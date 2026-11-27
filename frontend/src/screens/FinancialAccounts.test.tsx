import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import {
  CONTEXT,
  json,
  NAV_RUNS,
  noRuns,
  pending,
  stubFetch,
  tmfError,
  type Handler,
} from '../test/fetch';

const LIST = 'GET /tmf-api/accountManagement/v5/financialAccount?offset=0&limit=100';
const F1 = 'f0000001-0000-4000-8000-000000000001';
const F2 = 'f0000002-0000-4000-8000-000000000002';
const B1 = 'b0000001-0000-4000-8000-000000000001';

const owner = (name: string, type: string) => [
  {
    role: 'owner',
    partyOrPartyRole: { id: 'p1', name, '@type': 'PartyRef', '@referredType': type },
    '@type': 'RelatedPartyRefOrPartyRoleRef',
  },
];
const balance = (unit: string, value: number) => ({
  balanceType: 'currentBalance',
  amount: { unit, value },
  '@type': 'AccountBalance',
});
const contains = (id: string, name: string, type: string) => ({
  relationshipType: 'contains',
  account: { id, name, '@type': 'AccountRef', '@referredType': type },
  '@type': 'AccountRelationship',
});

const GROUP = {
  id: F1,
  href: `/tmf-api/accountManagement/v5/financialAccount/${F1}`,
  name: 'Group receivables',
  accountType: 'Business',
  state: 'Active',
  relatedParty: owner('Romashka LLC', 'Organization'),
  accountBalance: [balance('EUR', 7), balance('USD', -120.5)],
  accountRelationship: [
    contains(B1, 'Main account', 'BillingAccount'),
    contains('s0000001-0000-4000-8000-000000000001', 'Partner payouts', 'SettlementAccount'),
  ],
  '@type': 'FinancialAccount',
};
const EMPTY = {
  id: F2,
  href: `/tmf-api/accountManagement/v5/financialAccount/${F2}`,
  name: 'Personal',
  state: 'Suspended',
  // parties in other roles may come first: the owner is found by its role
  relatedParty: [
    {
      role: 'contact',
      partyOrPartyRole: { id: '6666', name: 'John Doe', '@type': 'PartyRef' },
      '@type': 'RelatedPartyRefOrPartyRoleRef',
    },
    ...owner('Ivan Ivanov', 'Individual'),
  ],
  accountBalance: [],
  accountRelationship: [],
  '@type': 'FinancialAccount',
};

function backend(list: Handler) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [LIST]: list,
  });
}

async function open() {
  window.history.replaceState(null, '', '/financial-accounts');
  render(<App />);
  return screen.findByRole('heading', { name: /^Accounts/ });
}

const rowOf = (name: string) =>
  screen.getAllByRole('row').find((r) => within(r).queryByText(name)) as HTMLElement;

describe('financial accounts', () => {
  it('lists owner, state, balance per currency and the contained accounts', async () => {
    backend(() => json([GROUP, EMPTY], { total: 2 }));
    await open();
    await screen.findByText('Group receivables');
    expectUi('ui.financialAccounts.list');
    const group = rowOf('Group receivables');
    expect(within(group).getByText('F0000001')).toBeTruthy();
    expect(within(group).getByText(/Romashka LLC/)).toBeTruthy();
    expect(within(group).getByText('7.00')).toBeTruthy();
    expect(within(group).getByText('−120.50').closest('.bss-amount')?.className).toContain(
      'negative',
    );
    expect(within(group).getByText('EUR')).toBeTruthy();
    const link = within(group).getByRole('link', { name: 'Main account' });
    expect(link.getAttribute('href')).toBe(`/billing-accounts/${B1}`);
    expect(within(group).getByText('(billing)')).toBeTruthy();
    // settlement and party accounts have no screen: named, not linked
    expect(within(group).getByText('Partner payouts').closest('a')).toBeNull();
    expect(within(group).getByText('(settlement)')).toBeTruthy();
    expect(within(group).getAllByRole('link')).toHaveLength(1);
    const empty = rowOf('Personal');
    expect(within(empty).getByText(/Ivan Ivanov/)).toBeTruthy();
    expect(within(empty).queryByText(/John Doe/)).toBeNull();
    expect(within(empty).getByText('nothing linked')).toBeTruthy();
    expect(within(empty).getByText('—')).toBeTruthy();
    expect(within(empty).getByText('Suspended')).toBeTruthy();
  });

  it('is reachable from the sidebar', async () => {
    backend(() => json([], { total: 0 }));
    window.history.replaceState(null, '', '/nowhere'); // a screen that loads nothing
    render(<App />);
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(within(nav).getByRole('link', { name: /^Financial accounts/ }));
    expect(await screen.findByText('No financial accounts yet')).toBeTruthy();
    expect(window.location.pathname).toBe('/financial-accounts');
  });

  it('shows the error with a retry', async () => {
    let calls = 0;
    backend(() => (calls++ === 0 ? tmfError(500, 'database down') : pending()));
    await open();
    expect(await screen.findByText('Failed to load financial accounts')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(calls).toBe(2);
  });

  describe('writes', () => {
    const FIN = '/tmf-api/accountManagement/v5/financialAccount';
    const OLGA = 'aaaa1111-0000-4000-8000-000000000002';
    const ROMASHKA = 'p1';
    // TEST-CHANGE (#314): the owners come from the party search, as typed, not every party
    const PARTIES = [
      { id: OLGA, kind: 'Individual', name: 'Olga Kuznetsova' },
      { id: ROMASHKA, kind: 'Organization', name: 'Romashka LLC' },
    ];
    const choices: Record<string, Handler> = {
      'GET /internal/v1/party?*': (url) => {
        const q = (new URL(url ?? '', 'http://test').searchParams.get('q') ?? '').toLowerCase();
        return json(PARTIES.filter((p) => p.name.toLowerCase().startsWith(q)));
      },
    };
    const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
      fetchMock.mock.calls
        .filter(([, init]) => init?.method && init.method !== 'GET')
        .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);
    const withWrites = (extra: Record<string, Handler>) =>
      stubFetch({
        'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
          json([], { total: 23 }),
        'GET /internal/v1/context': () => json(CONTEXT),
        'GET /internal/v1/session': () =>
          json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
        [NAV_RUNS]: noRuns,
        'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
          json([], { total: 7 }),
        'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
          json([], { total: 4 }),
        [LIST]: () => json([GROUP, EMPTY], { total: 2 }),
        ...choices,
        ...extra,
      });
    const OWNER_P1 = {
      role: 'owner',
      partyOrPartyRole: { id: ROMASHKA, '@type': 'PartyRef', '@referredType': 'Organization' },
      '@type': 'RelatedPartyRefOrPartyRoleRef',
    };

    it('creates a financial account: POST with the owner ref and an exact limit', async () => {
      const { fetchMock } = withWrites({
        [`POST ${FIN}`]: () => json({ id: F1 }, { status: 201 }),
      });
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'New financial account' }));
      expectUi('ui.financialAccounts.new');
      const dialog = await screen.findByRole('dialog', { name: 'New financial account' });
      expectUi('ui.financialAccounts.form');
      const create = within(dialog).getByRole('button', { name: 'Create' }) as HTMLButtonElement;
      const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
      expect(create.disabled).toBe(true);
      fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' Holding ' } });
      fireEvent.focus(owner);
      fireEvent.click(await within(dialog).findByRole('option', { name: /Romashka LLC/ }));
      expectUi('ui.financialAccounts.owner');
      fireEvent.change(within(dialog).getByLabelText('Account type'), {
        target: { value: 'Business' },
      });
      fireEvent.change(within(dialog).getByLabelText('Description'), {
        target: { value: 'All lines' },
      });
      fireEvent.change(within(dialog).getByLabelText(/^Credit limit \(/), {
        target: { value: '1,000.1' },
      });
      fireEvent.click(create);
      await screen.findByText('Holding created');
      expect(writes(fetchMock)).toEqual([
        [
          'POST',
          FIN,
          '{"@type":"FinancialAccount","name":"Holding","relatedParty":[' +
            JSON.stringify(OWNER_P1) +
            '],"state":"Active","accountType":"Business","description":"All lines",' +
            '"creditLimit":{"unit":"USD","value":1000.10}}',
        ],
      ]);
    });

    it('edits: PATCH of only the changed fields; the owner keeps other parties', async () => {
      const { fetchMock } = withWrites({
        [`PATCH ${FIN}/${F1}`]: () => json(GROUP),
        [`PATCH ${FIN}/${F2}`]: () => json(EMPTY),
      });
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'Edit Personal' }));
      expectUi('ui.financialAccounts.edit');
      let dialog = await screen.findByRole('dialog', { name: 'Edit Personal' });
      const ownerPicker = within(dialog).getByRole('combobox', { name: 'Owner' });
      const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
      expect(save.disabled).toBe(true); // nothing changed
      fireEvent.focus(ownerPicker);
      fireEvent.change(ownerPicker, { target: { value: '' } });
      fireEvent.click(await within(dialog).findByRole('option', { name: /Olga Kuznetsova/ }));
      fireEvent.change(within(dialog).getByLabelText('Description'), {
        target: { value: 'Spare' },
      });
      fireEvent.click(
        within(within(dialog).getByRole('group', { name: 'State' })).getByRole('button', {
          name: 'Active',
        }),
      );
      fireEvent.click(save);
      await screen.findByText('Personal saved');
      fireEvent.click(await screen.findByRole('button', { name: 'Edit Group receivables' }));
      dialog = await screen.findByRole('dialog', { name: 'Edit Group receivables' });
      expect(within(dialog).getByRole('combobox', { name: 'Owner' })).toHaveProperty(
        'value',
        'Romashka LLC · organization',
      );
      fireEvent.change(within(dialog).getByLabelText('Account type'), { target: { value: '' } });
      fireEvent.change(within(dialog).getByLabelText(/^Credit limit \(/), {
        target: { value: '250' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
      await screen.findByText('Group receivables saved');
      const owner = {
        role: 'owner',
        partyOrPartyRole: { id: OLGA, '@type': 'PartyRef', '@referredType': 'Individual' },
        '@type': 'RelatedPartyRefOrPartyRoleRef',
      };
      const contact = {
        role: 'contact',
        partyOrPartyRole: { id: '6666', name: 'John Doe', '@type': 'PartyRef' },
        '@type': 'RelatedPartyRefOrPartyRoleRef',
      };
      expect(writes(fetchMock)).toEqual([
        [
          'PATCH',
          `${FIN}/${F2}`,
          JSON.stringify({ description: 'Spare', state: 'Active', relatedParty: [owner, contact] }),
        ],
        [
          'PATCH',
          `${FIN}/${F1}`,
          '{"accountType":null,"creditLimit":{"unit":"USD","value":250.00}}',
        ],
      ]);
    });

    it('searches the owners on the server as the name is typed', async () => {
      const { calls } = withWrites({});
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'New financial account' }));
      const dialog = await screen.findByRole('dialog', { name: 'New financial account' });
      const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
      await waitFor(() => expect(owner.hasAttribute('disabled')).toBe(false));
      expect(calls).toContain('GET /internal/v1/party?offset=0&limit=20');
      fireEvent.focus(owner);
      fireEvent.change(owner, { target: { value: 'Rom' } });
      await waitFor(() =>
        expect(calls).toContain('GET /internal/v1/party?q=Rom&offset=0&limit=20'),
      );
      expect(await within(dialog).findByRole('option', { name: /Romashka LLC/ })).toBeTruthy();
    });

    it('shows the server refusal of an owner change and keeps the values', async () => {
      withWrites({
        [`PATCH ${FIN}/${F1}`]: () =>
          tmfError(409, 'relatedParty: the owner cannot change while accounts link'),
      });
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'Edit Group receivables' }));
      const dialog = await screen.findByRole('dialog', { name: 'Edit Group receivables' });
      const owner = within(dialog).getByRole('combobox', { name: 'Owner' });
      fireEvent.focus(owner);
      fireEvent.change(owner, { target: { value: '' } });
      fireEvent.click(await within(dialog).findByRole('option', { name: /Olga Kuznetsova/ }));
      fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
      const alert = await within(dialog).findByRole('alert');
      expect(alert.textContent).toContain('owner cannot change');
      expect(owner).toHaveProperty('value', 'Olga Kuznetsova · person');
    });

    it('offers Delete only when nothing is linked: DELETE, no body', async () => {
      const { fetchMock } = withWrites({
        [`DELETE ${FIN}/${F2}`]: () => new Response(null, { status: 204 }),
      });
      await open();
      await screen.findByText('Group receivables');
      expect(screen.queryByRole('button', { name: 'Delete Group receivables' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Delete Personal' }));
      expectUi('ui.financialAccounts.delete');
      const dialog = await screen.findByRole('dialog', { name: 'Delete Personal' });
      expectUi('ui.financialAccounts.confirmDelete');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
      await screen.findByText('Personal deleted');
      expect(writes(fetchMock)).toEqual([['DELETE', `${FIN}/${F2}`, null]]);
    });

    // #499: Cancel sends nothing; an unanswered save or delete offers only Refresh
    it('cancels New, Edit and Delete without a write', async () => {
      const { fetchMock } = withWrites({});
      await open();
      await screen.findByText('Group receivables');
      for (const [button, title] of [
        ['New financial account', 'New financial account'],
        ['Edit Personal', 'Edit Personal'],
        ['Delete Personal', 'Delete Personal'],
      ] as const) {
        fireEvent.click(screen.getByRole('button', { name: button }));
        const dialog = await screen.findByRole('dialog', { name: title });
        fireEvent.click(await within(dialog).findByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('dialog', { name: title })).toBeNull();
      }
      expect(writes(fetchMock)).toEqual([]);
    });

    it('offers only Refresh after an unanswered save, which closes and reloads', async () => {
      let lists = 0;
      const { fetchMock } = withWrites({
        [LIST]: () => {
          lists += 1;
          return json([GROUP, EMPTY], { total: 2 });
        },
        [`PATCH ${FIN}/${F2}`]: () => tmfError(502, 'Bad Gateway'),
      });
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'Edit Personal' }));
      const dialog = await screen.findByRole('dialog', { name: 'Edit Personal' });
      fireEvent.change(await within(dialog).findByLabelText('Name'), {
        target: { value: 'Personal 2' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

      expect(await within(dialog).findByText('Unsure whether it was saved.')).toBeTruthy();
      expect(within(dialog).queryByRole('button', { name: 'Save' })).toBeNull();
      const before = lists;
      fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
      expect(await screen.findByText('Refreshed')).toBeTruthy();
      expect(screen.queryByRole('dialog', { name: 'Edit Personal' })).toBeNull();
      await waitFor(() => expect(lists).toBe(before + 1));
      expect(writes(fetchMock)).toEqual([['PATCH', `${FIN}/${F2}`, '{"name":"Personal 2"}']]);
    });

    it('offers only Refresh after an unanswered delete, which closes and reloads', async () => {
      let lists = 0;
      const { fetchMock } = withWrites({
        [LIST]: () => {
          lists += 1;
          return json([GROUP, EMPTY], { total: 2 });
        },
        [`DELETE ${FIN}/${F2}`]: () => tmfError(503, 'Service Unavailable'),
      });
      await open();
      await screen.findByText('Group receivables');
      fireEvent.click(screen.getByRole('button', { name: 'Delete Personal' }));
      const dialog = await screen.findByRole('dialog', { name: 'Delete Personal' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

      expect(await within(dialog).findByText('Unsure whether it was saved.')).toBeTruthy();
      expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
      const before = lists;
      fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
      expect(await screen.findByText('Refreshed')).toBeTruthy();
      expect(screen.queryByRole('dialog', { name: 'Delete Personal' })).toBeNull();
      await waitFor(() => expect(lists).toBe(before + 1));
      expect(writes(fetchMock)).toEqual([['DELETE', `${FIN}/${F2}`, null]]);
    });
  });
});
