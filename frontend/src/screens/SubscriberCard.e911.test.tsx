import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, pending, tmfError, type Handler } from '../test/fetch';
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
  P1,
  backend,
  open,
  product,
  productAction,
  productRow,
  usd,
} from '../test/subscriberCardKit';

// The E911 address of Wi-Fi calling lines (#384, docs/decisions/network.md): Add product's
// E911 address group, the product row's address, Edit E911 address (a modify order with only
// product.place, PIN header when typed) and the pending change until the network confirms.
// The order bodies are replayed against the API in tests/resources/test_e911.py
// (test_e911_address_orders_as_the_ui_sends_them).

const SERVICE = {
  '@type': 'Contact',
  contactType: 'serviceAddress',
  contactMedium: [
    {
      '@type': 'GeographicAddressContactMedium',
      street1: '1 Market St',
      street2: 'Suite 5',
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
      accountBalance: [{ amount: usd(900), '@type': 'AccountBalance' }],
      contact,
    },
  );
const BUCKETS = `GET /tmf-api/prepayBalanceManagement/v4/bucket?partyAccount.id=${A}&offset=0&limit=100`;
const bucket = () =>
  json(
    [
      {
        id: BUCKET,
        remainingValue: { amount: 900, units: 'USD' },
        reservedValue: { amount: 0, units: 'USD' },
      },
    ],
    { total: 1 },
  );
const OFFERINGS =
  'GET /tmf-api/productCatalogManagement/v5/productOffering?lifecycleStatus=Active&isSellable=true&offset=0&limit=100';
const PRICES =
  'GET /tmf-api/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100';
const SPEC = 'GET /tmf-api/productCatalogManagement/v5/productSpecification/spec-wifi';
const wifiUse = (value: number) => ({
  '@type': 'ProductSpecificationCharacteristicValueUse',
  name: 'wifiCalling',
  valueType: 'integer',
  productSpecCharacteristicValue: [
    { '@type': 'IntegerCharacteristicValueSpecification', valueType: 'integer', value },
  ],
});
const offer = (over: Record<string, unknown> = {}) => ({
  id: O2,
  name: 'Wi-Fi Talk',
  productSpecification: { id: 'spec-wifi', name: 'Mobile' },
  productOfferingPrice: [{ id: 'pr2' }],
  ...over,
});
const spec = (wifiValues: number[]) => ({
  id: 'spec-wifi',
  productSpecCharacteristic: [
    {
      name: 'wifiCalling',
      valueType: 'integer',
      characteristicValueSpecification: wifiValues.map((value) => ({ value })),
    },
  ],
});
const FUNDED = {
  ...FIRST_CHARGE,
  period: { start: '2026-10-01', end: '2026-11-01' },
  prorated: false,
  billableDays: 31,
  periodDays: 31,
  amount: usd(30),
  next: null,
  balance: usd(900),
  balanceAfter: usd(870),
  creditLimit: null,
  exceedsCreditLimit: false,
  required: usd(30),
  available: usd(900),
  shortfall: usd(0),
};
const OFFICE = {
  streetNr: '742',
  streetName: 'Evergreen',
  streetSuffix: 'Terrace',
  city: 'Springfield',
  stateOrProvince: 'OR',
  postcode: '97403',
  country: 'US',
};

function addBackend(
  contact: object[],
  routes: Record<string, Handler> = {},
  offering: object = offer(),
  wifiValues = [1, 0],
) {
  return backend(
    {
      [BUCKETS]: bucket,
      [OFFERINGS]: () => json([offering], { total: 1 }),
      [PRICES]: () =>
        json([{ id: 'pr2', price: usd(30), validFor: { startDateTime: '2026-06-01T21:00:00Z' } }], {
          total: 1,
        }),
      [SPEC]: () => json(spec(wifiValues)),
      [`GET ${INTERNAL}/firstCharge?productOffering.id=${O2}&startDate=2026-10-01`]: () =>
        json(FUNDED),
      ...routes,
    },
    prepaid(contact),
  );
}

async function chooseOffering() {
  await open();
  fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
  const dialog = screen.getByRole('dialog', { name: 'Add product' });
  fireEvent.click((await within(dialog).findAllByRole('radio'))[0] as HTMLElement);
  return dialog;
}

const type = (dialog: HTMLElement, label: string, value: string) =>
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });

