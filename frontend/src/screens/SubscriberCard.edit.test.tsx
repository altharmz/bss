import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, pending, stubFetch, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody, billBody, billsUrl } from '../test/fixtures';
import {
  A,
  C,
  O2,
  P1,
  ORDERS,
  INTERNAL,
  usd,
  ACCOUNT,
  product,
  PRODUCTS,
  CHARGES,
  TRANSACTIONS,
  backend,
  open,
  selectTab,
  productMenu,
  productRow,
  PARTY_ID,
  PARTY_URL,
  CUSTOMER_URL,
  ACCOUNT_URL,
  ACCOUNT_BASE,
  FINANCIAL_URL,
  FIN1,
  FIN2,
  editBackend,
  openEdit,
  patches,
  BUCKET,
} from '../test/subscriberCardKit';

describe('edit subscriber', () => {
  it('patches only what changed: the party and the account', async () => {
    const { fetchMock } = editBackend({
      [`PATCH ${PARTY_URL}`]: () => json({ id: PARTY_ID }),
      [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT),
    });
    const dialog = await openEdit();
    expectUi('ui.subscriber.edit');
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    expect(within(dialog).getByDisplayValue('500.00')).toBeTruthy(); // the credit limit

    fireEvent.change(within(dialog).getByLabelText('Family name'), {
      target: { value: 'Kuznetsova-Petrova' },
    });
    fireEvent.click(
      within(within(dialog).getByRole('group', { name: 'Account state' })).getByRole('button', {
        name: 'Suspended',
      }),
    );
    expect(within(dialog).getByText(/takes no new orders/)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '' },
    });
    fireEvent.click(save);
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Saved: party, billing account',
    );
    expect(patches(fetchMock)).toEqual([
      [PARTY_URL, '{"familyName":"Kuznetsova-Petrova"}'],
      [ACCOUNT_URL, '{"state":"Suspended","creditLimit":null}'],
    ]);
  });

  it('sends a new credit limit as an exact token, and the customer status', async () => {
    const { fetchMock } = editBackend({
      [`PATCH ${CUSTOMER_URL}`]: () => json({ id: C }),
      [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT),
    });
    const dialog = await openEdit();
    fireEvent.click(
      within(within(dialog).getByRole('group', { name: 'Customer status' })).getByRole('button', {
        name: 'Inactive',
      }),
    );
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '2,000.1' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([
      [CUSTOMER_URL, '{"status":"Inactive"}'],
      [ACCOUNT_URL, '{"creditLimit":{"unit":"USD","value":2000.10}}'],
    ]);
  });

  it('says what was already saved when a later step fails', async () => {
    editBackend({
      [`PATCH ${PARTY_URL}`]: () => json({ id: PARTY_ID }),
      [`PATCH ${ACCOUNT_URL}`]: () => tmfError(422, 'Unprocessable Entity'),
    });
    const dialog = await openEdit();
    fireEvent.change(within(dialog).getByLabelText('Given name'), { target: { value: 'Olya' } });
    fireEvent.change(within(dialog).getByLabelText('Account name'), { target: { value: 'Main' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Saving the billing account failed');
    expect(alert.textContent).toContain('Already saved: party.');
  });

  it('cannot save an empty name or a bad credit limit', async () => {
    editBackend();
    const dialog = await openEdit();
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    fireEvent.change(within(dialog).getByLabelText('Customer name'), { target: { value: ' ' } });
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Customer name'), {
      target: { value: 'Olga K' },
    });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (empty = none)'), {
      target: { value: '1.234' },
    });
    expect(within(dialog).getByText('Enter a credit limit with at most 2 decimals.')).toBeTruthy();
    expect(save.disabled).toBe(true);
  });
});

