import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
import { mockWidth } from '../test/matchMedia';

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
const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';
const ACTIVE_PRODUCTS = [
  { id: 'p1', billingAccount: { id: A1 } },
  { id: 'p2', billingAccount: { id: A1 } },
  { id: 'p3', billingAccount: { id: A3 } },
];
/** The live numbers the server lists per account (#456): up to 3, and their count. */
const NUMBERS: Record<string, { msisdns: string[]; msisdnCount: number }> = {
  [A1]: { msisdns: ['12025550101', '12025550102', '12025550103'], msisdnCount: 4 },
  [A3]: { msisdns: ['12025550301'], msisdnCount: 1 },
};

type Body = ReturnType<typeof accountBody>;

/** A row of GET /internal/v1/subscriber, made from an account body. */
const customerOf = (body: Body) => {
  const party = body.relatedParty[0];
  if (!party) throw new Error('an account body without its customer');
  return party.partyOrPartyRole;
};

function subscriberRow(body: Body, all: Body[]) {
  const customer = customerOf(body);
  const amount = body.accountBalance[0]?.amount;
  return {
    id: body.id,
    name: body.name,
    state: body.state,
    ratingType: body.ratingType ?? 'postpaid',
    customer: { id: customer.id, name: customer.name },
    currency: body.currency,
    balance: amount,
    creditLimit: (body as { creditLimit?: unknown }).creditLimit ?? null,
    cycleDay: body.billCycleDay,
    financialAccount: null,
    activeProducts: ACTIVE_PRODUCTS.filter((p) => p.billingAccount.id === body.id).length,
    customerAccounts: all.filter((other) => customerOf(other).id === customer.id).length,
    ...(NUMBERS[body.id] ?? { msisdns: [], msisdnCount: 0 }),
  };
}

/** The server's list (#314): prefix search, filters and paging over `rows`. */
function subscribers(rows: Body[]): Handler {
  return (url) => {
    const query = new URL(url ?? '', 'http://test').searchParams;
    const q = (query.get('q') ?? '').toLowerCase();
    const hex = q.replace(/^ba-/, '');
    const offset = Number(query.get('offset') ?? 0);
    const limit = Number(query.get('limit') ?? 20);
    const found = rows
      .map((body) => subscriberRow(body, rows))
      .filter(
        (row) =>
          (!q ||
            row.customer.name.toLowerCase().startsWith(q) ||
            row.name.toLowerCase().startsWith(q) ||
            (/^[0-9a-f]{1,8}$/.test(hex) &&
              (row.id.startsWith(hex) || row.customer.id.startsWith(hex)))) &&
          (!query.get('state') || row.state === query.get('state')) &&
          (!query.get('ratingType') || row.ratingType === query.get('ratingType')) &&
          // the stub's accounts without a service address: the prepaid ones
          (query.get('serviceAddress') !== 'missing' || row.ratingType === 'prepaid'),
      );
    return json(found.slice(offset, offset + limit), {
      more: found.length > offset + limit,
    });
  };
}

function backend(list: Handler = subscribers(ROWS), extra: Record<string, Handler> = {}) {
  return stubFetch({
    ...extra,
    [NAV_RUNS]: noRuns,
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    'GET /internal/v1/subscriber?*': list,
  });
}

async function open() {
  window.history.replaceState(null, '', '/billing-accounts');
  render(<App />);
  return screen.findByRole('heading', { name: 'Billing accounts' });
}

const dataRows = () =>
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 8);
const rowOf = (name: RegExp) => dataRows().find((row) => name.test(row.textContent ?? ''));

const LIST = '/internal/v1/subscriber';
const requested = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(LIST));
const names = () => dataRows().map((r) => within(r).getAllByRole('cell')[0]?.textContent);

