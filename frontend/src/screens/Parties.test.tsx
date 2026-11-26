import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../app/App';
import {
  CONTEXT,
  json,
  NAV_RUNS,
  noRuns,
  pending,
  stubFetch,
  tmfCodeError,
  tmfError,
  type Handler,
} from '../test/fetch';
import { expectUi } from '../test/expectUi';

const PARTY = '/tmf-api/partyManagement/v5';
const CUSTOMERS = '/tmf-api/customer/v5/customer';
const ACCOUNTS = '/tmf-api/accountManagement/v5';

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
const account = (id: string, name: string, customerId: string) => ({ id, name, customerId });
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

type Row = { name: string; status: string; id: string };

/** The server's paging and prefix search (#314) over `rows`, by `name` (and `extra`). */
function serve<T extends Row>(rows: T[], names: (row: T) => string[] = (r) => [r.name]): Handler {
  return (url) => {
    const query = new URL(url ?? '', 'http://test').searchParams;
    const q = (query.get('q') ?? '').toLowerCase();
    const offset = Number(query.get('offset') ?? 0);
    const limit = Number(query.get('limit') ?? 20);
    const found = rows.filter(
      (row) =>
        (!q || names(row).some((n) => n.toLowerCase().startsWith(q)) || row.id.startsWith(q)) &&
        (!query.get('status') || row.status === query.get('status')),
    );
    return json(found.slice(offset, offset + limit), { more: found.length > offset + limit });
  };
}

/** GET /internal/v1/party rows, made from the fixtures. */
function partyRows(kind: 'Individual' | 'Organization') {
  const parties =
    kind === 'Individual'
      ? INDIVIDUALS.map((p) => ({ ...p, name: `${p.givenName} ${p.familyName}` }))
      : ORGANIZATIONS;
  return parties.map((p) => {
    const customers = CUSTOMER_ROWS.filter((c) => c.engagedParty.id === p.id);
    return {
      id: p.id,
      kind,
      name: p.name,
      status: p.status,
      family: 'familyName' in p ? String(p.familyName) : p.name,
      customers: customers.map((c) => ({ id: c.id, name: c.name })),
      billingAccounts: ACCOUNT_ROWS.filter((a) => customers.some((c) => c.id === a.customerId)),
      ownsAccounts: p.id === PAVEL,
    };
  });
}

const customerRows = () =>
  CUSTOMER_ROWS.map((c) => ({
    id: c.id,
    name: c.name,
    status: c.status,
    party: {
      id: c.engagedParty.id,
      kind: c.engagedParty['@referredType'],
      name: [...INDIVIDUALS, ...ORGANIZATIONS]
        .filter((p) => p.id === c.engagedParty.id)
        .map((p) => ('givenName' in p ? `${p.givenName} ${p.familyName}` : p.name))[0],
    },
    billingAccounts: ACCOUNT_ROWS.filter((a) => a.customerId === c.id).map(({ id, name }) => ({
      id,
      name,
    })),
  }));

const PARTIES = '/internal/v1/party';
const SEARCH_CUSTOMERS = '/internal/v1/partyCustomer';
const PAGE = 'offset=0&limit=50';

function backend(extra: Record<string, Handler> = {}) {
  const individuals = serve(partyRows('Individual'), (r) => [r.name, r.family]);
  const organizations = serve(partyRows('Organization'));
  return stubFetch({
    [NAV_RUNS]: noRuns,
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [`GET ${ACCOUNTS}/billingAccount?limit=1&fields=id`]: () => json([], { total: 2 }),
    [`GET ${ACCOUNTS}/financialAccount?limit=1&fields=id`]: () => json([], { total: 1 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 3 }),
    [`GET ${PARTIES}?kind=Individual*`]: individuals,
    [`GET ${PARTIES}?kind=Organization*`]: organizations,
    [`GET ${SEARCH_CUSTOMERS}?*`]: serve(customerRows()),
    ...extra,
  });
}

async function open() {
  window.history.replaceState(null, '', '/parties');
  render(<App />);
  return screen.findByRole('heading', { name: 'Parties' });
}

