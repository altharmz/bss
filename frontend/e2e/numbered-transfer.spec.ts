import { expect, test } from '@playwright/test';
import { signIn } from './auth';

// G4b / P13: written now; R16 runs this against the disposable local seeded stack
// with its billing worker running. All setup uses the public APIs; no staging data.
test('numbered prepaid subscribers complete a transfer by subscriber number', async ({ page }) => {
  test.setTimeout(360_000);
  await page.goto('/catalog');
  await signIn(page);
  const suffix = Date.now().toString();
  const name = `Numbered prepaid ${suffix}`;
  const catalog = '/tmf-api/productCatalogManagement/v5';
  const post = async (path: string, data: Record<string, unknown>) => {
    const response = await page.request.post(path, { data, headers: { 'X-Bss-Request': '1' } });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()) as { id: string; href: string };
  };
  const context = (await (await page.request.get('/internal/v1/context')).json()) as {
    currency: string;
  };
  await page.getByRole('button', { name: 'Specifications', exact: true }).click();
  await page.getByRole('button', { name: '+ New specification' }).click();
  const editor = page.getByRole('dialog', { name: 'New product specification' });
  await editor.getByLabel('Name', { exact: true }).fill(name);
  await editor.getByLabel('Requires a subscriber number').check();
  await editor.getByLabel('Lifecycle status').selectOption('Active');
  const specResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/productSpecification') && response.request().method() === 'POST',
  );
  await editor.getByRole('button', { name: 'Create', exact: true }).click();
  const spec = (await (await specResponse).json()) as { id: string };
  await expect(editor).toBeHidden();
  const price = await post(`${catalog}/productOfferingPrice`, {
    '@type': 'ProductOfferingPrice',
    name,
    lifecycleStatus: 'Active',
    priceType: 'recurring',
    recurringChargePeriodType: 'monthly',
    price: { unit: context.currency, value: 1 },
  });
  const offering = await post(`${catalog}/productOffering`, {
    '@type': 'ProductOffering',
    name,
    lifecycleStatus: 'Active',
    isSellable: true,
    productSpecification: { id: spec.id, '@type': 'ProductSpecificationRef' },
    productOfferingPrice: [{ id: price.id, '@type': 'ProductOfferingPriceRef' }],
  });
  const accounts: string[] = [];
  const digits = [`${suffix}0`, `${suffix}1`];
  for (const [index, value] of digits.entries()) {
    const party = await post('/tmf-api/partyManagement/v5/individual', {
      '@type': 'Individual',
      givenName: 'Numbered',
      familyName: `${suffix}-${index}`,
    });
    const customer = await post('/tmf-api/customerManagement/v5/customer', {
      '@type': 'Customer',
      name: `${name} ${index}`,
      status: 'Active',
      engagedParty: { id: party.id, '@type': 'PartyRef', '@referredType': 'Individual' },
    });
    const account = await post('/tmf-api/accountManagement/v5/billingAccount', {
      '@type': 'BssBillingAccount',
      name: `${name} ${index}`,
      billCycleDay: 1,
      currency: context.currency,
      ratingType: 'prepaid',
      relatedParty: [
        {
          '@type': 'RelatedPartyRefOrPartyRoleRef',
          role: 'customer',
          partyOrPartyRole: {
            id: customer.id,
            '@type': 'PartyRoleRef',
            '@referredType': 'Customer',
          },
        },
      ],
    });
    const number = await post('/tmf-api/resourceInventory/v5/resource', {
      '@type': 'LogicalResource',
      name: `MSISDN ${value}`,
      value,
      administrativeState: 'unlocked',
      resourceSpecification: { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
    });
    accounts.push(account.id);
    await page.goto(`/subscribers/${account.id}`);
    await page.getByRole('button', { name: 'Top up', exact: true }).first().click();
    const topup = page.getByRole('dialog', { name: 'Top up' });
    await topup.getByLabel('Amount').fill('20.00');
    await topup.getByRole('button', { name: 'cash', exact: true }).click();
    await topup.getByRole('button', { name: 'Top up', exact: true }).click();
    await expect(topup).toBeHidden();
    await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
    const add = page.getByRole('dialog', { name: 'Add product' });
    await add.getByRole('radio').filter({ hasText: name }).click();
    await add.getByLabel('Subscriber number', { exact: true }).selectOption(number.id);
    const orderResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith('/productOrder') && response.request().method() === 'POST',
    );
    await add.getByRole('button', { name: 'Activate', exact: true }).click();
    const order = (await (await orderResponse).json()) as { id: string };
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `/tmf-api/productOrderingManagement/v5/productOrder/${order.id}`,
          );
          return ((await response.json()) as { state: string }).state;
        },
        { timeout: 90_000, intervals: [1000, 5000] },
      )
      .toBe('completed');
    expect(offering.id).toBeTruthy();
  }
  await page.goto(`/subscribers/${accounts[0]}`);
  await page.getByRole('button', { name: 'Transfer', exact: true }).click();
  const transfer = page.getByRole('dialog', { name: 'Transfer funds' });
  await expect(transfer.getByRole('button', { name: digits[0] ?? '', exact: true })).toBeVisible();
  await transfer.getByLabel('Receiver number').fill(digits[1] ?? '');
  await transfer.getByRole('button', { name: 'Find', exact: true }).click();
  await expect(transfer.getByText(`${name} 1`, { exact: false })).toBeVisible();
  await transfer.getByLabel('Amount', { exact: true }).fill('5.00');
  await transfer.getByLabel('Reason').fill('Numbered transfer acceptance');
  await transfer.getByRole('button', { name: 'Review transfer' }).click();
  // R11 adds an explicit acknowledgement; keep the scenario valid after that slice.
  const acknowledgement = transfer.getByRole('checkbox', {
    name: /I confirmed the amount and the recipient.s number with the subscriber/,
  });
  if (await acknowledgement.count()) await acknowledgement.check();
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith('/transferBalance') && response.request().method() === 'POST',
  );
  await transfer.getByRole('button', { name: 'Confirm transfer' }).click();
  const action = (await (await accepted).json()) as { id: string; status: string };
  expect(action.status).toBe('created');
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/tmf-api/prepayBalanceManagement/v4/transferBalance/${action.id}`,
        );
        return ((await response.json()) as { status: string }).status;
      },
      { timeout: 90_000, intervals: [1000, 5000] },
    )
    .toBe('completed');
});
