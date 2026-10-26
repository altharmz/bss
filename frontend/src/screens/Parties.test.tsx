import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';

const PARTY = '/tmf-api/partyManagement/v5';
const CUSTOMERS = '/tmf-api/customer/v5/customer';
const ACCOUNTS = '/tmf-api/accountManagement/v5';
const PAGE = 'offset=0&limit=100';

const IVAN = 'aaaa1111-0000-4000-8000-000000000001';
const OLGA = 'aaaa1111-0000-4000-8000-000000000002';
const PAVEL = 'aaaa1111-0000-4000-8000-000000000003';
const ROMASHKA = 'cccc3333-0000-4000-8000-000000000003';
const EMPTY_ORG = 'cccc3333-0000-4000-8000-000000000004';
const C_IVAN = 'dddd0000-0000-4000-8000-000000000001';
const C_ORPHAN = 'dddd0000-0000-4000-8000-000000000002';
const C_ROMASHKA = 'dddd0000-0000-4000-8000-000000000003';
const A1 = '11111111-0000-4000-8000-0000000000a1';
const A3 = '33333333-0000-4000-8000-0000000000a3';
const FIN = 'f0000000-0000-4000-8000-000000000001';

const individual = (id: string, givenName: string, familyName: string, status = 'validated') => ({
  id,
  givenName,
  familyName,
  status,
  '@type': 'Individual',
});
const organization = (id: string, name: string, status = 'validated') => ({
  id,
  name,
  status,
  '@type': 'Organization',
});
const customer = (id: string, name: string, party: string, kind: string, status = 'Active') => ({
  id,
  name,
  status,
  engagedParty: { id: party, name, '@referredType': kind, '@type': 'PartyRef' },
  '@type': 'Customer',
});
const account = (id: string, name: string, customerId: string) => ({
  id,
  name,
  relatedParty: [{ role: 'customer', partyOrPartyRole: { id: customerId } }],
});
const owned = (partyId: string) => ({
  id: FIN,
  relatedParty: [{ role: 'owner', partyOrPartyRole: { id: partyId } }],
});

const INDIVIDUALS = [
  individual(IVAN, 'Ivan', 'Petrov'),
  individual(OLGA, 'Olga', 'Kuznetsova', 'initialized'),
  individual(PAVEL, 'Pavel', 'Sidorov'),
];
const ORGANIZATIONS = [organization(ROMASHKA, 'Romashka LLC'), organization(EMPTY_ORG, 'Lone Ltd')];
const CUSTOMER_ROWS = [
  customer(C_IVAN, 'Ivan Petrov', IVAN, 'Individual'),
  customer(C_ORPHAN, 'Ivan Second', IVAN, 'Individual', 'Created'),
  customer(C_ROMASHKA, 'Romashka LLC', ROMASHKA, 'Organization'),
];
const ACCOUNT_ROWS = [account(A1, 'Ivan main', C_IVAN), account(A3, 'Romashka HQ', C_ROMASHKA)];

function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    [NAV_RUNS]: noRuns,
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [`GET ${ACCOUNTS}/billingAccount?limit=1&fields=id`]: () => json([], { total: 2 }),
    [`GET ${ACCOUNTS}/financialAccount?limit=1&fields=id`]: () => json([], { total: 1 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 3 }),
    [`GET ${PARTY}/individual?${PAGE}`]: () => json(INDIVIDUALS, { total: INDIVIDUALS.length }),
    [`GET ${PARTY}/organization?${PAGE}`]: () =>
      json(ORGANIZATIONS, { total: ORGANIZATIONS.length }),
    [`GET ${CUSTOMERS}?fields=name,status,engagedParty&${PAGE}`]: () =>
      json(CUSTOMER_ROWS, { total: CUSTOMER_ROWS.length }),
    [`GET ${ACCOUNTS}/billingAccount?fields=name,relatedParty&${PAGE}`]: () =>
      json(ACCOUNT_ROWS, { total: ACCOUNT_ROWS.length }),
    [`GET ${ACCOUNTS}/financialAccount?fields=relatedParty&${PAGE}`]: () =>
      json([owned(PAVEL)], { total: 1 }),
    [`GET ${ACCOUNTS}/partyAccount?fields=relatedParty&${PAGE}`]: () => json([], { total: 0 }),
    [`GET ${ACCOUNTS}/settlementAccount?fields=relatedParty&${PAGE}`]: () => json([], { total: 0 }),
    ...extra,
  });
}