describe('subscribers list', () => {
  it('names a shared loaded currency in the header and every amount', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    expect(screen.getByRole('columnheader', { name: 'Balance USD' })).toBeTruthy();
    for (const row of dataRows())
      expect(within(row).getAllByRole('cell')[7]?.textContent).toContain('USD');
  });

  // TEST-CHANGE (#314): the search runs on the server, so the header follows the page shown.
  it('omits the currency in a mixed header and names it again for a single-currency page', async () => {
    const rows = ROWS.map((row, index) => ({ ...row, currency: index === 0 ? 'EUR' : 'USD' }));
    backend(subscribers(rows));
    await open();
    await screen.findByText('Ivan Petrov');
    expect(screen.getByRole('columnheader', { name: 'Balance' })).toBeTruthy();
    expect(within(rowOf(/Ivan Petrov/) as HTMLElement).getAllByRole('cell')[7]?.textContent).toBe(
      '1,250.40EUR',
    );
    expect(
      within(rowOf(/Anna Smirnova/) as HTMLElement).getAllByRole('cell')[7]?.textContent,
    ).toContain('USD');
    fireEvent.change(screen.getByRole('textbox', { name: 'Search billing accounts' }), {
      target: { value: 'Ivan' },
    });
    expect(await screen.findByRole('columnheader', { name: 'Balance EUR' })).toBeTruthy();
  });

  // TEST-CHANGE (#314): no counts (#312); the footer names the rows shown, not a total.
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
    expect(cells[3]?.textContent).toBe('Active');
    expect(cells[7]?.textContent).toBe('1,250.40USD');
    expect(cells[5]?.textContent).toBe('2');
    expect(cells[6]?.textContent).toBe('5');

    const anna = rowOf(/Anna Smirnova/) as HTMLElement;
    expect(within(anna).getByText('Suspended').className).toContain('pill-paused');
    const negative = within(anna).getByText('−116.13');
    expect(negative.parentElement?.className).toContain('negative');
    expect(within(anna).getAllByRole('cell')[5]?.firstElementChild?.className).toBe('bss-zero');

    // a customer with two accounts is two rows, told apart by the account name
    expect(rowOf(/Romashka HQ/)).toBeTruthy();
    expect(rowOf(/Romashka branch/)).toBeTruthy();
    expect(within(rowOf(/Ivan Petrov/) as HTMLElement).queryByText(/·/)).toBeNull();
    expect(screen.getByText('Showing 1–4 · amounts in billing account currency')).toBeTruthy();
    expect(screen.queryByText('Negative balance')).toBeNull();
  });

  it('shows the oldest line number with +N, the first three in a tooltip, and — without one (#456)', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    expect(screen.getByRole('columnheader', { name: 'Number' })).toBeTruthy();

    const ivan = within(rowOf(/Ivan Petrov/) as HTMLElement).getAllByRole('cell')[2];
    expect(ivan?.textContent).toBe('12025550101 +3');
    const title = (cell: HTMLElement | undefined) =>
      cell?.querySelector('[data-ui="ui.subscribers.number"]')?.getAttribute('title');
    expect(title(ivan)).toBe('12025550101\n12025550102\n12025550103\n+1 more');
    expectUi('ui.subscribers.number');

    const hq = within(rowOf(/Romashka HQ/) as HTMLElement).getAllByRole('cell')[2];
    expect(hq?.textContent).toBe('12025550301');
    expect(title(hq)).toBeNull();

    const anna = within(rowOf(/Anna Smirnova/) as HTMLElement).getAllByRole('cell')[2];
    expect(anna?.textContent).toBe('—');
  });

  // TEST-CHANGE (#314): the state filter is a server query and shows no counts (#312).
  it('filters by the real account state on the server, without counts', async () => {
    const { fetchMock } = backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const group = screen.getByRole('group', { name: 'Account state' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All', 'Active', 'Suspended', 'Closed']);
    fireEvent.click(within(group).getByRole('button', { name: 'Suspended' }));
    await screen.findByText('Showing 1–1 · amounts in billing account currency');
    expect(names()).toEqual(['Anna Smirnova']);
    expect(requested(fetchMock)).toContain(`${LIST}?state=Suspended&offset=0&limit=50`);
  });

  // TEST-CHANGE (#314): the mode filter is a server query and shows no counts (#312).
  it('shows the account mode and filters by it on the server, without counts', async () => {
    const rows = [
      ...ROWS,
      accountBody(
        '55555555-0000-4000-8000-0000000000a5',
        { id: 'dddd4444-0000-4000-8000-000000000004', name: 'Dmitri Sokolov' },
        { ratingType: 'prepaid' },
      ),
    ];
    const { fetchMock } = backend(subscribers(rows));
    await open();
    await screen.findByText('Ivan Petrov');
    expect(within(rowOf(/Dmitri Sokolov/) as HTMLElement).getByText('Prepaid')).toBeTruthy();
    expectUi('ui.subscribers.prepaid');
    const group = screen.getByRole('group', { name: 'Account mode' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['All', 'Postpaid', 'Prepaid']);
    fireEvent.click(within(group).getByRole('button', { name: 'Prepaid' }));
    await screen.findByText('Showing 1–1 · amounts in billing account currency');
    expect(rowOf(/Dmitri Sokolov/)?.textContent).toContain('Prepaid');
    fireEvent.click(within(group).getByRole('button', { name: 'Postpaid' }));
    await screen.findByText('Showing 1–4 · amounts in billing account currency');
    expect(requested(fetchMock)).toContain(`${LIST}?ratingType=postpaid&offset=0&limit=50`);
  });

  it('lists the prepaid accounts with no service address with its chip (#207)', async () => {
    const rows = [
      ...ROWS,
      accountBody(
        '55555555-0000-4000-8000-0000000000a5',
        { id: 'dddd4444-0000-4000-8000-000000000004', name: 'Dmitri Sokolov' },
        { ratingType: 'prepaid' },
      ),
    ];
    const { fetchMock } = backend(subscribers(rows));
    await open();
    await screen.findByText('Ivan Petrov');
    const chip = expectUi('ui.subscribers.noServiceAddress');
    expect(chip.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(chip);
    await screen.findByText('Showing 1–1 · amounts in billing account currency');
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(names()).toEqual(['Dmitri Sokolov']);
    expect(requested(fetchMock)).toContain(`${LIST}?serviceAddress=missing&offset=0&limit=50`);
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    await screen.findByText('Showing 1–5 · amounts in billing account currency');
    expect(chip.getAttribute('aria-pressed')).toBe('false');
  });

  // TEST-CHANGE (#314): the search is a prefix search on the server (decided 2026-11-10).
  it('searches by prefix on the server, and resets when nothing matches', async () => {
    const { fetchMock } = backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const search = screen.getByRole('textbox', { name: 'Search billing accounts' });
    fireEvent.change(search, { target: { value: 'anna' } });
    await screen.findByText('Showing 1–1 · amounts in billing account currency');
    expect(names()).toEqual(['Anna Smirnova']);
    expect(requested(fetchMock)).toContain(`${LIST}?q=anna&offset=0&limit=50`);
    fireEvent.change(search, { target: { value: 'BA-11111111' } }); // a short account id
    await screen.findByText('Ivan Petrov');
    expect(names()).toEqual(['Ivan Petrov']);
    fireEvent.change(search, { target: { value: 'nobody' } });
    expect(await screen.findByText('Nothing matches “nobody”')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Reset filters' })[0] as HTMLElement);
    await screen.findByText('Showing 1–4 · amounts in billing account currency');
    expect((search as HTMLInputElement).value).toBe('');
  });

  it('waits for the typing to settle before it asks the server', async () => {
    const { fetchMock } = backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const search = screen.getByRole('textbox', { name: 'Search billing accounts' });
    for (const value of ['r', 'ro', 'rom']) fireEvent.change(search, { target: { value } });
    await screen.findByText('Showing 1–2 · amounts in billing account currency');
    expect(requested(fetchMock).filter((url) => url.includes('q='))).toEqual([
      `${LIST}?q=rom&offset=0&limit=50`,
    ]);
  });

  it('pages with Next and Previous, and new filters start at the first page', async () => {
    const many = Array.from({ length: 52 }, (_, i) =>
      accountBody(
        `${String(i).padStart(8, '0')}-0000-4000-8000-000000000000`,
        { id: `${String(i).padStart(8, '0')}-1111-4000-8000-000000000000`, name: `Client ${i}` },
        {},
      ),
    );
    const { fetchMock } = backend(subscribers(many));
    await open();
    await screen.findByText('Client 0');
    expect(dataRows()).toHaveLength(50);
    expectUi('ui.subscribers.previous');
    expectUi('ui.subscribers.next');
    const previous = screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement;
    expect(previous.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Showing 51–52 · amounts in billing account currency');
    expect(names()).toEqual(['Client 50', 'Client 51']);
    expect(requested(fetchMock)).toContain(`${LIST}?offset=50&limit=50`);
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await screen.findByText('Showing 1–50 · amounts in billing account currency');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Showing 51–52 · amounts in billing account currency');
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account state' })).getByRole('button', {
        name: 'Active',
      }),
    );
    await screen.findByText('Showing 1–50 · amounts in billing account currency');
    expect(requested(fetchMock)).toContain(`${LIST}?state=Active&offset=0&limit=50`);
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
    backend(subscribers(rows));
    await open();
    await screen.findByText('Ivan Petrov');
    expect(within(rowOf(/Ivan Petrov/) as HTMLElement).queryByText('Over limit')).toBeNull();
    expect(within(rowOf(/Anna Smirnova/) as HTMLElement).getByText('Over limit')).toBeTruthy();
    expect(within(rowOf(/Romashka/) as HTMLElement).queryByText('Over limit')).toBeNull();
  });

  it('focuses the search with slash except while editing a field', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov'); // the list has loaded: the search input is final
    const search = screen.getByRole('textbox', { name: 'Search billing accounts' });
    fireEvent.keyDown(document.body, { key: '/' });
    expect(document.activeElement).toBe(search);
    const input = document.createElement('input');
    document.body.append(input);
    input.focus();
    fireEvent.keyDown(input, { key: '/' });
    expect(document.activeElement).toBe(input);
    input.remove();
  });

  // TEST-CHANGE (#314): the header shows no count (#312).
  it('shows no header count, and the query with active filters in no results', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const heading = screen.getByRole('heading', { name: 'Billing accounts' })
      .parentElement as HTMLElement;
    expect(heading.querySelector('.count')).toBeNull();
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account state' })).getByRole('button', {
        name: 'Suspended',
      }),
    );
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Account mode' })).getByRole('button', {
        name: 'Prepaid',
      }),
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Search billing accounts' }), {
      target: { value: 'nobody' },
    });
    expect(await screen.findByText('Nothing matches “nobody”')).toBeTruthy();
    expect(heading.querySelector('.count')).toBeNull();
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
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
  });

  it('shows the failing call, and the empty state', async () => {
    backend(() => tmfError(503, 'Service Unavailable'));
    await open();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load billing accounts')).toBeTruthy();
    expect(
      within(alert).getByText(`GET ${LIST}?offset=0&limit=50 → 503 Service Unavailable`),
    ).toBeTruthy();
  });

  it('explains an empty system', async () => {
    backend(() => json([]));
    await open();
    expect(await screen.findByText('No billing accounts yet')).toBeTruthy();
    expect(screen.getByText('Create one with New billing account.')).toBeTruthy();
  });
});

