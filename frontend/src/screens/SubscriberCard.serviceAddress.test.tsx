import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, tmfCodeError, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { accountBody } from '../test/fixtures';
import {
  A,
  BUCKET,
  C,
  FIRST_CHARGE,
  INTERNAL,
  O2,
  ORDERS,
  backend,
  open,
  selectTab,
  usd,
} from '../test/subscriberCardKit';

// The service address of the billing account (#207, docs/decisions/taxes.md): set, change and
// remove send the account's whole contact array; a 422 shows at the input; the PIN header is
// sent only when typed. The same bodies are replayed against the API in
// tests/internal/test_ui_requests.py (test_service_address_writes_as_the_ui_sends_them).

const PATCH = `PATCH /tmf-api/accountManagement/v5/billingAccount/${A}`;
const BILLING_CONTACT = {
  '@type': 'Contact',
  contactType: 'billing',
  contactName: 'Accounts desk',
  contactMedium: [{ '@type': 'EmailContactMedium', emailAddress: 'desk@example.com' }],
};
const CA = {
  '@type': 'Contact',
  contactType: 'serviceAddress',
  contactMedium: [
    {
      '@type': 'GeographicAddressContactMedium',
      street1: '1 Market St',
      city: 'San Francisco',
      stateOrProvince: 'CA',
      postCode: '94105',
      country: 'US',
    },
  ],
};
const prepaid = (contact: object[]) =>
  accountBody(
    A,
    { id: C, name: 'Olga Kuznetsova' },
    {
      ratingType: 'prepaid',
      accountBalance: [{ amount: usd(120), '@type': 'AccountBalance' }],
      contact,
    },
  );
const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
const bucket = () =>
  json(
    [
      {
        id: BUCKET,
        remainingValue: { amount: 100, units: 'USD' },
        reservedValue: { amount: 0, units: 'USD' },
      },
    ],
    { total: 1 },
  );

function card(contact: object[], routes: Record<string, Handler> = {}) {
  return backend({ [BUCKETS]: bucket, ...routes }, prepaid(contact));
}

function patches(fetchMock: ReturnType<typeof backend>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PATCH')
    .map(([, init]) => ({
      body: JSON.parse(String(init?.body)) as unknown,
      pin: (init?.headers as Record<string, string>)['X-Account-Pin'],
    }));
}