async function open() {
  window.history.replaceState(null, '', '/parties');
  render(<App />);
  return screen.findByRole('heading', { name: 'Parties' });
}

const tab = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const rowOf = (name: RegExp) =>
  screen.getAllByRole('row').find((row) => name.test(row.textContent ?? ''));
const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

describe('parties list', () => {
  it('lists individuals, organizations and customers from the three list requests', async () => {
    const { calls } = backend();
    await open();
    expectUi('ui.parties');
    expect(await screen.findByText('Olga Kuznetsova')).toBeTruthy();
    expectUi('ui.parties.individuals');
    // Ivan has two customers; his one billing account is a link to its card
    expect(rowOf(/Ivan Petrov/)?.textContent).toContain('2');
    const link = within(rowOf(/Ivan Petrov/) as HTMLElement).getByRole('link', {
      name: 'Ivan main',
    });
    expect(link.getAttribute('href')).toBe(`/subscribers/${A1}`);
    // a person without a customer or an account is reachable too
    expect(rowOf(/Olga/)?.textContent).toContain('Initialized');

    tab('Organizations');
    expect(await screen.findByText('Lone Ltd')).toBeTruthy();
    expectUi('ui.parties.organizations');
    expect(
      within(rowOf(/Romashka LLC/) as HTMLElement)
        .getByRole('link', { name: 'Romashka HQ' })
        .getAttribute('href'),
    ).toBe(`/subscribers/${A3}`);

    tab('Customers');
    expect(await screen.findByText('Ivan Second')).toBeTruthy();
    expectUi('ui.parties.customers');
    expect(rowOf(/Ivan Petrov/)?.textContent).toContain('Ivan Petrov');
    expect(rowOf(/Ivan Second/)?.textContent).toContain('Created');
    expect(calls).toContain(`GET ${PARTY}/individual?${PAGE}`);
    expect(calls).toContain(`GET ${PARTY}/organization?${PAGE}`);
    expect(calls).toContain(`GET ${CUSTOMERS}?fields=name,status,engagedParty&${PAGE}`);
  });

  it('searches by name and filters by status', async () => {
    backend();
    await open();
    await screen.findByText('Olga Kuznetsova');
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: 'pav' } });
    expect(screen.queryByText('Olga Kuznetsova')).toBeNull();
    expect(screen.getByText('Pavel Sidorov')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /initialized/ }));
    expect(screen.getByText('Olga Kuznetsova')).toBeTruthy();
    expect(screen.queryByText('Pavel Sidorov')).toBeNull();
  });

  it('is in the sidebar under Customers and shows a load failure', async () => {
    backend({ [`GET ${PARTY}/individual?${PAGE}`]: () => tmfError(503, 'Unavailable') });
    await open();
    expect(await screen.findByText('Failed to load parties')).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Parties' }).getAttribute('href')).toBe(
      '/parties',
    );
  });
});

