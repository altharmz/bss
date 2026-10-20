import { fireEvent, render, screen, within } from '@testing-library/react';
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
  accountBalance: [balance('EUR', 7), balance('RUB', -120.5)],
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
  return screen.findByRole('heading', { name: /Financial accounts/ });
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
    expect(link.getAttribute('href')).toBe(`/subscribers/${B1}`);
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
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    fireEvent.click(within(nav).getByRole('link', { name: /Financial accounts/ }));
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
});
