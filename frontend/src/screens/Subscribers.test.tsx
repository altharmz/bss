import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
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
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';
const PRODUCTS = '/tmf-api/productInventory/v5/product';

const balance = (value: number) => [
  { balanceType: 'currentBalance', amount: { unit: 'USD', value }, '@type': 'AccountBalance' },
];

const IVAN = { id: 'aaaa1111-0000-4000-8000-000000000001', name: 'Ivan Petrov' };
const ANNA = { id: 'bbbb2222-0000-4000-8000-000000000002', name: 'Anna Smirnova' };
const ROMASHKA = { id: 'cccc3333-0000-4000-8000-000000000003', name: 'Romashka LLC' };
const A1 = '11111111-0000-4000-8000-0000000000a1';
const A2 = '22222222-0000-4000-8000-0000000000a2';
const A3 = '33333333-0000-4000-8000-0000000000a3';
const A4 = '44444444-0000-4000-8000-0000000000a4';

const ROWS = [
  accountBody(A1, IVAN, { accountBalance: balance(1250.4), billCycleDay: 5 }),
  accountBody(A2, ANNA, { state: 'Suspended', accountBalance: balance(-116.13) }),
  accountBody(A3, ROMASHKA, { name: 'Romashka HQ', billCycleDay: 15 }),
  accountBody(A4, ROMASHKA, { name: 'Romashka branch', state: 'Closed', billCycleDay: 15 }),
];
const ACTIVE_PRODUCTS = [
  { id: 'p1', billingAccount: { id: A1 } },
  { id: 'p2', billingAccount: { id: A1 } },
  { id: 'p3', billingAccount: { id: A3 } },
];

function backend(
  accounts: Handler = () => json(ROWS, { total: ROWS.length }),
  extra: Record<string, Handler> = {},
) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    ...extra,
    [NAV_RUNS]: noRuns,
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [`GET ${ACCOUNTS}?limit=1&fields=id`]: () => json([], { total: ROWS.length }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 3 }),
    [`GET ${ACCOUNTS}?offset=0&limit=100`]: accounts,
    [`GET ${PRODUCTS}?status=active&fields=billingAccount&offset=0&limit=100`]: () =>
      json(ACTIVE_PRODUCTS, { total: ACTIVE_PRODUCTS.length }),
  });
}

async function open() {
  window.history.replaceState(null, '', '/subscribers');
  render(<App />);
  return screen.findByRole('heading', { name: 'Subscribers' });
}

const dataRows = () =>
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 7);
const rowOf = (name: RegExp) => dataRows().find((row) => name.test(row.textContent ?? ''));