const type = (dialog: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

describe('service address', () => {
  it('warns on a prepaid card without one and shows none on the Account tab', async () => {
    card([BILLING_CONTACT]);
    await open();
    const missing = expectUi('ui.subscriber.serviceAddress.missing');
    expect(missing.textContent).toContain(
      'No service address: new prepaid lines are refused and charges are taxed at the flat rate',
    );
    selectTab('Account');
    const panel = expectUi('ui.subscriber.serviceAddress');
    expect(within(panel).getByText('—')).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Set service address' })).toBeTruthy();
    expect(within(panel).queryByRole('button', { name: 'Remove' })).toBeNull();
  });

  it('shows the address and no warning once it is set', async () => {
    card([CA]);
    await open();
    expect(document.querySelector('[data-ui="ui.subscriber.serviceAddress.missing"]')).toBeNull();
    selectTab('Account');
    const panel = expectUi('ui.subscriber.serviceAddress');
    expect(panel.textContent).toContain('1 Market St');
    expect(panel.textContent).toContain('San Francisco, CA 94105');
  });

  it('sets it: the other contacts go back as loaded, no PIN header when none is typed', async () => {
    const { fetchMock } = card([BILLING_CONTACT], { [PATCH]: () => json(prepaid([])) });
    await open();
    fireEvent.click(
      within(expectUi('ui.subscriber.serviceAddress.missing')).getByRole('button', {
        name: 'Set service address…',
      }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Set service address' });
    expectUi('ui.subscriber.serviceAddress.edit');
    expectUi('ui.subscriber.serviceAddress.form');
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect((within(dialog).getByLabelText('Country') as HTMLInputElement).readOnly).toBe(true);
    type(dialog, 'Street', ' 5 Main St ');
    type(dialog, 'City', 'Albany');
    fireEvent.change(within(dialog).getByLabelText('State'), { target: { value: 'NY' } });
    type(dialog, 'ZIP code', '12207-1234');
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    expect(await screen.findByText('Service address set')).toBeTruthy();
    expect(patches(fetchMock)).toEqual([
      {
        body: {
          contact: [
            BILLING_CONTACT,
            {
              '@type': 'Contact',
              contactType: 'serviceAddress',
              contactMedium: [
                {
                  '@type': 'GeographicAddressContactMedium',
                  street1: '5 Main St',
                  city: 'Albany',
                  stateOrProvince: 'NY',
                  postCode: '12207-1234',
                  country: 'US',
                },
              ],
            },
          ],
        },
        pin: undefined,
      },
    ]);
  });

  it('changes it in place with the typed PIN', async () => {
    const { fetchMock } = card([CA, BILLING_CONTACT], { [PATCH]: () => json(prepaid([])) });
    await open();
    selectTab('Account');
    fireEvent.click(
      within(expectUi('ui.subscriber.serviceAddress')).getByRole('button', {
        name: 'Edit service address',
      }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Edit service address' });
    expect((within(dialog).getByLabelText('Street') as HTMLInputElement).value).toBe('1 Market St');
    type(dialog, 'Street, line 2', 'Suite 200');
    type(dialog, 'Account PIN', ' 123456 ');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Service address changed')).toBeTruthy();
    const medium = { ...CA.contactMedium[0], street2: 'Suite 200' };
    expect(patches(fetchMock)).toEqual([
      {
        body: {
          contact: [
            {
              '@type': 'Contact',
              contactType: 'serviceAddress',
              contactMedium: [
                {
                  '@type': medium['@type'],
                  street1: medium.street1,
                  street2: medium.street2,
                  city: medium.city,
                  stateOrProvince: medium.stateOrProvince,
                  postCode: medium.postCode,
                  country: 'US',
                },
              ],
            },
            BILLING_CONTACT,
          ],
        },
        pin: '123456',
      },
    ]);
  });

  it('removes it after a confirmation', async () => {
    const { fetchMock } = card([BILLING_CONTACT, CA], { [PATCH]: () => json(prepaid([])) });
    await open();
    selectTab('Account');
    fireEvent.click(
      within(expectUi('ui.subscriber.serviceAddress')).getByRole('button', { name: 'Remove' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Remove service address' });
    expectUi('ui.subscriber.serviceAddress.remove');
    expect(patches(fetchMock)).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    expect(await screen.findByText('Service address removed')).toBeTruthy();
    expect(patches(fetchMock)).toEqual([{ body: { contact: [BILLING_CONTACT] }, pin: undefined }]);
  });

  it('shows a 422 at the input and keeps what was typed', async () => {
    card([BILLING_CONTACT], {
      [PATCH]: () =>
        json(
          {
            '@type': 'Error',
            code: '422',
            reason: 'Unprocessable Entity',
            message:
              'contact.1.contactMedium.0: postCode must be a ZIP (12345) or ZIP+4 (12345-6789)',
            status: '422',
          },
          { status: 422 },
        ),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Set service address…' }));
    const dialog = screen.getByRole('dialog', { name: 'Set service address' });
    type(dialog, 'Street', '5 Main St');
    type(dialog, 'City', 'Albany');
    fireEvent.change(within(dialog).getByLabelText('State'), { target: { value: 'NY' } });
    type(dialog, 'ZIP code', '1220');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(
      await within(dialog).findByText('postCode must be a ZIP (12345) or ZIP+4 (12345-6789)'),
    ).toBeTruthy();
    expect(within(dialog).getByLabelText('ZIP code').getAttribute('aria-invalid')).toBe('true');
    expect(within(dialog).getByText('Correct the marked fields.')).toBeTruthy();
    expect((within(dialog).getByLabelText('City') as HTMLInputElement).value).toBe('Albany');
    // typing in the input clears its error
    type(dialog, 'ZIP code', '12207');
    expect(within(dialog).queryByText(/postCode must be/)).toBeNull();
  });

  it('explains a PIN refusal with the mapped sentence', async () => {
    card([CA], {
      [PATCH]: () => tmfCodeError(403, 'pinRequired', 'the account PIN is required'),
    });
    await open();
    selectTab('Account');
    fireEvent.click(screen.getByRole('button', { name: 'Edit service address' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit service address' });
    type(dialog, 'City', 'Oakland');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(
      await within(dialog).findByText(
        'Ask the customer for their account PIN and enter it in the Account PIN field.',
      ),
    ).toBeTruthy();
  });
});

describe('serviceAddressRequired refusal', () => {
  it('reads as the mapped explanation and opens the service address dialog', async () => {
    card([], {
      'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100':
        () =>
          json(
            [
              {
                id: O2,
                name: 'Fiber 100',
                productSpecification: { name: 'Home Internet' },
                productOfferingPrice: [{ id: 'pr2' }],
              },
            ],
            { total: 1 },
          ),
      'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100':
        () =>
          json(
            [{ id: 'pr2', price: usd(650), validFor: { startDateTime: '2026-06-01T21:00:00Z' } }],
            { total: 1 },
          ),
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=2026-10-01`]: () =>
        json(FIRST_CHARGE),
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o1',
            state: 'rejected',
            productOrderErrorMessage: [
              { code: 'serviceAddressRequired', reason: 'the billing account needs one' },
            ],
            '@type': 'ProductOrder',
          },
          { status: 201 },
        ),
    });
    await open();
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    const dialog = screen.getByRole('dialog', { name: 'Add product' });
    fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
    const add = await within(dialog).findByRole('button', { name: 'Add Fiber 100' });
    await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(add);
    expect(
      await within(dialog).findByText(
        'The billing account needs a service address before a prepaid line can be activated.',
      ),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set service address…' }));
    expect(screen.getByRole('dialog', { name: 'Set service address' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Add product' })).toBeNull();
  });
});

// #498: an unanswered change or removal offers Refresh, which reads the account again
describe('service address with an unknown outcome', () => {
  it.each([
    ['Edit service address', 'Save'],
    ['Remove service address', 'Remove'],
  ])('%s: Refresh after no answer reads the account again', async (title, action) => {
    let reads = 0;
    const { fetchMock } = card([CA, BILLING_CONTACT], {
      [`GET /tmf-api/accountManagement/v5/billingAccount/${A}`]: () => {
        reads += 1;
        return json(prepaid([CA, BILLING_CONTACT]));
      },
      [PATCH]: () => tmfError(504, 'gateway timeout'),
    });
    await open();
    selectTab('Account');
    const panel = expectUi('ui.subscriber.serviceAddress');
    fireEvent.click(
      within(panel).getByRole('button', {
        name: title === 'Edit service address' ? 'Edit service address' : 'Remove',
      }),
    );
    const dialog = screen.getByRole('dialog', { name: title });
    if (action === 'Save') type(dialog, 'Street, line 2', 'Suite 200');
    fireEvent.click(within(dialog).getByRole('button', { name: action }));
    expect(await within(dialog).findByText('Unsure whether it was saved.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: action })).toBeNull();
    const before = reads;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Refreshed')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(reads).toBeGreaterThan(before));
    expect(patches(fetchMock)).toHaveLength(1);
  });
});