describe('billing structure in the edit form', () => {
  const CY1 = 'c1000000-0000-4000-8000-000000000001';
  const BF1 = 'b1000000-0000-4000-8000-000000000001';
  const BF2 = 'b2000000-0000-4000-8000-000000000002';
  const BM1 = 'e1000000-0000-4000-8000-000000000001';
  const BM2 = 'e2000000-0000-4000-8000-000000000002';
  const LISTS: Record<string, Handler> = {
    [`GET ${ACCOUNT_BASE}/billingCycleSpecification?offset=0&limit=100`]: () =>
      json(
        [
          { id: 'monthly', name: 'Monthly billing' },
          { id: CY1, name: 'Corporate monthly' },
        ],
        { total: 2 },
      ),
    [`GET ${ACCOUNT_BASE}/billFormat?offset=0&limit=100`]: () =>
      json(
        [
          { id: BF1, name: 'JSON detailed' },
          { id: BF2, name: 'JSON summary' },
        ],
        { total: 2 },
      ),
    [`GET ${ACCOUNT_BASE}/billPresentationMedia?offset=0&limit=100`]: () =>
      json(
        [
          { id: BM1, name: 'Email' },
          { id: BM2, name: 'Post' },
        ],
        { total: 2 },
      ),
  };
  const structured = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    {
      billStructure: {
        cycleSpecification: { id: CY1, name: 'Corporate monthly' },
        format: { id: BF1, name: 'JSON detailed' },
        presentationMedia: [{ id: BM1, name: 'Email' }],
        '@type': 'BillStructure',
      },
    },
  );

  it('selects a cycle specification, a format and media: billStructure patch of the changes', async () => {
    const { fetchMock } = editBackend({ ...LISTS, [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT) });
    const dialog = await openEdit();
    expectUi('ui.subscriber.billingGroup');
    await within(dialog).findByRole('option', { name: 'Corporate monthly' });
    expectUi('ui.subscriber.cycleSpecification');
    expectUi('ui.subscriber.billFormat');
    expectUi('ui.subscriber.presentationMedia');
    const cycle = within(dialog).getByLabelText('Cycle specification') as HTMLSelectElement;
    expect(cycle.value).toBe('monthly');
    fireEvent.change(cycle, { target: { value: CY1 } });
    fireEvent.change(within(dialog).getByLabelText('Bill format'), { target: { value: BF2 } });
    fireEvent.click(await within(dialog).findByLabelText('Email'));
    fireEvent.click(within(dialog).getByLabelText('Post'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([
      [
        ACCOUNT_URL,
        JSON.stringify({
          billStructure: {
            cycleSpecification: {
              id: CY1,
              '@type': 'BillingCycleSpecificationRef',
              '@referredType': 'BillingCycleSpecification',
            },
            format: { id: BF2, '@type': 'BillFormatRef', '@referredType': 'BillFormat' },
            presentationMedia: [
              {
                id: BM1,
                '@type': 'BillPresentationMediaRef',
                '@referredType': 'BillPresentationMedia',
              },
              {
                id: BM2,
                '@type': 'BillPresentationMediaRef',
                '@referredType': 'BillPresentationMedia',
              },
            ],
          },
        }),
      ],
    ]);
  });

  it('clears: back to the built-in cycle, no format, no media', async () => {
    const { fetchMock } = editBackend(
      { ...LISTS, [`PATCH ${ACCOUNT_URL}`]: () => json(structured) },
      structured,
    );
    const dialog = await openEdit();
    await within(dialog).findByRole('option', { name: 'Corporate monthly' });
    expect((within(dialog).getByLabelText('Cycle specification') as HTMLSelectElement).value).toBe(
      CY1,
    );
    expect((within(dialog).getByLabelText('Bill format') as HTMLSelectElement).value).toBe(BF1);
    expect((within(dialog).getByLabelText('Email') as HTMLInputElement).checked).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Cycle specification'), {
      target: { value: 'monthly' },
    });
    fireEvent.change(within(dialog).getByLabelText('Bill format'), { target: { value: '' } });
    fireEvent.click(within(dialog).getByLabelText('Email'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([
      [
        ACCOUNT_URL,
        JSON.stringify({
          billStructure: {
            cycleSpecification: {
              id: 'monthly',
              '@type': 'BillingCycleSpecificationRef',
              '@referredType': 'BillingCycleSpecification',
            },
            format: null,
            presentationMedia: [],
          },
        }),
      ],
    ]);
  });

  it('shows the 422 of a setting that vanished and keeps the choice', async () => {
    editBackend({
      ...LISTS,
      [`PATCH ${ACCOUNT_URL}`]: () => tmfError(422, 'billStructure.format does not exist'),
    });
    const dialog = await openEdit();
    await within(dialog).findByRole('option', { name: 'JSON summary' });
    fireEvent.change(within(dialog).getByLabelText('Bill format'), { target: { value: BF2 } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('format does not exist');
    expect((within(dialog).getByLabelText('Bill format') as HTMLSelectElement).value).toBe(BF2);
  });

  // #499: one failed list says so; Retry asks for all three again
  it('retries the billing settings lists after one fails to load', async () => {
    let formats = 0;
    const { calls } = editBackend({
      ...LISTS,
      [`GET ${ACCOUNT_BASE}/billFormat?offset=0&limit=100`]: () => {
        formats += 1;
        return formats === 1
          ? tmfError(500, 'Internal Server Error')
          : json([{ id: BF2, name: 'JSON summary' }], { total: 1 });
      },
    });
    const dialog = await openEdit();
    const note = await within(dialog).findByText(/Failed to load the billing settings/);
    const lists = (path: string) =>
      calls.filter((c) => c === `GET ${ACCOUNT_BASE}/${path}?offset=0&limit=100`).length;
    const before = ['billingCycleSpecification', 'billFormat', 'billPresentationMedia'].map(lists);
    fireEvent.click(within(note).getByRole('button', { name: 'Retry' }));

    expect(await within(dialog).findByRole('option', { name: 'JSON summary' })).toBeTruthy();
    expect(within(dialog).queryByText(/Failed to load the billing settings/)).toBeNull();
    expect(['billingCycleSpecification', 'billFormat', 'billPresentationMedia'].map(lists)).toEqual(
      before.map((n) => n + 1),
    );
  });
});

// #499: an organization's name is edited in place of the person's names
describe('edit an organization subscriber', () => {
  const ORG_URL = `/tmf-api/partyManagement/v5/organization/${PARTY_ID}`;

  it('patches the organization with its new name only', async () => {
    const { fetchMock } = editBackend({
      [`GET ${CUSTOMER_URL}`]: () =>
        json({
          id: C,
          name: 'Olga Kuznetsova',
          status: 'Active',
          engagedParty: { id: PARTY_ID, '@type': 'PartyRef', '@referredType': 'Organization' },
          '@type': 'Customer',
        }),
      [`GET ${ORG_URL}`]: () =>
        json({ id: PARTY_ID, name: 'Kuznetsova Trading', '@type': 'Organization' }),
      [`PATCH ${ORG_URL}`]: () => json({ id: PARTY_ID }),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit billing account' });
    const name = await within(dialog).findByLabelText('Organization name');
    expect(name).toHaveProperty('value', 'Kuznetsova Trading');
    expect(within(dialog).queryByLabelText('Family name')).toBeNull();
    fireEvent.change(name, { target: { value: '  ' } });
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    fireEvent.change(name, { target: { value: 'Kuznetsova Trading LLC ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Saved: party');
    expect(patches(fetchMock)).toEqual([[ORG_URL, '{"name":"Kuznetsova Trading LLC"}']]);
  });
});

describe('link a financial account', () => {
  const linked = accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    { financialAccount: { id: FIN1, name: 'Group receivables', '@type': 'FinancialAccountRef' } },
  );
  const LINK = {
    id: FIN2,
    '@type': 'FinancialAccountRef',
    '@referredType': 'FinancialAccount',
  };

  it('lists the owner financial accounts and links the chosen one', async () => {
    const { fetchMock, calls } = editBackend({ [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT) });
    const dialog = await openEdit();
    expectUi('ui.subscriber.financialLink');
    expect(calls).toContain(`GET ${FINANCIAL_URL}?relatedParty.id=${PARTY_ID}&offset=0&limit=100`);
    const select = within(dialog).getByLabelText('Financial account') as HTMLSelectElement;
    expect(select.value).toBe('');
    expect(await within(select).findByRole('option', { name: 'Old group (closed)' })).toBeTruthy();
    fireEvent.change(select, { target: { value: FIN2 } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([[ACCOUNT_URL, JSON.stringify({ financialAccount: LINK })]]);
  });

  it('unlinks with null', async () => {
    const { fetchMock } = editBackend({ [`PATCH ${ACCOUNT_URL}`]: () => json(ACCOUNT) }, linked);
    const dialog = await openEdit();
    const select = within(dialog).getByLabelText('Financial account') as HTMLSelectElement;
    expect(select.value).toBe(FIN1);
    fireEvent.change(select, { target: { value: '' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await screen.findByRole('status');
    expect(patches(fetchMock)).toEqual([[ACCOUNT_URL, '{"financialAccount":null}']]);
  });

  it('shows the 422 of another owner and keeps the choice', async () => {
    editBackend({
      [`PATCH ${ACCOUNT_URL}`]: () =>
        tmfError(422, 'financialAccount: must be owned by the same party as this account'),
    });
    const dialog = await openEdit();
    const select = within(dialog).getByLabelText('Financial account') as HTMLSelectElement;
    await within(select).findByRole('option', { name: 'Old group (closed)' });
    fireEvent.change(select, { target: { value: FIN2 } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('same party');
    expect(select.value).toBe(FIN2);
  });
});

describe('delete a billing account', () => {
  const EMPTY: Record<string, Handler> = {
    [`GET ${INTERNAL}/products`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/charges?limit=20`]: () => json([], { total: 0 }),
    [`GET ${INTERNAL}/transactions?limit=20`]: () => json([], { total: 0 }),
  };
  const LIST = '/internal/v1/subscriber?offset=0&limit=50';
  const DELETE_KEY = `DELETE ${ACCOUNT_URL}`;
  const deleteButton = () => screen.queryByRole('menuitem', { name: 'Delete…' });
  const deletes = (calls: string[]) => calls.filter((c) => c.startsWith('DELETE'));

  it('is offered for an empty postpaid account: DELETE with no body, then the list', async () => {
    const { fetchMock, calls } = backend({
      ...EMPTY,
      [DELETE_KEY]: () => new Response(null, { status: 204 }),
      [`GET ${LIST}`]: () => json([], { total: 0 }),
    });
    await open();
    await screen.findByText('No active products');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitFor(() => expect(deleteButton()).toBeTruthy());
    expectUi('ui.subscriber.delete');
    fireEvent.click(deleteButton() as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: 'Delete billing account' });
    expectUi('ui.subscriber.deleteConfirm');
    expect(dialog.textContent).toContain(
      'Deletes the billing account. Its customer and party stay (delete them under Parties).',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(window.location.pathname).toBe('/billing-accounts'));
    expect(deletes(calls)).toEqual([DELETE_KEY]);
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(call?.[1]?.body).toBeUndefined();
  });

  it('adds a billing account for the same customer from More actions', async () => {
    const NEW = 'ffff6666-0000-4000-8000-0000000000a6';
    const { fetchMock } = backend({
      ...EMPTY,
      'POST /tmf-api/accountManagement/v5/billingAccount': () => json({ id: NEW }, { status: 201 }),
      [`GET /tmf-api/accountManagement/v5/billingAccount/${NEW}`]: pending,
    });
    await open();
    await screen.findByText('No active products');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expectUi('ui.subscriber.addAccount');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add billing account' });
    fireEvent.change(within(dialog).getByLabelText('Billing cycle day (1-28)'), {
      target: { value: '15' },
    });
    fireEvent.change(within(dialog).getByLabelText('Credit limit, USD (optional)'), {
      target: { value: '1500.50' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add billing account' }));
    await waitFor(() => expect(window.location.pathname).toBe(`/billing-accounts/${NEW}`));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(String(post?.[1]?.body)).toBe(
      '{"@type":"BssBillingAccount","name":"Olga Kuznetsova main","billCycleDay":15,' +
        '"currency":"USD","relatedParty":[{"@type":"RelatedPartyRefOrPartyRoleRef",' +
        `"role":"customer","partyOrPartyRole":{"id":"${C}","name":"Olga Kuznetsova",` +
        '"@type":"PartyRoleRef","@referredType":"Customer"}}],' +
        '"creditLimit":{"unit":"USD","value":1500.50}}',
    );
  });

  it('is not offered with a product, a charge, a transaction or a bill', async () => {
    backend(); // the default card has products, charges and transactions
    await open();
    await screen.findByText('Fiber 100');
    await screen.findByText('Transaction history');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(deleteButton()).toBeNull();
  });

  it.each([
    ['a charge', { [`GET ${INTERNAL}/charges?limit=20`]: () => json(CHARGES, { total: 2 }) }],
    [
      'a transaction',
      { [`GET ${INTERNAL}/transactions?limit=20`]: () => json(TRANSACTIONS, { total: 3 }) },
    ],
    [
      'a bill',
      {
        [billsUrl(A)]: () => json([billBody('b9', '2026-09-01', 10, 10, 'new')], { total: 1 }),
      },
    ],
    ['a product', { [`GET ${INTERNAL}/products`]: () => json(PRODUCTS, { total: 3 }) }],
  ])('is not offered with %s alone', async (_what, extra) => {
    backend({ ...EMPTY, ...extra });
    await open();
    await new Promise((resolve) => setTimeout(resolve, 30));
    await screen.findByText('Transaction history');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(deleteButton()).toBeNull();
  });

  it('is never offered for a prepaid account', async () => {
    backend(
      {
        ...EMPTY,
        'GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=aaaa1111-0000-4000-8000-0000000000a1&offset=0&limit=100':
          () => json([], { total: 0 }),
      },
      accountBody(A, { id: C, name: 'Olga Kuznetsova' }, { ratingType: 'prepaid' }),
    );
    await open();
    await new Promise((resolve) => setTimeout(resolve, 30));
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(deleteButton()).toBeNull();
  });

  it('shows the 409 as close it instead and keeps the dialog', async () => {
    backend({ ...EMPTY, [DELETE_KEY]: () => tmfError(409, 'billingAccount has charges') });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitFor(() => expect(deleteButton()).toBeTruthy());
    fireEvent.click(deleteButton() as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: 'Delete billing account' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('close it instead');
    expect(window.location.pathname).toBe(`/billing-accounts/${A}`);
  });

  // #499: an unanswered delete offers Refresh (the list), another refusal is shown as it is,
  // and a refused new account keeps what was typed
  const openDelete = async () => {
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    await waitFor(() => expect(deleteButton()).toBeTruthy());
    fireEvent.click(deleteButton() as HTMLElement);
    return screen.findByRole('dialog', { name: 'Delete billing account' });
  };

  it('offers Refresh after an unanswered delete, which opens the list', async () => {
    const { calls } = backend({
      ...EMPTY,
      [DELETE_KEY]: () => tmfError(504, 'Gateway Timeout'),
      [`GET ${LIST}`]: () => json([], { total: 0 }),
    });
    const dialog = await openDelete();
    expect(within(dialog).queryByRole('button', { name: 'Refresh' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('Unsure whether it was saved: Refresh to check.');
    expect(alert.textContent).not.toContain('close it instead');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(window.location.pathname).toBe('/billing-accounts'));
    expect(await screen.findByText('Refreshed')).toBeTruthy();
    expect(deletes(calls)).toEqual([DELETE_KEY]);
  });

  it('shows another refusal as the server words it, with no Refresh', async () => {
    const { calls } = backend({
      ...EMPTY,
      [DELETE_KEY]: () => tmfError(403, 'operator may not delete accounts'),
    });
    const dialog = await openDelete();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toBe(`DELETE ${ACCOUNT_URL} → 403 operator may not delete accounts`);
    expect(within(dialog).queryByRole('button', { name: 'Refresh' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toHaveProperty(
      'disabled',
      false,
    );
    expect(window.location.pathname).toBe(`/billing-accounts/${A}`);
    expect(deletes(calls)).toEqual([DELETE_KEY]);
  });

  it('keeps the new account typed in after a refusal', async () => {
    const { fetchMock } = backend({
      ...EMPTY,
      'POST /tmf-api/accountManagement/v5/billingAccount': () =>
        tmfError(422, 'creditLimit exceeds the allowed maximum'),
    });
    await open();
    await screen.findByText('No active products');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add billing account' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add billing account' });
    const day = within(dialog).getByLabelText('Billing cycle day (1-28)');
    const limit = within(dialog).getByLabelText('Credit limit, USD (optional)');
    fireEvent.change(day, { target: { value: '15' } });
    fireEvent.change(limit, { target: { value: '999999' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add billing account' }));

    expect(await within(dialog).findByText(/creditLimit exceeds the allowed maximum/)).toBeTruthy();
    expect(day).toHaveProperty('value', '15');
    expect(limit).toHaveProperty('value', '999999');
    expect(window.location.pathname).toBe(`/billing-accounts/${A}`);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });
});

describe('Ledger subscriber header', () => {
  it('uses the account identity header and opens postpaid payment from More actions', async () => {
    backend();
    await open();
    const heading = screen.getByRole('heading', { name: 'Olga Kuznetsova' });
    expect(heading.closest('.bss-panel')).toBeNull();
    expect(screen.getByText('POSTPAID')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add product' }).className).toContain('secondary');
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expectUi('ui.subscriber.moreActions');
    expect(screen.queryByRole('menuitem', { name: 'Delete…' })).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('menuitem', { name: 'Record payment' })).toHaveProperty(
        'disabled',
        false,
      ),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Record payment' }));
    expect(await screen.findByRole('dialog', { name: 'Record payment' })).toBeTruthy();
  });
});

describe('Ledger subscriber tabs', () => {
  it.each(['#bills', '#unknown', '#balance', '#commands', ''])(
    'resolves the initial hash %s',
    async (hash) => {
      backend();
      await open(hash);
      expect(
        screen
          .getByRole('tab', { name: hash === '#bills' ? /^Bills/ : 'Overview' })
          .getAttribute('aria-selected'),
      ).toBe('true');
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
      expectUi('ui.subscriber.tabs');
    },
  );

  it('moves every postpaid panel into its labelled pane, keeps loaders eager and reuses their data', async () => {
    const { calls } = backend();
    await open();
    await waitFor(() => expect(screen.getByRole('tab', { name: /^Payments 0$/ })).toBeTruthy());
    const before = [...calls];
    for (const [tab, marker] of [
      ['Bills', 'ui.subscriber.bills'],
      ['Payments', 'ui.subscriber.payments'],
      ['Charges', 'ui.subscriber.charges'],
      ['Transactions', 'ui.subscriber.transactions'],
      ['Account', 'ui.subscriber.account'],
    ]) {
      if (!tab || !marker) throw new Error('missing test case');
      selectTab(tab);
      const active = screen.getByRole('tabpanel');
      expect(active.querySelector(`[data-ui="${marker}"]`)).toBeTruthy();
      expect(window.location.hash).toBe(`#${tab.toLowerCase()}`);
      expectUi(marker);
    }
    expect(calls).toEqual(before);
    expect(screen.queryByRole('tab', { name: 'Balance actions' })).toBeNull();
    expect(screen.queryByRole('tab', { name: /^Commands/ })).toBeNull();
    selectTab('Overview');
    expect(screen.getByRole('tabpanel').querySelector('.subscriber-overview')).toBeTruthy();
  });

  it('counts loaded bills, payments plus refunds and caps history labels at 20+', async () => {
    backend({
      [`GET /tmf-api/payment/v4/payment?account.id=${A}&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'pay',
              paymentDate: '2026-09-01T09:00:00Z',
              totalAmount: usd(1),
              paymentMethod: { name: 'Cash' },
              status: 'done',
            },
          ],
          { total: 1 },
        ),
      [`GET /tmf-api/payment/v4/refund?account.id=${A}&offset=0&limit=100`]: () =>
        json(
          [
            {
              id: 'ref',
              refundDate: '2026-09-01T09:00:00Z',
              totalAmount: usd(1),
              paymentMethod: { name: 'Cash' },
            },
          ],
          { total: 1 },
        ),
      [`GET ${INTERNAL}/charges?limit=20`]: () =>
        json(Array.from({ length: 20 }, (_, i) => ({ ...CHARGES[0], id: `charge-${i}` }))),
      [billsUrl(A)]: () => json([billBody('b1', '2026-09-01', 10, 10, 'new')], { total: 1 }),
    });
    await open();
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Payments 2' })).toBeTruthy());
    expect(screen.getByRole('tab', { name: 'Bills 1' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Charges 20+' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Transactions 3' })).toBeTruthy();
  });

  it('shows prepaid Balance actions and Commands and follows browser hash changes', async () => {
    backend(
      {
        [`GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`]:
          () =>
            json(
              [
                {
                  id: BUCKET,
                  remainingValue: { amount: 10, units: 'USD' },
                  reservedValue: { amount: 0, units: 'USD' },
                },
              ],
              { total: 1 },
            ),
      },
      { ...ACCOUNT, ratingType: 'prepaid' },
    );
    await open();
    selectTab('Balance actions');
    await waitFor(() =>
      expect(
        screen.getByRole('tabpanel').querySelector('[data-ui="ui.subscriber.prepaid.history"]'),
      ).toBeTruthy(),
    );
    selectTab('Commands');
    expect(
      within(screen.getByRole('tabpanel'))
        .getByRole('link', { name: 'Prepaid commands' })
        .getAttribute('href'),
    ).toBe(`/prepaid-commands?account=${A}`);
    window.history.replaceState(null, '', `#account`);
    fireEvent(window, new HashChangeEvent('hashchange'));
    expect(
      screen.getByRole('tabpanel').querySelector('[data-ui="ui.subscriber.account"]'),
    ).toBeTruthy();
  });
});

describe('same-day status Undo', () => {
  const products = (rows: object[]) => ({
    [`GET ${INTERNAL}/products`]: () => json(rows, { total: rows.length }),
  });
  const posts = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
  const openMenu = (row: HTMLElement) =>
    fireEvent.click(within(row).getByRole('button', { name: 'Actions' }));
  it.each([
    ['suspended', 'Undo suspension', 'Resume…'],
    ['active', 'Undo resumption', 'Suspend…'],
  ])('cancels an applied %s change through its menu', async (status, label, opposite) => {
    const { fetchMock } = backend({
      ...products([
        product(P1, {
          status,
          undoableStatusChange: {
            date: '2026-10-05',
            status,
            productOrder: { id: 'ord-today' },
          },
        }),
      ]),
      [`POST /tmf-api/productOrderingManagement/v5/cancelProductOrder`]: () =>
        json({ id: 'k1', state: 'done', '@type': 'CancelProductOrder' }, { status: 201 }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    openMenu(row);
    const menu = productMenu(row);
    expect(within(menu).queryByRole('menuitem', { name: opposite })).toBeNull();
    const undo = within(menu).getByRole('menuitem', { name: label });
    expect(undo).toBe(expectUi('ui.subscriber.undoStatusChange'));
    fireEvent.click(undo);
    await screen.findByRole('status');
    expect(String(posts(fetchMock)[0]?.[1]?.body)).toBe(
      JSON.stringify({
        '@type': 'CancelProductOrder',
        productOrder: { id: 'ord-today', '@type': 'ProductOrderRef' },
      }),
    );
  });

  it('undoes a future suspension from its pending line', async () => {
    const { fetchMock } = backend({
      ...products([
        product(P1, {
          pendingChanges: [
            {
              kind: 'status',
              date: '2026-11-03',
              status: 'suspended',
              productOrder: { id: 'ord-future' },
            },
          ],
        }),
      ]),
      [`POST /tmf-api/productOrderingManagement/v5/cancelProductOrder`]: () =>
        json({ id: 'k1', state: 'done', '@type': 'CancelProductOrder' }, { status: 201 }),
    });
    await open();
    const row = await waitFor(() => productRow('Mobile L'));
    expect(within(row).getByText('Suspends on 2026-11-03')).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'Undo' }));
    await screen.findByRole('status');
    expect(String(posts(fetchMock)[0]?.[1]?.body)).toBe(
      JSON.stringify({
        '@type': 'CancelProductOrder',
        productOrder: { id: 'ord-future', '@type': 'ProductOrderRef' },
      }),
    );
  });
});

// G5: account-scoped unfinished orders precede the products, including draft prices.
describe('subscriber Overview orders', () => {
  it('loads all three unfinished states and shows the requested-start offering price', async () => {
    const list = (state: string) =>
      `GET ${ORDERS}?billingAccount.id=${A}&state=${state}&offset=0&limit=50`;
    const { calls } = backend({
      [list('draft')]: () =>
        json(
          [
            {
              id: 'a0000000-0000-4000-8000-000000000002',
              state: 'draft',
              billingAccount: { id: A },
              requestedStartDate: '2026-10-06T09:00:00Z',
              productOrderItem: [
                { id: '1', action: 'add', productOffering: { id: O2, name: 'Draft Fiber' } },
              ],
            },
          ],
          { total: 1 },
        ),
      [list('acknowledged')]: () =>
        json(
          [
            {
              id: 'a0000000-0000-4000-8000-000000000003',
              state: 'acknowledged',
              productOrderItem: [],
            },
          ],
          { total: 1 },
        ),
      [list('inProgress')]: () =>
        json(
          [
            {
              id: 'a0000000-0000-4000-8000-000000000004',
              state: 'inProgress',
              productOrderItem: [],
            },
          ],
          { total: 1 },
        ),
      [`GET /tmf-api/productCatalogManagement/v5/productOffering/${O2}`]: () =>
        json({
          id: O2,
          name: 'Draft Fiber',
          lifecycleStatus: 'Active',
          productOfferingPrice: [{ id: 'old-draft-price' }, { id: 'draft-price' }],
        }),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice/old-draft-price': () =>
        json({
          id: 'old-draft-price',
          name: 'Earlier rate',
          priceType: 'recurring',
          price: usd(111),
          validFor: { endDateTime: '2026-10-05T21:00:00Z' },
        }),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice/draft-price': () =>
        json({
          id: 'draft-price',
          name: 'Draft rate',
          priceType: 'recurring',
          price: usd(777),
          validFor: { startDateTime: '2026-10-05T21:00:00Z', endDateTime: '2026-10-06T21:00:00Z' },
        }),
      'GET /tmf-api/productOrderingManagement/v5/cancelProductOrder?productOrder.id=a0000000-0000-4000-8000-000000000002&offset=0&limit=100':
        () => json([], { total: 0 }),
    });
    await open();
    const box = expectUi('ui.subscriber.unfinishedOrders');
    await within(box).findByText('777.00');
    expect(within(box).getByText('Draft Fiber')).toBeTruthy();
    expect(within(box).getAllByRole('button', { name: 'Open' })).toHaveLength(3);
    for (const state of ['draft', 'acknowledged', 'inProgress'])
      expect(calls).toContain(list(state));
    expect(
      box.compareDocumentPosition(screen.getByText('Active products')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(within(box).getAllByRole('button', { name: 'Open' })[0] as HTMLElement);
    expect(await screen.findByRole('dialog', { name: /Order/ })).toBeTruthy();
    expectUi('ui.subscriber.openOrder');
  });

  it('labels the customer and future product start explicitly', async () => {
    backend();
    await open();
    expect(screen.getByText(/Customer no\./)).toBeTruthy();
    expect(await screen.findByText('Starts 2026-10-06')).toBeTruthy();
  });

  // #487: a draft submitted from the card that the worker completes later is followed there
  it('follows a draft submitted from the card that is accepted in progress', async () => {
    const DRAFT_ID = 'a0000000-0000-4000-8000-000000000002';
    const draft = {
      id: DRAFT_ID,
      state: 'draft',
      billingAccount: { id: A },
      requestedStartDate: '2026-10-06T09:00:00Z',
      productOrderItem: [
        { id: '1', action: 'add', productOffering: { id: O2, name: 'Draft Fiber' } },
      ],
    };
    const { fetchMock } = backend({
      [`GET ${ORDERS}?billingAccount.id=${A}&state=draft&offset=0&limit=50`]: () =>
        json([draft], { total: 1 }),
      [`GET ${ORDERS}/${DRAFT_ID}`]: () => json(draft),
      [`PATCH ${ORDERS}/${DRAFT_ID}`]: () => json({ id: DRAFT_ID, state: 'inProgress' }),
      [`GET /internal/v1/prepaid/command/${DRAFT_ID}`]: pending,
      'GET /*': pending,
    });
    await open();
    const box = expectUi('ui.subscriber.unfinishedOrders');
    fireEvent.click(await within(box).findByRole('button', { name: 'Open' }));
    const dialog = await screen.findByRole('dialog', { name: /Order/ });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Submit' }));
    const watch = await waitFor(() => expectUi('ui.subscriber.prepaid.orderWatch'));
    expect(watch.textContent).toContain('Order A0000000 accepted');
    expect(
      fetchMock.mock.calls
        .filter(([, init]) => init?.method === 'PATCH')
        .map(([, init]) => init?.body),
    ).toEqual(['{"state":"acknowledged"}']);
  });
});

// #498: a draft price that fails to load retries in place; the order dialog closes
describe('subscriber Overview draft price', () => {
  const DRAFT_ID = 'a0000000-0000-4000-8000-000000000002';
  const draft = {
    id: DRAFT_ID,
    state: 'draft',
    billingAccount: { id: A },
    requestedStartDate: '2026-10-06T09:00:00Z',
    productOrderItem: [
      { id: '1', action: 'add', productOffering: { id: O2, name: 'Draft Fiber' } },
    ],
  };

  it('shows a failed draft price with Retry, then the price', async () => {
    let fail = true;
    backend({
      [`GET ${ORDERS}?billingAccount.id=${A}&state=draft&offset=0&limit=50`]: () =>
        json([draft], { total: 1 }),
      [`GET /tmf-api/productCatalogManagement/v5/productOffering/${O2}`]: () =>
        fail
          ? tmfError(503, 'catalog unavailable')
          : json({
              id: O2,
              name: 'Draft Fiber',
              lifecycleStatus: 'Active',
              productOfferingPrice: [{ id: 'draft-price' }],
            }),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice/draft-price': () =>
        json({
          id: 'draft-price',
          name: 'Draft rate',
          priceType: 'recurring',
          price: usd(777),
          validFor: { startDateTime: '2026-10-05T21:00:00Z' },
        }),
    });
    await open();
    const box = expectUi('ui.subscriber.unfinishedOrders');
    expect(await within(box).findByText('Failed to load draft price')).toBeTruthy();
    fail = false;
    fireEvent.click(within(box).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(within(box).queryByText('Failed to load draft price')).toBeNull());
    expect(await within(box).findByText('777.00')).toBeTruthy();
  });

  it('closes an order opened from the Overview', async () => {
    backend({
      [`GET ${ORDERS}?billingAccount.id=${A}&state=draft&offset=0&limit=50`]: () =>
        json([draft], { total: 1 }),
      [`GET ${ORDERS}/${DRAFT_ID}`]: () => json(draft),
      'GET /*': pending,
    });
    await open();
    const box = expectUi('ui.subscriber.unfinishedOrders');
    fireEvent.click(await within(box).findByRole('button', { name: 'Open' }));
    const dialog = await screen.findByRole('dialog', { name: /Order/ });
    // the footer's Close (the header has a close icon of the same name)
    fireEvent.click(within(dialog).getByText('Close'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
