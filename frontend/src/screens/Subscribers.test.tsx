import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { CONTEXT, json, stubFetch, tmfError, type Handler } from '../test/fetch';
import { accountBody } from '../test/fixtures';

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';
const PRODUCTS = '/tmf-api/productInventory/v5/product';

const balance = (value: number) => [
  { balanceType: 'currentBalance', amount: { unit: 'RUB', value }, '@type': 'AccountBalance' },
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

function backend(accounts: Handler = () => json(ROWS, { total: ROWS.length })) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
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
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 6);
const rowOf = (name: RegExp) => dataRows().find((row) => name.test(row.textContent ?? ''));

describe('subscribers list', () => {
  it('shows one row per billing account with balance, products and cycle day', async () => {
    backend();
    await open();
    await screen.findByText('Ivan Petrov');
    expect(dataRows()).toHaveLength(4);

    const ivan = rowOf(/Ivan Petrov/) as HTMLElement;
    const cells = within(ivan).getAllByRole('cell');
    expect(cells[0]?.textContent).toBe('AAAA1111'); // the customer's short id
    expect(cells[2]?.textContent).toBe('Active');
    expect(cells[3]?.textContent).toBe('1,250.40RUB');
    expect(cells[4]?.textContent).toBe('2');
    expect(cells[5]?.textContent).toBe('Day 5');

    const anna = rowOf(/Anna Smirnova/) as HTMLElement;
    expect(within(anna).getByText('Suspended').className).toContain('inactive');
    const negative = within(anna).getByText('−116.13');
    expect(negative.parentElement?.className).toContain('chip');
    expect(within(anna).getAllByRole('cell')[4]?.firstElementChild?.className).toBe('bss-zero');

    // a customer with two accounts is two rows, told apart by the account name
    expect(rowOf(/Romashka HQ/)).toBeTruthy();
    expect(rowOf(/Romashka branch/)).toBeTruthy();
    expect(screen.getByText('Showing 4 of 4')).toBeTruthy();
    expect(screen.getByText('Amounts in billing account currency')).toBeTruthy();
    expect(screen.getByText('Negative balance:').textContent).toContain('1');
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
    expect(dataRows().map((r) => within(r).getAllByRole('cell')[1]?.textContent)).toEqual([
      'Anna Smirnova',
    ]);
    expect(screen.getByText('Showing 1 of 4')).toBeTruthy();
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
    expect(screen.getByText('No matches')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(dataRows()).toHaveLength(4);
    expect((search as HTMLInputElement).value).toBe('');
  });

  it('opens the card on a row click', async () => {
    backend();
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
    expect(
      screen.getByText('Subscribers appear after registration via CRM or import.'),
    ).toBeTruthy();
  });
});