describe('subscribers list', () => {
  it('shows one row per billing account with balance, products and cycle day', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    expectUi('ui.subscribers.list');
    expect(dataRows()).toHaveLength(4);

    const ivan = rowOf(/Ivan Petrov/) as HTMLElement;
    const cells = within(ivan).getAllByRole('cell');
    expect(cells[1]?.textContent).toBe('BA-11111111');
    expect(within(ivan).getByRole('button', { name: `Copy ${A1}` })).toBeTruthy();
    expect(cells[2]?.textContent).toBe('Active');
    expect(cells[6]?.textContent).toBe('1,250.40');
    expect(cells[4]?.textContent).toBe('2');
    expect(cells[5]?.textContent).toBe('5');

    const anna = rowOf(/Anna Smirnova/) as HTMLElement;
    expect(within(anna).getByText('Suspended').className).toContain('pill-paused');
    const negative = within(anna).getByText('−116.13');
    expect(negative.parentElement?.className).toContain('negative');
    expect(within(anna).getAllByRole('cell')[4]?.firstElementChild?.className).toBe('bss-zero');

    // a customer with two accounts is two rows, told apart by the account name
    expect(rowOf(/Romashka HQ/)).toBeTruthy();
    expect(rowOf(/Romashka branch/)).toBeTruthy();
    expect(screen.getByText('Showing 4 of 4')).toBeTruthy();
    expect(screen.getByText('Amounts in billing account currency')).toBeTruthy();
    expect(screen.getByText('Negative balance').textContent).toContain('1');
  });

  it('filters by the real account state, with counts', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const group = screen.getByRole('group', { name: 'Account state' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All4', 'Active2', 'Suspended1', 'Closed1']);
    fireEvent.click(within(group).getByRole('button', { name: /^Suspended/ }));
    expect(dataRows().map((r) => within(r).getAllByRole('cell')[0]?.textContent)).toEqual([
      'Anna Smirnova',
    ]);
    expect(screen.getByText('Showing 1 of 4')).toBeTruthy();
  });

  it('shows the account mode and filters by it, with counts', async () => {
    const rows = [
      ...ROWS,
      accountBody(
        '55555555-0000-4000-8000-0000000000a5',
        { id: 'dddd4444-0000-4000-8000-000000000004', name: 'Dmitri Sokolov' },
        { ratingType: 'prepaid' },
      ),
    ];
    backend(() => json(rows, { total: rows.length }));
    await open();
    await screen.findByText('Ivan Petrov');
    expect(within(rowOf(/Dmitri Sokolov/) as HTMLElement).getByText('Prepaid')).toBeTruthy();
    expectUi('ui.subscribers.prepaid');
    const group = screen.getByRole('group', { name: 'Account mode' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All5', 'Postpaid4', 'Prepaid1']);
    fireEvent.click(within(group).getByRole('button', { name: /^Prepaid/ }));
    expect(dataRows()).toHaveLength(1);
    expect(rowOf(/Dmitri Sokolov/)?.textContent).toContain('Prepaid');
    fireEvent.click(within(group).getByRole('button', { name: /^Postpaid/ }));
    expect(dataRows()).toHaveLength(4);
    expect(screen.getByText('Showing 4 of 5')).toBeTruthy();
  });

  it('searches names and subscriber numbers, and resets when nothing matches', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const search = screen.getByRole('textbox', { name: 'Search subscribers' });
    fireEvent.change(search, { target: { value: 'smirn' } });
    expect(dataRows()).toHaveLength(1);
    fireEvent.change(search, { target: { value: 'aaaa 11' } }); // spaces ignored
    expect(rowOf(/Ivan Petrov/)).toBeTruthy();
    expect(dataRows()).toHaveLength(1);
    fireEvent.change(search, { target: { value: 'nobody' } });
    expect(screen.getByText('Nothing matches “nobody”')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset filters' })[0] as HTMLElement);
    expect(dataRows()).toHaveLength(4);
    expect((search as HTMLInputElement).value).toBe('');
  });

  it('flags only balances strictly below the credit limit', async () => {
    const rows = [
      accountBody(A1, IVAN, {
        creditLimit: { unit: 'EUR', value: 100 },
        accountBalance: balance(-100),
      }),
      accountBody(A2, ANNA, {
        creditLimit: { unit: 'EUR', value: 100 },
        accountBalance: balance(-100.01),
      }),
      accountBody(A3, ROMASHKA, { accountBalance: balance(-500) }),
    ];
    backend(() => json(rows, { total: rows.length }));
    await open();
    await screen.findByText('Ivan Petrov');
    expect(within(rowOf(/Ivan Petrov/) as HTMLElement).queryByText('Over limit')).toBeNull();
    expect(within(rowOf(/Anna Smirnova/) as HTMLElement).getByText('Over limit')).toBeTruthy();
    expect(within(rowOf(/Romashka/) as HTMLElement).queryByText('Over limit')).toBeNull();
  });

  it('focuses the search with slash except while editing a field', async () => {
    backend();
    await open();
    const search = screen.getByPlaceholderText('Name, BA id or subscriber no.');
    fireEvent.keyDown(document.body, { key: '/' });
    expect(document.activeElement).toBe(search);
    const input = document.createElement('input');
    document.body.append(input);
    input.focus();
    fireEvent.keyDown(input, { key: '/' });
    expect(document.activeElement).toBe(input);
    input.remove();
  });

  it('shows the filtered header count and query with active filters in no results', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const heading = screen.getByRole('heading', { name: 'Subscribers' })
      .parentElement as HTMLElement;
    expect(heading.querySelector('.count')?.textContent).toBe('4');
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account state' })).getByRole('button', {
        name: /^Suspended/,
      }),
    );
    expect(heading.querySelector('.count')?.textContent).toBe('1');
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account mode' })).getByRole('button', {
        name: /^Prepaid/,
      }),
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Search subscribers' }), {
      target: { value: 'nobody' },
    });
    expect(heading.querySelector('.count')?.textContent).toBe('0');
    expect(screen.getByText('Nothing matches “nobody”')).toBeTruthy();
    expect(screen.getByText('with Status')).toHaveProperty('textContent', 'with Status Suspended');
    expect(screen.getByText('with Mode')).toHaveProperty('textContent', 'with Mode Prepaid');
  });

  it('opens the card on a row click', async () => {
    backend(undefined, {
      // the card's own load: not this test's business
      [`GET ${ACCOUNTS}/${A1}`]: pending,
    });
    await open();
    await screen.findByText('Ivan Petrov');
    fireEvent.click(rowOf(/Ivan Petrov/) as HTMLElement);
    expect(window.location.pathname).toBe(`/subscribers/${A1}`);
  });

  it('shows the failing call, and the empty state', async () => {
    backend(() => tmfError(503, 'Service Unavailable'));
    await open();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load subscribers')).toBeTruthy();
    expect(
      within(alert).getByText(`GET ${ACCOUNTS}?offset=0&limit=100 → 503 Service Unavailable`),
    ).toBeTruthy();
  });

  it('explains an empty system', async () => {
    backend(() => json([], { total: 0 }));
    await open();
    expect(await screen.findByText('No subscribers yet')).toBeTruthy();
    expect(screen.getByText('Create one with New subscriber.')).toBeTruthy();
  });
});

// The bodies asserted here are replayed against the real API by tests/test_ui_requests.py.
const PARTY = '/tmf-api/partyManagement/v5';
const CUSTOMERS = '/tmf-api/customer/v5/customer';
const NEW_PARTY = 'dddd4444-0000-4000-8000-000000000004';
const NEW_CUSTOMER = 'eeee5555-0000-4000-8000-000000000005';
const NEW_ACCOUNT = 'ffff6666-0000-4000-8000-0000000000a6';