function posts(fetchMock: ReturnType<typeof backend>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([, init]) => ({
      body: JSON.parse(String(init?.body)) as { productOrderItem: Record<string, unknown>[] },
      pin: (init?.headers as Record<string, string> | undefined)?.['X-Account-Pin'],
    }));
}

/** The API's 422 (app/api/errors.py): the problems by path in `message`. */
const unprocessable = (message: string) =>
  json(
    { '@type': 'Error', code: '422', reason: 'Unprocessable Content', message, status: '422' },
    { status: 422 },
  );

const COMPLETED = () =>
  json(
    { id: 'o1', state: 'completed', '@type': 'ProductOrder', productOrderItem: [] },
    { status: 201 },
  );

describe('E911 address in Add product', () => {
  it('defaults to the service address and then sends no place (the server copies it)', async () => {
    const { fetchMock } = addBackend([SERVICE], { [`POST ${ORDERS}`]: COMPLETED });
    const dialog = await chooseOffering();
    const group = await waitFor(() => expectUi('ui.addProduct.e911'));
    const same = within(group).getByLabelText('Same as service address') as HTMLInputElement;
    expect(same.checked).toBe(true);
    const street = within(group).getByLabelText('Street') as HTMLInputElement;
    expect(street.value).toBe('1 Market St');
    expect(street.readOnly).toBe(true);
    expect(
      (within(group).getByLabelText('Apartment, suite or floor') as HTMLInputElement).value,
    ).toBe('Suite 5');
    const add = within(dialog).getByRole('button', { name: 'Add Wi-Fi Talk' }) as HTMLButtonElement;
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(await screen.findByText(/Wi-Fi Talk ordered/)).toBeTruthy();
    const sent = posts(fetchMock);
    expect(sent).toHaveLength(1);
    const body = sent[0]?.body;
    expect(body?.productOrderItem[0]).not.toHaveProperty('product');
  });

  it('sends a typed address as product.place of the add item', async () => {
    const { fetchMock } = addBackend([SERVICE], { [`POST ${ORDERS}`]: COMPLETED });
    const dialog = await chooseOffering();
    const group = await waitFor(() => expectUi('ui.addProduct.e911'));
    fireEvent.click(within(group).getByLabelText('Same as service address'));
    const street = within(group).getByLabelText('Street') as HTMLInputElement;
    expect(street.readOnly).toBe(false);
    expect(street.value).toBe('1 Market St'); // prefilled, then edited
    type(group, 'Street number', '742');
    type(group, 'Street', ' Evergreen ');
    type(group, 'Street suffix', 'Terrace');
    type(group, 'Apartment, suite or floor', '');
    type(group, 'City', 'Springfield');
    fireEvent.change(within(group).getByLabelText('State'), { target: { value: 'OR' } });
    type(group, 'ZIP code', '97403');
    const add = within(dialog).getByRole('button', { name: 'Add Wi-Fi Talk' }) as HTMLButtonElement;
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(await screen.findByText(/Wi-Fi Talk ordered/)).toBeTruthy();
    const sent = posts(fetchMock);
    expect(sent).toHaveLength(1);
    const body = sent[0]?.body;
    expect(body?.productOrderItem[0]).toEqual({
      '@type': 'ProductOrderItem',
      id: '1',
      action: 'add',
      productOffering: { id: O2, '@type': 'ProductOfferingRef' },
      product: {
        '@type': 'ProductRefOrValue',
        place: [
          {
            '@type': 'RelatedPlaceRefOrValue',
            role: 'e911Address',
            place: { '@type': 'GeographicAddress', ...OFFICE },
          },
        ],
      },
    });
  });

  it('asks for an address when the account has none and shows a 422 at its input', async () => {
    const { fetchMock } = addBackend([], {
      [`POST ${ORDERS}`]: () =>
        unprocessable(
          'productOrderItem.0.product.place.0.place.postcode: postcode must be a ZIP (12345) or ZIP+4 (12345-6789)',
        ),
    });
    const dialog = await chooseOffering();
    const group = await waitFor(() => expectUi('ui.addProduct.e911'));
    const same = within(group).getByLabelText('Same as service address') as HTMLInputElement;
    expect(same.checked).toBe(false);
    expect(same.disabled).toBe(true);
    expect(group.textContent).toContain('The account has no service address');
    const add = within(dialog).getByRole('button', { name: 'Add Wi-Fi Talk' }) as HTMLButtonElement;
    await within(dialog).findByText(/Paid from the balance/);
    expect(add.disabled).toBe(true);
    type(group, 'Street', 'Main St');
    type(group, 'City', 'Albany');
    fireEvent.change(within(group).getByLabelText('State'), { target: { value: 'NY' } });
    type(group, 'ZIP code', '1220');
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(
      await within(group).findByText('postcode must be a ZIP (12345) or ZIP+4 (12345-6789)'),
    ).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Add product' })).toBeTruthy();
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it('shows the e911AddressRequired refusal with its copy', async () => {
    addBackend([SERVICE], {
      [`POST ${ORDERS}`]: () =>
        json(
          {
            id: 'o2',
            state: 'rejected',
            '@type': 'ProductOrder',
            productOrderErrorMessage: [
              { code: 'e911AddressRequired', reason: 'a Wi-Fi calling line needs an E911 address' },
            ],
          },
          { status: 201 },
        ),
    });
    const dialog = await chooseOffering();
    await waitFor(() => expectUi('ui.addProduct.e911'));
    const add = within(dialog).getByRole('button', { name: 'Add Wi-Fi Talk' }) as HTMLButtonElement;
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);
    expect(
      await within(dialog).findByText(/A Wi-Fi calling line needs an E911 address: send one/),
    ).toBeTruthy();
  });

  it('asks nothing when the offering narrows Wi-Fi calling off', async () => {
    addBackend([SERVICE], {}, offer({ prodSpecCharValueUse: [wifiUse(0)] }));
    const dialog = await chooseOffering();
    await within(dialog).findByText(/Paid from the balance/);
    expect(document.querySelector('[data-ui="ui.addProduct.e911"]')).toBeNull();
  });

  it("follows the offering's narrowing over the specification default", async () => {
    addBackend([SERVICE], {}, offer({ prodSpecCharValueUse: [wifiUse(1)] }), [0, 1]);
    await chooseOffering();
    await waitFor(() => expectUi('ui.addProduct.e911'));
  });
});

const LINE = product(P1, {
  productOffering: { id: O2, name: 'Wi-Fi Talk', code: 'WIFI' },
  wifiCalling: true,
  e911Address: {
    ...OFFICE,
    geographicSubAddress: [{ '@type': 'GeographicSubAddress', name: 'Apt 4' }],
  },
});

function cardBackend(products: object[], routes: Record<string, Handler> = {}) {
  return backend(
    {
      [BUCKETS]: bucket,
      [`GET ${INTERNAL}/products`]: () => json(products, { total: products.length }),
      ...routes,
    },
    prepaid([SERVICE]),
  );
}

describe('E911 address on the subscriber card', () => {
  it('shows the address of a Wi-Fi line and nothing on a line without one', async () => {
    cardBackend([
      LINE,
      product('p-plain', { productOffering: { id: 'o9', name: 'Data only', code: null } }),
    ]);
    await open();
    const row = await waitFor(() => productRow('Wi-Fi Talk'));
    expect(within(row).getByText(/^E911:/).textContent).toBe(
      'E911: 742 Evergreen Terrace, Apt 4, Springfield, OR 97403',
    );
    expectUi('ui.subscriber.e911');
    expect(within(productRow('Data only')).queryByText(/^E911:/)).toBeNull();
    expect(() => productAction(productRow('Data only'), 'Edit E911 address…')).toThrow();
  });

  it('says a Wi-Fi line has no address registered', async () => {
    cardBackend([{ ...LINE, e911Address: null }]);
    await open();
    const row = await waitFor(() => productRow('Wi-Fi Talk'));
    expect(within(row).getByText('E911: no address registered')).toBeTruthy();
  });

  it('changes it with a modify item carrying only the place, the PIN as a header', async () => {
    const { fetchMock } = cardBackend([LINE], {
      [`POST ${ORDERS}`]: () =>
        json({ id: 'o-e911', state: 'inProgress', '@type': 'ProductOrder' }, { status: 201 }),
      [`GET ${ORDERS}/o-e911`]: pending,
      'GET /internal/v1/prepaid/command/o-e911': pending,
    });
    await open();
    const row = await waitFor(() => productRow('Wi-Fi Talk'));
    const item = productAction(row, 'Edit E911 address…');
    expect(item.getAttribute('data-ui')).toBe('ui.subscriber.e911.edit');
    fireEvent.click(item);
    const dialog = screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' });
    expectUi('ui.subscriber.e911.form');
    expect((within(dialog).getByLabelText('Street') as HTMLInputElement).value).toBe('Evergreen');
    expect(
      (within(dialog).getByLabelText('Apartment, suite or floor') as HTMLInputElement).value,
    ).toBe('Apt 4');
    type(dialog, 'Street number', '1');
    type(dialog, 'Street', 'Market');
    type(dialog, 'Street suffix', 'St');
    type(dialog, 'Apartment, suite or floor', ' ');
    type(dialog, 'City', 'San Francisco');
    fireEvent.change(within(dialog).getByLabelText('State'), { target: { value: 'CA' } });
    type(dialog, 'ZIP code', '94105-1234');
    type(dialog, 'Account PIN', '482913');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save E911 address' }));
    expect(
      await screen.findByText(
        'Wi-Fi Talk: E911 address change accepted, it takes effect when the network confirms',
      ),
    ).toBeTruthy();
    expect(posts(fetchMock)).toEqual([
      {
        pin: '482913',
        body: {
          '@type': 'ProductOrder',
          billingAccount: { id: A, '@type': 'BillingAccountRef' },
          productOrderItem: [
            {
              '@type': 'ProductOrderItem',
              id: '1',
              action: 'modify',
              product: {
                '@type': 'ProductRefOrValue',
                id: P1,
                place: [
                  {
                    '@type': 'RelatedPlaceRefOrValue',
                    role: 'e911Address',
                    place: {
                      '@type': 'GeographicAddress',
                      streetNr: '1',
                      streetName: 'Market',
                      streetSuffix: 'St',
                      city: 'San Francisco',
                      stateOrProvince: 'CA',
                      postcode: '94105-1234',
                      country: 'US',
                    },
                  },
                ],
              },
            },
          ],
        },
      },
    ]);
  });

  it('keeps the dialog open with a refusal, and a 422 at the input', async () => {
    let answer = 0;
    cardBackend([LINE], {
      [`POST ${ORDERS}`]: () =>
        ++answer === 1
          ? unprocessable(
              'productOrderItem.0.product.place.0.place.city: city is required; productOrderItem.0.product.place.0.place.country: country must be US',
            )
          : json(
              {
                id: 'o3',
                state: 'rejected',
                '@type': 'ProductOrder',
                productOrderErrorMessage: [
                  { code: 'pinRequired', reason: 'the account PIN is required' },
                ],
              },
              { status: 201 },
            ),
    });
    await open();
    fireEvent.click(
      productAction(await waitFor(() => productRow('Wi-Fi Talk')), 'Edit E911 address…'),
    );
    const dialog = screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' });
    const save = within(dialog).getByRole('button', { name: 'Save E911 address' });
    fireEvent.click(save);
    expect(await within(dialog).findByText('city is required')).toBeTruthy();
    expect(within(dialog).getByText(/country must be US/)).toBeTruthy();
    fireEvent.click(save);
    await waitFor(() => expect(within(dialog).queryByText('city is required')).toBeNull());
    expect(await within(dialog).findByText(/Ask the customer for their account PIN/)).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' })).toBeTruthy();
  });

  it('shows the unconfirmed change on the pending-change line, without a second edit', async () => {
    cardBackend([
      {
        ...LINE,
        pendingChanges: [
          {
            kind: 'e911',
            date: '2026-10-01',
            e911Address: {
              streetName: 'Market',
              city: 'San Francisco',
              stateOrProvince: 'CA',
              postcode: '94105',
              country: 'US',
            },
            productOrder: { id: 'o-e911', href: `${ORDERS}/o-e911` },
          },
        ],
      },
    ]);
    await open();
    const row = await waitFor(() => productRow('Wi-Fi Talk'));
    const line = within(row).getByText(/^E911 address changes to/);
    expect(line.textContent).toBe(
      'E911 address changes to Market, San Francisco, CA 94105 when the network confirms',
    );
    expect(line.closest('[data-ui="ui.subscriber.pendingChange"]')).toBeTruthy();
    expect(within(row).getByText(/^E911:/).textContent).toContain('742 Evergreen Terrace');
    expect(() => productAction(row, 'Edit E911 address…')).toThrow();
  });

  it('shows and offers nothing on a postpaid account', async () => {
    backend({
      [`GET ${INTERNAL}/products`]: () => json([{ ...LINE, e911Address: null }], { total: 1 }),
    });
    await open();
    const row = await waitFor(() => productRow('Wi-Fi Talk'));
    expect(within(row).queryByText(/^E911:/)).toBeNull();
    expect(() => productAction(row, 'Edit E911 address…')).toThrow();
  });

  it('shows and offers nothing on an add-on pack that enables Wi-Fi calling', async () => {
    cardBackend([
      {
        ...LINE,
        e911Address: null,
        offeringKind: 'addOn',
        productOffering: { id: 'o-pack', name: 'Wi-Fi pack', code: null },
      },
    ]);
    await open();
    const row = await waitFor(() => productRow('Wi-Fi pack'));
    expect(within(row).queryByText(/^E911:/)).toBeNull();
    expect(() => productAction(row, 'Edit E911 address…')).toThrow();
  });
});

// #498: a completed change, a plain refusal, an unknown outcome and Escape
describe('E911 address change outcomes', () => {
  async function editDialog() {
    await open();
    fireEvent.click(
      productAction(await waitFor(() => productRow('Wi-Fi Talk')), 'Edit E911 address…'),
    );
    return screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' });
  }

  it('closes on a completed change and says so', async () => {
    const { fetchMock } = cardBackend([LINE], { [`POST ${ORDERS}`]: COMPLETED });
    const dialog = await editDialog();
    type(dialog, 'City', 'Eugene');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save E911 address' }));
    expect(await screen.findByText('Wi-Fi Talk: E911 address changed')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    const [sent] = posts(fetchMock);
    const item = sent?.body.productOrderItem[0] as {
      product: { place: { place: { city: string } }[] };
    };
    expect(item.product.place[0]?.place.city).toBe('Eugene');
    expect(sent?.pin).toBeUndefined();
  });

  it('shows a refusal that names no field and keeps the typed address', async () => {
    cardBackend([LINE], {
      [`POST ${ORDERS}`]: () => tmfError(409, 'another order already changes this product'),
    });
    const dialog = await editDialog();
    type(dialog, 'City', 'Eugene');
    const save = within(dialog).getByRole('button', { name: 'Save E911 address' });
    fireEvent.click(save);
    const refusal = await within(dialog).findByText(/another order already changes this product/);
    expect(refusal.closest('.bss-note')?.textContent).toContain('Refused.');
    expect((within(dialog).getByLabelText('City') as HTMLInputElement).value).toBe('Eugene');
    expect((save as HTMLButtonElement).disabled).toBe(false);
  });

  it('says an unanswered change may have happened and Refresh reads the card again', async () => {
    let reads = 0;
    const { fetchMock } = cardBackend([LINE], {
      [`GET ${INTERNAL}/products`]: () => {
        reads += 1;
        return json([LINE], { total: 1 });
      },
      [`POST ${ORDERS}`]: () => tmfError(502, 'bad gateway'),
    });
    const dialog = await editDialog();
    const before = reads;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save E911 address' }));
    expect(await within(dialog).findByText('Unsure whether it was saved.')).toBeTruthy();
    // no second Save while the outcome is unknown
    expect(within(dialog).queryByRole('button', { name: 'Save E911 address' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(reads).toBeGreaterThan(before));
    expect(posts(fetchMock)).toHaveLength(1);
  });

  it('closes on Escape, but not while the change is being sent', async () => {
    let answer: (response: Response) => void = () => {};
    cardBackend([LINE], {
      [`POST ${ORDERS}`]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    await editDialog();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(productAction(productRow('Wi-Fi Talk'), 'Edit E911 address…'));
    const dialog = screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save E911 address' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('dialog', { name: 'Edit E911 address: Wi-Fi Talk' })).toBe(dialog);
    answer(COMPLETED());
    expect(await screen.findByText('Wi-Fi Talk: E911 address changed')).toBeTruthy();
  });
});