describe('party and customer dialogs', () => {
  it('edits only the changed names of a person (merge patch) and refreshes', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/individual/${OLGA}`]: () => json(INDIVIDUALS[1]),
      [`PATCH ${PARTY}/individual/${OLGA}`]: () => json({}),
    });
    await open();
    fireEvent.click(await screen.findByText('Olga Kuznetsova'));
    const dialog = await screen.findByRole('dialog', { name: 'Olga Kuznetsova' });
    expectUi('ui.parties.individual');
    await within(dialog).findByText('Given name');
    expectUi('ui.parties.edit');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed
    fireEvent.change(within(dialog).getByLabelText('Family name'), {
      target: { value: ' Kuznetsova-Lee ' },
    });
    fireEvent.click(save);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Olga Kuznetsova saved',
    );
    expect(writes(fetchMock)).toEqual([
      ['PATCH', `${PARTY}/individual/${OLGA}`, '{"familyName":"Kuznetsova-Lee"}'],
    ]);
  });

  it('edits an organization name and a customer name and status', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/organization/${EMPTY_ORG}`]: () => json(ORGANIZATIONS[1]),
      [`PATCH ${PARTY}/organization/${EMPTY_ORG}`]: () => json({}),
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
      [`PATCH ${CUSTOMERS}/${C_ORPHAN}`]: () => json({}),
    });
    await open();
    tab('Organizations');
    fireEvent.click(await screen.findByText('Lone Ltd'));
    let dialog = await screen.findByRole('dialog', { name: 'Lone Ltd' });
    await within(dialog).findByText('Organization name');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(dialog).getByLabelText('Organization name'), {
      target: { value: 'Lone Holdings' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Lone Ltd saved');

    tab('Customers');
    fireEvent.click(await screen.findByText('Ivan Second'));
    dialog = await screen.findByRole('dialog', { name: 'Ivan Second' });
    expectUi('ui.parties.customer');
    await within(dialog).findByText('Engaged party');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(dialog).getByLabelText('Customer name'), {
      target: { value: 'Ivan P.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Inactive' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Ivan Second saved');
    expect(writes(fetchMock)).toEqual([
      ['PATCH', `${PARTY}/organization/${EMPTY_ORG}`, '{"name":"Lone Holdings"}'],
      ['PATCH', `${CUSTOMERS}/${C_ORPHAN}`, '{"name":"Ivan P.","status":"Inactive"}'],
    ]);
  });

  it('deletes a party with no customers and accounts: DELETE, no body', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/individual/${OLGA}`]: () => json(INDIVIDUALS[1]),
      [`DELETE ${PARTY}/individual/${OLGA}`]: () => new Response(null, { status: 204 }),
    });
    await open();
    fireEvent.click(await screen.findByText('Olga Kuznetsova'));
    const dialog = await screen.findByRole('dialog', { name: 'Olga Kuznetsova' });
    expectUi('ui.parties.delete');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    await screen.findByText('Olga Kuznetsova deleted');
    expect(writes(fetchMock)).toEqual([['DELETE', `${PARTY}/individual/${OLGA}`, null]]);
  });

  it('hides Delete when the data shows a dependency', async () => {
    backend({
      [`GET ${PARTY}/individual/${IVAN}`]: () => json(INDIVIDUALS[0]),
      [`GET ${PARTY}/individual/${PAVEL}`]: () => json(INDIVIDUALS[2]),
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
    });
    await open();
    fireEvent.click(await screen.findByText('Ivan Petrov'));
    let dialog = await screen.findByRole('dialog', { name: 'Ivan Petrov' });
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(dialog).getByText(/It has 2 customers/)).toBeTruthy();
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[1] as HTMLElement);
    // an owned financial account blocks the party without customers
    fireEvent.click(screen.getByText('Pavel Sidorov'));
    dialog = await screen.findByRole('dialog', { name: 'Pavel Sidorov' });
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(dialog).getByText(/It owns accounts/)).toBeTruthy();
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[1] as HTMLElement);
    // a customer with a billing account
    tab('Customers');
    fireEvent.click((await screen.findAllByText('Ivan Petrov'))[0] as HTMLElement);
    dialog = await screen.findByRole('dialog', { name: 'Ivan Petrov' });
    expect(within(dialog).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(dialog).getByText(/It has 1 billing account/)).toBeTruthy();
  });

  it('deletes a customer without accounts and shows the server 409 for a party', async () => {
    const { fetchMock } = backend({
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
      [`DELETE ${CUSTOMERS}/${C_ORPHAN}`]: () => new Response(null, { status: 204 }),
      [`GET ${PARTY}/organization/${EMPTY_ORG}`]: () => json(ORGANIZATIONS[1]),
      [`DELETE ${PARTY}/organization/${EMPTY_ORG}`]: () =>
        tmfError(409, 'Referenced by a customer'),
    });
    await open();
    tab('Organizations');
    fireEvent.click(await screen.findByText('Lone Ltd'));
    let dialog = await screen.findByRole('dialog', { name: 'Lone Ltd' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByRole('alert')).toBeTruthy();
    expect(within(dialog).getByRole('alert').textContent).toContain('409');
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[1] as HTMLElement);
    tab('Customers');
    fireEvent.click(await screen.findByText('Ivan Second'));
    dialog = await screen.findByRole('dialog', { name: 'Ivan Second' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    await screen.findByText('Ivan Second deleted');
    expect(writes(fetchMock)).toEqual([
      ['DELETE', `${PARTY}/organization/${EMPTY_ORG}`, null],
      ['DELETE', `${CUSTOMERS}/${C_ORPHAN}`, null],
    ]);
  });
});