function bodies(fetchMock: ReturnType<typeof stubFetch>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url, init]) => [String(url), String(init?.body)]);
}

describe('new subscriber', () => {
  it('keeps the created account when the optional initial-product step cannot load', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: () => tmfError(503, 'Unavailable'),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New subscriber' }));
    const dialog = await screen.findByRole('dialog', { name: 'New subscriber' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create subscriber' }));
    const next = await screen.findByRole('dialog', { name: 'Subscriber created' });
    expect(await within(next).findByText('Failed to load the created account')).toBeTruthy();
    fireEvent.click(within(next).getByText('Retry'));
    expect(await within(next).findByText('Failed to load the created account')).toBeTruthy();
    expect(bodies(fetchMock)).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Create subscriber' })).toBeNull();
    fireEvent.click(within(next).getByText('Open subscriber'));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Olga K created');
    expect(window.location.pathname).toBe(`/subscribers/${NEW_ACCOUNT}`);
    expect(bodies(fetchMock)).toHaveLength(3);
  });

  it('creates the party, the customer and the account, then opens the card', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: pending, // the card it opens
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New subscriber' }));
    const dialog = await screen.findByRole('dialog', { name: 'New subscriber' });
    expectUi('ui.subscribers.new');
    const create = within(dialog).getByRole('button', { name: 'Create subscriber' });
    expect((create as HTMLButtonElement).disabled).toBe(true); // no name yet
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), {
      target: { value: ' Kuznetsova ' },
    });
    fireEvent.change(within(dialog).getByLabelText('Billing cycle day (1-28)'), {
      target: { value: '29' },
    });
    expect(within(dialog).getByText('The cycle day is 1 to 28.')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Billing cycle day (1-28)'), {
      target: { value: '15' },
    });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (optional)'), {
      target: { value: '1,500.5' },
    });
    fireEvent.click(create);

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Olga Kuznetsova created',
    );
    expect(window.location.pathname).toBe(`/subscribers/${NEW_ACCOUNT}`);
    expect(bodies(fetchMock)).toEqual([
      [
        `${PARTY}/individual`,
        '{"@type":"Individual","givenName":"Olga","familyName":"Kuznetsova"}',
      ],
      [
        CUSTOMERS,
        '{"@type":"Customer","name":"Olga Kuznetsova","status":"Active",' +
          `"engagedParty":{"id":"${NEW_PARTY}","@type":"PartyRef","@referredType":"Individual"}}`,
      ],
      [
        ACCOUNTS,
        '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,' +
          '"currency":"USD","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",' +
          `"role":"customer","partyOrPartyRole":{"id":"${NEW_CUSTOMER}",` +
          '"name":"Olga Kuznetsova","@type":"PartyRoleRef","@referredType":"Customer"}}],' +
          '"creditLimit":{"unit":"USD","value":1500.50}}',
      ],
    ]);
  });

  it('creates a prepaid account: the mode is sent, the credit limit is not offered', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: pending,
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New subscriber' }));
    const dialog = await screen.findByRole('dialog', { name: 'New subscriber' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (optional)'), {
      target: { value: '10' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepaid' }));
    expect(within(dialog).queryByLabelText('Credit limit, USD (optional)')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create subscriber' }));
    await screen.findByRole('status');
    const account = bodies(fetchMock)[2];
    expect(account?.[1]).toBe(
      '{"@type":"BssBillingAccount","name":"Olga K main","billCycleDay":1,"currency":"USD",' +
        '"ratingType":"prepaid","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",' +
        `"role":"customer","partyOrPartyRole":{"id":"${NEW_CUSTOMER}",` +
        '"name":"Olga K","@type":"PartyRoleRef","@referredType":"Customer"}}]}',
    );
  });

  it('creates an organization without a credit limit', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/organization`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: pending,
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New subscriber' }));
    const dialog = await screen.findByRole('dialog', { name: 'New subscriber' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Organization' }));
    fireEvent.change(within(dialog).getByLabelText('Organization name'), {
      target: { value: 'Vasilek LLC' },
    });
    fireEvent.change(within(dialog).getByLabelText('Account name'), { target: { value: 'HQ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create subscriber' }));
    await screen.findByRole('status');
    const [party, , account] = bodies(fetchMock);
    expect(party).toEqual([
      `${PARTY}/organization`,
      '{"@type":"Organization","name":"Vasilek LLC"}',
    ]);
    expect(JSON.parse(account?.[1] ?? '{}')).toMatchObject({ name: 'HQ', billCycleDay: 1 });
    expect(account?.[1]).not.toContain('creditLimit');
  });

  it('says what was already created when a later step fails', async () => {
    backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => tmfError(422, 'Unprocessable Entity'),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New subscriber' }));
    const dialog = await screen.findByRole('dialog', { name: 'New subscriber' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create subscriber' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Creating the billing account failed');
    expect(alert.textContent).toContain('Already created: individual Olga K, customer Olga K.');
    expect(window.location.pathname).toBe('/subscribers');
  });
});