// The bodies asserted here are replayed against the real API by tests/internal/test_ui_requests.py.
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
  it('explains when the initial product asks for a subscriber number', async () => {
    backend();
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    expect(
      await screen.findByText(
        'After creating the account, add a product. Offerings that need a subscriber number ask for one.',
      ),
    ).toBeTruthy();
  });

  it('keeps the created account when the optional initial-product step cannot load', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: () => tmfError(503, 'Unavailable'),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
    const next = await screen.findByRole('dialog', { name: 'Billing account created' });
    expect(await within(next).findByText('Failed to load the created account')).toBeTruthy();
    fireEvent.click(within(next).getByText('Retry'));
    expect(await within(next).findByText('Failed to load the created account')).toBeTruthy();
    expect(bodies(fetchMock)).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Create billing account' })).toBeNull();
    fireEvent.click(within(next).getByText('Open billing account'));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Olga K created');
    expect(window.location.pathname).toBe(`/billing-accounts/${NEW_ACCOUNT}`);
    expect(bodies(fetchMock)).toHaveLength(3);
  });

  // #499: Cancel sends nothing; the created account opens Add product for its initial
  // product, and closing it opens the card
  it('cancels a new billing account without a request', async () => {
    const { fetchMock } = backend();
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(bodies(fetchMock)).toEqual([]);
  });

  it('opens Add product for the initial product, and its Cancel opens the card', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: () =>
        json(accountBody(NEW_ACCOUNT, { id: NEW_CUSTOMER, name: 'Olga K' })),
      'GET /*': pending,
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
    const add = await screen.findByRole('dialog', { name: 'Add product' });
    expect(bodies(fetchMock)).toHaveLength(3);
    fireEvent.click(within(add).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(window.location.pathname).toBe(`/billing-accounts/${NEW_ACCOUNT}`));
    expect(await screen.findByText('Olga K created')).toBeTruthy();
    expect(bodies(fetchMock)).toHaveLength(3);
  });

  it('creates a subscriber with email and phone contact media', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => json({ id: NEW_ACCOUNT }, { status: 201 }),
      [`GET ${ACCOUNTS}/${NEW_ACCOUNT}`]: pending,
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.change(within(dialog).getByLabelText('Email'), {
      target: { value: ' olga@example.org ' },
    });
    fireEvent.change(within(dialog).getByLabelText('Phone'), {
      target: { value: ' +1 555 0100 ' },
    });
    expectUi('ui.subscribers.contactMedia');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
    await screen.findByRole('status');
    expect(bodies(fetchMock)[0]).toEqual([
      `${PARTY}/individual`,
      '{"@type":"Individual","givenName":"Olga","familyName":"K",' +
        '"contactMedium":[{"@type":"EmailContactMedium","contactType":"email",' +
        '"emailAddress":"olga@example.org"},{"@type":"PhoneContactMedium","contactType":"phone",' +
        '"phoneNumber":"+1 555 0100"}]}',
    ]);
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
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    expectUi('ui.subscribers.new');
    const create = within(dialog).getByRole('button', { name: 'Create billing account' });
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
    expect(window.location.pathname).toBe(`/billing-accounts/${NEW_ACCOUNT}`);
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
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (optional)'), {
      target: { value: '10' },
    });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Prepaid' }));
    expect(within(dialog).queryByLabelText('Credit limit, USD (optional)')).toBeNull();
    expect(within(dialog).queryByLabelText('Billing cycle day (1-28)')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Organization' }));
    fireEvent.change(within(dialog).getByLabelText('Organization name'), {
      target: { value: 'Vasilek LLC' },
    });
    fireEvent.change(within(dialog).getByLabelText('Account name'), { target: { value: 'HQ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
    await screen.findByRole('status');
    const [party, , account] = bodies(fetchMock);
    expect(party).toEqual([
      `${PARTY}/organization`,
      '{"@type":"Organization","name":"Vasilek LLC"}',
    ]);
    expect(JSON.parse(account?.[1] ?? '{}')).toMatchObject({ name: 'HQ', billCycleDay: 1 });
    expect(account?.[1]).not.toContain('creditLimit');
  });

  const fillOlga = async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'New billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'New billing account' });
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olga' } });
    fireEvent.change(within(dialog).getByLabelText('Family name'), { target: { value: 'K' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create billing account' }));
    return dialog;
  };
  const deletes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'DELETE')
      .map(([url, init]) => [String(url), init?.body ?? null]);
  const PARTY_URL = `${PARTY}/individual/${NEW_PARTY}`;
  const CUSTOMER_URL = `${CUSTOMERS}/${NEW_CUSTOMER}`;

  it('deletes the customer, then the party, when the account step fails', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => tmfError(422, 'Unprocessable Entity'),
      [`DELETE ${CUSTOMER_URL}`]: () => new Response(null, { status: 204 }),
      [`DELETE ${PARTY_URL}`]: () => new Response(null, { status: 204 }),
    });
    const dialog = await fillOlga();
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Creating the billing account failed');
    expect(alert.textContent).not.toContain('Could not remove');
    expect(deletes(fetchMock)).toEqual([
      [CUSTOMER_URL, null],
      [PARTY_URL, null],
    ]);
    expect(window.location.pathname).toBe('/billing-accounts');
  });

  it('deletes the party when the customer step fails', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => tmfError(422, 'Unprocessable Entity'),
      [`DELETE ${PARTY_URL}`]: () => new Response(null, { status: 204 }),
    });
    const dialog = await fillOlga();
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Creating the customer failed');
    expect(deletes(fetchMock)).toEqual([[PARTY_URL, null]]);
    expect(bodies(fetchMock)).toHaveLength(2);
  });

  it('deletes nothing when the party step fails', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => tmfError(422, 'Unprocessable Entity'),
    });
    const dialog = await fillOlga();
    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'Creating the party failed',
    );
    expect(deletes(fetchMock)).toEqual([]);
  });

  it('lists what is left, with a link to Parties, when a compensation fails', async () => {
    const { fetchMock } = backend(undefined, {
      [`POST ${PARTY}/individual`]: () => json({ id: NEW_PARTY }, { status: 201 }),
      [`POST ${CUSTOMERS}`]: () => json({ id: NEW_CUSTOMER }, { status: 201 }),
      [`POST ${ACCOUNTS}`]: () => tmfError(422, 'Unprocessable Entity'),
      [`DELETE ${CUSTOMER_URL}`]: () => tmfError(503, 'Unavailable'),
    });
    const dialog = await fillOlga();
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Creating the billing account failed');
    expect(alert.textContent).toContain(`customer Olga K ${NEW_CUSTOMER}`);
    expect(alert.textContent).toContain(`individual Olga K ${NEW_PARTY}`);
    expect(within(alert).getByRole('link', { name: 'Open Parties' }).getAttribute('href')).toBe(
      '/parties',
    );
    // the party is not tried while its customer still exists
    expect(deletes(fetchMock)).toEqual([[CUSTOMER_URL, null]]);
  });
});