const tab = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
// TEST-CHANGE (#457): Customers is its own screen, opened from the sidebar, not a Parties tab
const customers = async () => {
  fireEvent.click(
    within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', {
      name: 'Customers',
    }),
  );
  return screen.findByRole('heading', { name: 'Customers' });
};
const rowOf = (name: RegExp) =>
  screen.getAllByRole('row').find((row) => name.test(row.textContent ?? ''));
const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

describe('parties list', () => {
  it('lists individuals, organizations and customers, one page each', async () => {
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
    expect(link.getAttribute('href')).toBe(`/billing-accounts/${A1}`);
    // a person without a customer or an account is reachable too
    expect(rowOf(/Olga/)?.textContent).toContain('Initialized');
    // party kind: initialized is Draft, validated is Live
    const pill = (row: HTMLElement | undefined, label: string) =>
      within(row as HTMLElement).getByText(label).classList;
    expect(pill(rowOf(/Olga/), 'Initialized').contains('pill-draft')).toBe(true);
    expect(pill(rowOf(/Ivan Petrov/), 'Validated').contains('pill-live')).toBe(true);

    tab('Organizations');
    expect(await screen.findByText('Lone Ltd')).toBeTruthy();
    expectUi('ui.parties.organizations');
    expect(
      within(rowOf(/Romashka LLC/) as HTMLElement)
        .getByRole('link', { name: 'Romashka HQ' })
        .getAttribute('href'),
    ).toBe(`/billing-accounts/${A3}`);

    await customers();
    expect(await screen.findByText('Ivan Second')).toBeTruthy();
    expectUi('ui.parties.customers');
    expect(rowOf(/Ivan Petrov/)?.textContent).toContain('Ivan Petrov');
    expect(rowOf(/Ivan Second/)?.textContent).toContain('Created');
    // customer kind: Created is Draft
    expect(pill(rowOf(/Ivan Second/), 'Created').contains('pill-draft')).toBe(true);
    // TEST-CHANGE (#314): one paged search per section, its figures computed by the server
    expect(calls).toContain(`GET ${PARTIES}?kind=Individual&${PAGE}`);
    expect(calls).toContain(`GET ${PARTIES}?kind=Organization&${PAGE}`);
    expect(calls).toContain(`GET ${SEARCH_CUSTOMERS}?${PAGE}`);
  });

  // TEST-CHANGE (#314): a prefix search and the status on the server, without counts
  it('searches by the start of a name and filters by status on the server', async () => {
    const { calls } = backend();
    await open();
    await screen.findByText('Olga Kuznetsova');
    expect(
      within(screen.getByRole('group', { name: 'Status' }))
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All', 'initialized', 'validated', 'deceased']);
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: 'sid' } });
    await waitFor(() => expect(calls).toContain(`GET ${PARTIES}?kind=Individual&q=sid&${PAGE}`));
    await waitFor(() => expect(screen.queryByText('Olga Kuznetsova')).toBeNull());
    expect(await screen.findByText('Pavel Sidorov')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: 'nobody' } });
    expect(await screen.findByText('Nothing matches “nobody”')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'initialized' }));
    expect(await screen.findByText('Olga Kuznetsova')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('Pavel Sidorov')).toBeNull());
    expect(calls).toContain(`GET ${PARTIES}?kind=Individual&status=initialized&${PAGE}`);
  });

  it('pages with Previous and Next', async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      id: `p${i}`,
      kind: 'Organization',
      name: `Org ${String(i).padStart(2, '0')}`,
      status: 'validated',
      customers: [],
      billingAccounts: [],
      ownsAccounts: false,
    }));
    backend({ [`GET ${PARTIES}?kind=Organization*`]: serve(many) });
    await open();
    tab('Organizations');
    expect(await screen.findByText('Org 00')).toBeTruthy();
    expectUi('ui.parties.previous');
    expectUi('ui.parties.next');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Org 50')).toBeTruthy();
    expect(screen.getByText(/^Showing 51–51/)).toBeTruthy();
  });

  it('opens Customers at /customers, out of the Parties tabs, with its own filters', async () => {
    const { calls } = backend();
    await open();
    await screen.findByText('Olga Kuznetsova');
    expect(
      within(screen.getByRole('group', { name: 'Party sections' }))
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Individuals', 'Organizations']);
    await customers();
    expect(window.location.pathname).toBe('/customers');
    expectUi('ui.customers');
    expect(await screen.findByText('Ivan Second')).toBeTruthy();
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumbs' });
    expect(within(crumbs).getByText('Customers')).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    const link = within(nav).getByRole('link', { name: 'Customers' });
    expect(link.getAttribute('aria-current')).toBe('page');
    expect(link.getAttribute('title')).toBe('TMF629 customer');
    expect(
      within(rowOf(/Ivan Petrov/) as HTMLElement)
        .getByRole('link')
        .getAttribute('href'),
    ).toBe(`/billing-accounts/${A1}`);
    expect(
      within(screen.getByRole('group', { name: 'Status' }))
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All', 'Created', 'Active', 'Inactive']);
    fireEvent.change(screen.getByLabelText('Search customers'), { target: { value: 'ivan' } });
    await waitFor(() => expect(calls).toContain(`GET ${SEARCH_CUSTOMERS}?q=ivan&${PAGE}`));
    fireEvent.click(screen.getByRole('button', { name: 'Created' }));
    await waitFor(() =>
      expect(calls).toContain(`GET ${SEARCH_CUSTOMERS}?q=ivan&status=Created&${PAGE}`),
    );
  });

  it('is in the sidebar under Customers and shows a load failure', async () => {
    backend({ [`GET ${PARTIES}?kind=Individual*`]: () => tmfError(503, 'Unavailable') });
    await open();
    expect(await screen.findByText('Failed to load parties')).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Parties' }).getAttribute('href')).toBe(
      '/parties',
    );
  });
});

describe('party and customer dialogs', () => {
  it('creates an individual with status, birth date and contact media', async () => {
    const { fetchMock } = backend({
      [`POST ${PARTY}/individual`]: () => json({ id: PAVEL }, { status: 201 }),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New individual' }));
    expectUi('ui.parties.newIndividual');
    const dialog = await screen.findByRole('dialog', { name: 'New individual' });
    expectUi('ui.parties.newIndividualForm');
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: ' Anna ' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: ' Lee ' } });
    fireEvent.change(within(dialog).getByLabelText('Birth date'), {
      target: { value: '1990-01-02' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'validated' }));
    fireEvent.change(within(dialog).getByLabelText('Email'), {
      target: { value: ' anna@example.org ' },
    });
    fireEvent.change(within(dialog).getByLabelText('Phone'), {
      target: { value: ' +7 900 000-00-00 ' },
    });
    fireEvent.change(within(dialog).getByLabelText('Street address'), {
      target: { value: ' Tverskaya 1 ' },
    });
    fireEvent.change(within(dialog).getByLabelText('City'), { target: { value: ' Moscow ' } });
    fireEvent.change(within(dialog).getByLabelText('Postal code'), {
      target: { value: ' 101000 ' },
    });
    fireEvent.change(within(dialog).getByLabelText('Country'), { target: { value: ' Russia ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('Anna Lee created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${PARTY}/individual`,
        '{"@type":"Individual","givenName":"Anna","familyName":"Lee","status":"validated",' +
          '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",' +
          '"emailAddress":"anna@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",' +
          '"phoneNumber":"+7 900 000-00-00"},{"@type":"GeographicAddressContactMedium",' +
          '"contactType":"postalAddress","street1":"Tverskaya 1","city":"Moscow",' +
          '"postCode":"101000","country":"Russia"}],"birthDate":"1990-01-02T00:00:00+03:00"}',
      ],
    ]);
  });

  it('creates an organization with its own status enum and contact media', async () => {
    const { fetchMock } = backend({
      [`POST ${PARTY}/organization`]: () => json({ id: EMPTY_ORG }, { status: 201 }),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New organization' }));
    expectUi('ui.parties.newOrganization');
    const dialog = await screen.findByRole('dialog', { name: 'New organization' });
    expectUi('ui.parties.newOrganizationForm');
    expect(within(dialog).queryByRole('button', { name: 'deceased' })).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('Organization name'), {
      target: { value: ' New Co ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'closed' }));
    fireEvent.change(within(dialog).getByLabelText('Email'), {
      target: { value: 'office@example.org' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('New Co created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${PARTY}/organization`,
        '{"@type":"Organization","name":"New Co","status":"closed",' +
          '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",' +
          '"emailAddress":"office@example.org"}]}',
      ],
    ]);
  });

  it('edits every supported party document in one merge patch', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/individual/${OLGA}`]: () =>
        json({ ...INDIVIDUALS[1], birthDate: '1990-01-01T21:00:00Z' }),
      [`PATCH ${PARTY}/individual/${OLGA}`]: () => json({}),
    });
    await open();
    fireEvent.click(await screen.findByText('Olga Kuznetsova'));
    const dialog = await screen.findByRole('dialog', { name: 'Olga Kuznetsova' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    expect(within(dialog).getByLabelText('Birth date')).toHaveProperty('value', '1990-01-02');
    fireEvent.change(within(dialog).getByLabelText('Birth date'), {
      target: { value: '1985-04-03' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'validated' }));
    fireEvent.change(within(dialog).getByLabelText('Email'), {
      target: { value: 'olga@example.org' },
    });
    fireEvent.change(within(dialog).getByLabelText('Phone'), { target: { value: '+1 555 0100' } });
    fireEvent.change(within(dialog).getByLabelText('Street address'), {
      target: { value: 'Main 2' },
    });
    fireEvent.change(within(dialog).getByLabelText('City'), { target: { value: 'Boston' } });
    fireEvent.change(within(dialog).getByLabelText('Postal code'), { target: { value: '02108' } });
    fireEvent.change(within(dialog).getByLabelText('Country'), { target: { value: 'US' } });
    fireEvent.change(within(dialog).getByLabelText('Identification type'), {
      target: { value: 'passport' },
    });
    fireEvent.change(within(dialog).getByLabelText('Identification ID'), {
      target: { value: 'P123' },
    });
    fireEvent.change(within(dialog).getByLabelText('External reference ID'), {
      target: { value: 'CRM-9' },
    });
    fireEvent.change(within(dialog).getByLabelText('External reference type'), {
      target: { value: 'crmId' },
    });
    expectUi('ui.parties.contactMedia');
    expectUi('ui.parties.identification');
    expectUi('ui.parties.externalReference');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByText('Olga Kuznetsova saved');
    expect(writes(fetchMock)).toEqual([
      [
        'PATCH',
        `${PARTY}/individual/${OLGA}`,
        '{"birthDate":"1985-04-03T00:00:00+04:00","status":"validated",' +
          '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",' +
          '"emailAddress":"olga@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",' +
          '"phoneNumber":"+1 555 0100"},{"@type":"GeographicAddressContactMedium",' +
          '"contactType":"postalAddress","street1":"Main 2","city":"Boston",' +
          '"postCode":"02108","country":"US"}],"individualIdentification":[' +
          '{"@type":"IndividualIdentification","identificationType":"passport",' +
          '"identificationId":"P123"}],"externalReference":[{"@type":"ExternalIdentifier",' +
          '"externalIdentifierType":"crmId","id":"CRM-9"}]}',
      ],
    ]);
  });

  it('asks for the account PIN only with a contact change and sends it as a header', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/individual/${OLGA}`]: () => json(INDIVIDUALS[1]),
      [`PATCH ${PARTY}/individual/${OLGA}`]: () =>
        tmfCodeError(403, 'pinNotSet', 'the customer has no account PIN yet'),
    });
    await open();
    fireEvent.click(await screen.findByText('Olga Kuznetsova'));
    const dialog = await screen.findByRole('dialog', { name: 'Olga Kuznetsova' });
    await within(dialog).findByText('Given name');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(dialog).getByLabelText('Family name'), {
      target: { value: 'Kuznetsova-Lee' },
    });
    expect(within(dialog).queryByLabelText('Account PIN')).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('City'), { target: { value: 'Boston' } });
    fireEvent.change(within(dialog).getByLabelText('Account PIN'), {
      target: { value: '482913' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await within(dialog).findByText(/has no account PIN yet. Set one/)).toBeTruthy();
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH');
    expect((patch?.[1]?.headers as Record<string, string>)['X-Account-Pin']).toBe('482913');
    expect(String(patch?.[1]?.body)).not.toContain('482913');
  });

  it('shows a customer audit log only when asked for', async () => {
    const audit = `GET /internal/v1/security/audit?customer.id=${C_ORPHAN}&offset=0&limit=25`;
    const { calls } = backend({
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
      [audit]: () =>
        json([
          {
            id: 'a0000001-0000-4000-8000-000000000001',
            at: '2026-10-01T07:00:00Z',
            operator: 'i.petrova',
            role: 'admin',
            customer: { id: C_ORPHAN, href: '' },
            billingAccount: null,
            product: null,
            action: 'contact_change',
            outcome: 'done',
            reason: null,
            verification: 'pin',
            detail: {},
          },
        ]),
    });
    await open();
    await customers();
    fireEvent.click(await screen.findByText('Ivan Second'));
    const dialog = await screen.findByRole('dialog', { name: 'Ivan Second' });
    await within(dialog).findByText('Engaged party');
    expect(calls.some((c) => c.includes('/security/'))).toBe(false);
    fireEvent.click(expectUi('ui.parties.customerAudit'));
    await within(dialog).findByText('Contact change');
    expect(calls).toContain(audit);
    expect(within(dialog).getByText('PIN')).toBeTruthy();
  });

  it('creates a customer from a party with its name and Active status', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/organization/${EMPTY_ORG}`]: () => json(ORGANIZATIONS[1]),
      [`POST ${CUSTOMERS}`]: () => json({ id: C_ORPHAN }, { status: 201 }),
    });
    await open();
    tab('Organizations');
    fireEvent.click(await screen.findByText('Lone Ltd'));
    const partyDialog = await screen.findByRole('dialog', { name: 'Lone Ltd' });
    expect(partyDialog).toBe(expectUi('ui.parties.organization'));
    const newCustomer = within(partyDialog).getByRole('button', { name: 'New customer' });
    expect(newCustomer).toBe(expectUi('ui.parties.newCustomer'));
    fireEvent.click(newCustomer);
    const dialog = await screen.findByRole('dialog', { name: 'New customer' });
    expectUi('ui.parties.newCustomerForm');
    expect((within(dialog).getByLabelText('Customer name') as HTMLInputElement).value).toBe(
      'Lone Ltd',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create customer' }));
    await screen.findByText('Lone Ltd created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        CUSTOMERS,
        '{"@type":"Customer","name":"Lone Ltd","status":"Active",' +
          `"engagedParty":{"id":"${EMPTY_ORG}","@type":"PartyRef","@referredType":"Organization"}}`,
      ],
    ]);
  });

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

    await customers();
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
    await customers();
    fireEvent.click((await screen.findAllByText('Ivan Petrov'))[0] as HTMLElement);
    dialog = await screen.findByRole('dialog', { name: 'Ivan Petrov' });
    // only the account's own Delete is there; the customer's waits for the accounts to go
    expect(within(dialog).getAllByRole('button', { name: 'Delete' })).toHaveLength(1);
    expect(within(dialog).getByRole('button', { name: 'Remove billing accounts first' })).toBe(
      expectUi('ui.parties.removeAccountsFirst'),
    );
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
    await customers();
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

describe('billing accounts of a customer', () => {
  const ACCOUNT_URL = `${ACCOUNTS}/billingAccount/${A1}`;
  const customerDialog = async () => {
    await open();
    await customers();
    fireEvent.click((await screen.findAllByText('Ivan Petrov'))[0] as HTMLElement);
    return screen.findByRole('dialog', { name: 'Ivan Petrov' });
  };

  it('adds a billing account with the body of New subscriber', async () => {
    const { fetchMock } = backend({
      [`POST ${ACCOUNTS}/billingAccount`]: () => json({ id: 'new' }, { status: 201 }),
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
    });
    const dialog = await customerDialog();
    expectUi('ui.parties.addAccount');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add billing account' }));
    const add = await screen.findByRole('dialog', { name: 'Add billing account' });
    expectUi('ui.account.new');
    fireEvent.change(within(add).getByLabelText('Billing cycle day (1-28)'), {
      target: { value: '15' },
    });
    fireEvent.change(within(add).getByLabelText('Credit limit, USD (optional)'), {
      target: { value: '1500.50' },
    });
    fireEvent.click(within(add).getByRole('button', { name: 'Add billing account' }));
    await screen.findByText('Billing account created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACCOUNTS}/billingAccount`,
        '{"@type":"BssBillingAccount","name":"Ivan Petrov main","billCycleDay":15,"currency":"USD",' +
          '"relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef","role":"customer",' +
          `"partyOrPartyRole":{"id":"${C_IVAN}","name":"Ivan Petrov","@type":"PartyRoleRef",` +
          '"@referredType":"Customer"}}],"creditLimit":{"unit":"USD","value":1500.50}}',
      ],
    ]);
  });

  it('adds a prepaid account without cycle day or credit limit', async () => {
    const { fetchMock } = backend({
      [`POST ${ACCOUNTS}/billingAccount`]: () => json({ id: 'new' }, { status: 201 }),
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
    });
    const dialog = await customerDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add billing account' }));
    const add = await screen.findByRole('dialog', { name: 'Add billing account' });
    fireEvent.click(within(add).getByRole('radio', { name: 'Prepaid' }));
    expect(within(add).queryByLabelText('Billing cycle day (1-28)')).toBeNull();
    fireEvent.change(within(add).getByLabelText('Account name'), { target: { value: 'Wallet' } });
    fireEvent.click(within(add).getByRole('button', { name: 'Add billing account' }));
    await screen.findByText('Billing account created');
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${ACCOUNTS}/billingAccount`,
        '{"@type":"BssBillingAccount","name":"Wallet","billCycleDay":1,"currency":"USD",' +
          '"ratingType":"prepaid","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",' +
          `"role":"customer","partyOrPartyRole":{"id":"${C_IVAN}","name":"Ivan Petrov",` +
          '"@type":"PartyRoleRef","@referredType":"Customer"}}]}',
      ],
    ]);
  });

  it('deletes an account in place after a confirm', async () => {
    const { fetchMock } = backend({
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
      [`DELETE ${ACCOUNT_URL}`]: () => new Response(null, { status: 204 }),
    });
    const dialog = await customerDialog();
    expectUi('ui.parties.customerAccounts');
    const remove = within(dialog).getByRole('button', { name: 'Delete' });
    expect(remove).toBe(expectUi('ui.parties.deleteAccount'));
    fireEvent.click(remove);
    expect(writes(fetchMock)).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    await screen.findByText('Ivan main deleted');
    expect(writes(fetchMock)).toEqual([['DELETE', ACCOUNT_URL, null]]);
  });

  it('shows the server 409 on the account row and keeps the dialog', async () => {
    backend({
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
      [`DELETE ${ACCOUNT_URL}`]: () => tmfError(409, 'billingAccount has charges'),
    });
    const dialog = await customerDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('409');
    expect(screen.getByRole('dialog', { name: 'Ivan Petrov' })).toBeTruthy();
    expect(
      within(dialog).getByRole('button', { name: 'Remove billing accounts first' }),
    ).toBeTruthy();
  });

  it('enables Delete customer once no account remains', async () => {
    backend({
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
    });
    await open();
    await customers();
    fireEvent.click(await screen.findByText('Ivan Second'));
    const dialog = await screen.findByRole('dialog', { name: 'Ivan Second' });
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(
      within(dialog).queryByRole('button', { name: 'Remove billing accounts first' }),
    ).toBeNull();
  });
});

/** A TMF Error whose message is the server's own words (its reason is the status phrase). */
const refused = (status: number, message: string) =>
  json(
    { '@type': 'Error', code: String(status), reason: 'Refused', message, status: String(status) },
    { status },
  );

// The cancelled, refused and uncertain paths of the party and customer dialogs (#487): what
// the operator sees and that nothing is sent by a step back.
describe('party and customer dialogs: cancel, refusal and unknown outcome', () => {
  const dialogOf = async (name: string) => {
    fireEvent.click(await screen.findByText(name));
    const dialog = await screen.findByRole('dialog', { name });
    return dialog;
  };

  it('opens an account link from a party row without opening the party', async () => {
    backend({ 'GET /*': pending });
    await open();
    const row = (await screen.findByText('Ivan Petrov')).closest('[role="row"]') as HTMLElement;
    fireEvent.click(within(row).getByRole('link', { name: 'Ivan main' }));
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
    expect(screen.queryByRole('dialog', { name: 'Ivan Petrov' })).toBeNull();
  });

  it('resets a search and a status filter that hide every party', async () => {
    const { calls } = backend();
    await open();
    await screen.findByText('Olga Kuznetsova');
    fireEvent.click(screen.getByRole('button', { name: 'deceased' }));
    fireEvent.change(screen.getByLabelText('Search individuals'), { target: { value: 'zz' } });
    // the search settles and is asked for before Reset is pressed
    await waitFor(() => expect(calls.some((c) => c.includes('q=zz'))).toBe(true));
    await waitFor(() => expect(screen.queryByText('Olga Kuznetsova')).toBeNull());
    fireEvent.click(await screen.findByRole('button', { name: 'Reset filters' }));
    await screen.findByText('Olga Kuznetsova');
    expect(screen.getByLabelText('Search individuals')).toHaveProperty('value', '');
    expect(screen.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
    const last = calls.filter((c) => c.startsWith(`GET ${PARTIES}?kind=Individual`)).at(-1);
    expect(last).not.toMatch(/[?&](q|status)=/);
  });

  it('cancels a new party and a new customer without sending', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/organization/${EMPTY_ORG}`]: () => json(ORGANIZATIONS[1]),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New individual' }));
    const party = await screen.findByRole('dialog', { name: 'New individual' });
    fireEvent.click(within(party).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    tab('Organizations');
    const org = await dialogOf('Lone Ltd');
    fireEvent.click(within(org).getByRole('button', { name: 'New customer' }));
    const created = await screen.findByRole('dialog', { name: 'New customer' });
    fireEvent.click(within(created).getByRole('button', { name: 'Cancel' }));
    // back to the party it came from
    expect(await screen.findByRole('dialog', { name: 'Lone Ltd' })).toBeTruthy();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('says a create with no answer may have happened and Refresh reloads the list', async () => {
    const { fetchMock, calls } = backend({
      [`POST ${PARTY}/individual`]: () => refused(504, 'upstream timed out'),
    });
    await open();
    await screen.findByText('Olga Kuznetsova');
    fireEvent.click(screen.getByRole('button', { name: 'New individual' }));
    const dialog = await screen.findByRole('dialog', { name: 'New individual' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Anna' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'Lee' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect((await within(dialog).findByRole('alert')).textContent).toBe(
      'upstream timed out. Unsure whether it was saved: Refresh to check.',
    );
    // the input stays for a retry
    expect(within(dialog).getByLabelText('Given name')).toHaveProperty('value', 'Anna');
    const lists = calls.filter((c) => c.startsWith(`GET ${PARTIES}?kind=Individual`)).length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Refreshed');
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() =>
      expect(calls.filter((c) => c.startsWith(`GET ${PARTIES}?kind=Individual`))).toHaveLength(
        lists + 1,
      ),
    );
    expect(writes(fetchMock)).toHaveLength(1);
  });

  it('sends the edited customer name and offers Refresh after an unknown outcome', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/organization/${EMPTY_ORG}`]: () => json(ORGANIZATIONS[1]),
      [`POST ${CUSTOMERS}`]: () => refused(502, 'bad gateway'),
    });
    await open();
    tab('Organizations');
    const org = await dialogOf('Lone Ltd');
    fireEvent.click(within(org).getByRole('button', { name: 'New customer' }));
    const dialog = await screen.findByRole('dialog', { name: 'New customer' });
    fireEvent.change(within(dialog).getByLabelText('Customer name'), {
      target: { value: 'Lone Retail' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create customer' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('Refresh to check');
    expect(String(writes(fetchMock)[0]?.[2])).toContain('"name":"Lone Retail"');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Refreshed');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('steps back from a delete and from an edit, dropping the PIN and the refusal', async () => {
    const { fetchMock } = backend({
      [`GET ${PARTY}/individual/${OLGA}`]: () => json(INDIVIDUALS[1]),
      [`PATCH ${PARTY}/individual/${OLGA}`]: () =>
        tmfCodeError(403, 'pinRequired', 'the account PIN is required'),
    });
    await open();
    const dialog = await dialogOf('Olga Kuznetsova');
    await within(dialog).findByText('Given name');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(within(dialog).queryByText('Delete this party?')).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olya' } });
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'o@x.org' } });
    fireEvent.change(within(dialog).getByLabelText('Account PIN'), {
      target: { value: '482913' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await within(dialog).findByRole('alert');
    expect(String(writes(fetchMock)[0]?.[2])).toContain('"givenName":"Olya"');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    // the record again, without the refusal; a new edit starts from it, PIN empty
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(within(dialog).getByText('Olga')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    expect(within(dialog).getByLabelText('Given name')).toHaveProperty('value', 'Olga');
    fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'o@x.org' } });
    expect(within(dialog).getByLabelText('Account PIN')).toHaveProperty('value', '');
  });

  it('steps back from a customer edit and closes Add billing account without sending', async () => {
    const { fetchMock } = backend({
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
      [`PATCH ${CUSTOMERS}/${C_ORPHAN}`]: () => refused(422, 'name is too long'),
    });
    await open();
    await customers();
    const dialog = await dialogOf('Ivan Second');
    await within(dialog).findByText('Engaged party');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit' }));
    fireEvent.change(within(dialog).getByLabelText('Customer name'), {
      target: { value: 'Ivan the Second' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('name is too long');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(within(dialog).getByText('Engaged party')).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Add billing account' }));
    const add = await screen.findByRole('dialog', { name: 'Add billing account' });
    fireEvent.click(within(add).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Add billing account' })).toBeNull();
    expect(writes(fetchMock).map(([method]) => method)).toEqual(['PATCH']);
  });

  it('keeps Delete customer after a refused delete and scrolls to accounts to remove', async () => {
    const scrolled = vi.fn();
    const { fetchMock } = backend({
      [`GET ${CUSTOMERS}/${C_ORPHAN}`]: () => json(CUSTOMER_ROWS[1]),
      [`DELETE ${CUSTOMERS}/${C_ORPHAN}`]: () => refused(409, 'Customer has open orders'),
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
    });
    await open();
    await customers();
    let dialog = await dialogOf('Ivan Second');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('open orders');
    // the confirm closed: Delete is offered again, not a second Yes
    expect(within(dialog).queryByRole('button', { name: 'Yes' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(writes(fetchMock)).toEqual([['DELETE', `${CUSTOMERS}/${C_ORPHAN}`, null]]);
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[1] as HTMLElement);

    fireEvent.click((await screen.findAllByText('Ivan Petrov'))[0] as HTMLElement);
    dialog = await screen.findByRole('dialog', { name: 'Ivan Petrov' });
    await within(dialog).findByText('Engaged party');
    const accounts = expectUi('ui.parties.customerAccounts').parentElement as HTMLElement;
    accounts.scrollIntoView = scrolled;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove billing accounts first' }));
    expect(scrolled).toHaveBeenCalledWith({ block: 'center' });
  });
});

describe('billing accounts of a customer: cancel and unknown outcome', () => {
  const ACCOUNT_URL = `${ACCOUNTS}/billingAccount/${A1}`;

  it('steps back from an account delete and offers Refresh after no answer', async () => {
    const { fetchMock } = backend({
      [`GET ${CUSTOMERS}/${C_IVAN}`]: () => json(CUSTOMER_ROWS[0]),
      [`DELETE ${ACCOUNT_URL}`]: () => refused(500, 'database restarted'),
    });
    await open();
    await customers();
    fireEvent.click((await screen.findAllByText('Ivan Petrov'))[0] as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: 'Ivan Petrov' });
    const rows = expectUi('ui.parties.customerAccounts');
    fireEvent.click(within(rows).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(rows).getByRole('button', { name: 'No' }));
    expect(within(rows).queryByText('Delete this billing account?')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
    fireEvent.click(within(rows).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(rows).getByRole('button', { name: 'Yes' }));
    expect((await within(rows).findByRole('alert')).textContent).toBe(
      'database restarted. Unsure whether it was saved: Refresh to check.',
    );
    fireEvent.click(within(rows).getByRole('button', { name: 'Refresh' }));
    await screen.findByText('Refreshed');
    expect(screen.queryByRole('dialog', { name: 'Ivan Petrov' })).toBeNull();
    expect(dialog.isConnected).toBe(false);
    expect(writes(fetchMock)).toEqual([['DELETE', ACCOUNT_URL, null]]);
  });
});