describe('subscribers inspector (>= 1536 px)', () => {
  it('navigates on a click below 1536 px and shows no inspector', async () => {
    mockWidth(false);
    backend(undefined, { [`GET ${ACCOUNTS}/${A1}`]: pending });
    await open();
    await screen.findByText('Ivan Petrov');
    fireEvent.click(rowOf(/Ivan Petrov/) as HTMLElement);
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
    expect(document.querySelector('[data-ui="ui.subscribers.inspector"]')).toBeNull();
  });

  it('selects the row and shows the inspector from the loaded list, with no new request', async () => {
    mockWidth(true);
    const { fetchMock } = backend();
    await open();
    await screen.findByText('Ivan Petrov');
    const before = fetchMock.mock.calls.length;
    fireEvent.click(rowOf(/Ivan Petrov/) as HTMLElement);
    expect(window.location.pathname).toBe('/billing-accounts');
    expect(rowOf(/Ivan Petrov/)?.getAttribute('aria-selected')).toBe('true');
    const inspector = expectUi('ui.subscribers.inspector');
    expect(within(inspector).getByRole('heading', { name: 'Ivan Petrov' })).toBeTruthy();
    expect(within(inspector).getByRole('button', { name: `Copy ${A1}` })).toBeTruthy();
    expect(within(inspector).getByText('Active')).toBeTruthy();
    expect(within(inspector).getByText('Postpaid')).toBeTruthy();
    expect(within(inspector).getByText('1,250.40')).toBeTruthy();
    expect(within(inspector).getByText('2 active')).toBeTruthy();
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it('opens the card on Enter, a double click and Open account', async () => {
    mockWidth(true);
    backend(undefined, { [`GET ${ACCOUNTS}/${A1}`]: pending, [`GET ${ACCOUNTS}/${A2}`]: pending });
    await open();
    await screen.findByText('Ivan Petrov');
    const ivan = rowOf(/Ivan Petrov/) as HTMLElement;
    fireEvent.keyDown(ivan, { key: 'Enter' });
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
    window.history.replaceState(null, '', '/billing-accounts');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await screen.findByRole('heading', { name: 'Billing accounts' });
    fireEvent.doubleClick(rowOf(/Anna Smirnova/) as HTMLElement);
    expect(window.location.pathname).toBe(`/billing-accounts/${A2}`);
  });

  it('opens the card from Open account', async () => {
    mockWidth(true);
    backend(undefined, { [`GET ${ACCOUNTS}/${A1}`]: pending });
    await open();
    await screen.findByText('Ivan Petrov');
    fireEvent.click(rowOf(/Ivan Petrov/) as HTMLElement);
    fireEvent.click(
      within(expectUi('ui.subscribers.inspector')).getByRole('button', { name: 'Open account' }),
    );
    expect(window.location.pathname).toBe(`/billing-accounts/${A1}`);
  });

  it('closes on Escape', async () => {
    mockWidth(true);
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    fireEvent.click(rowOf(/Ivan Petrov/) as HTMLElement);
    expectUi('ui.subscribers.inspector');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.querySelector('[data-ui="ui.subscribers.inspector"]')).toBeNull();
    expect(rowOf(/Ivan Petrov/)?.getAttribute('aria-selected')).toBeNull();
  });
});
