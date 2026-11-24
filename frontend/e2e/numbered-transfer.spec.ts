import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { signIn } from './auth';

// Fresh local catalog fixture is priced at business midnight by the same clock
// mechanism as the seed. The browser attaches its UI-created numbered specification;
// purchases and transfer must complete through the real task-local billing worker.
test('numbered prepaid subscribers complete a transfer by subscriber number', async ({ page }) => {
  test.setTimeout(360_000);
  await page.goto('/catalog');
  await signIn(page);
  // Submitting credentials is asynchronous; wait for the authenticated screen
  // before using its session cookie for API fixture setup.
  await expect(page.getByRole('heading', { name: 'Product catalog', exact: true })).toBeVisible();
  const suffix = Date.now().toString();
  const name = `Numbered prepaid ${suffix}`;
  const catalog = '/tmf-api/productCatalogManagement/v5';
  const post = async (path: string, data: Record<string, unknown>) => {
    const response = await page.request.post(path, { data, headers: { 'X-Bss-Request': '1' } });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()) as { id: string; href: string };
  };
  const contextResponse = await page.request.get('/internal/v1/context');
  expect(contextResponse.status(), await contextResponse.text()).toBe(200);
  const context = (await contextResponse.json()) as {
    currency: string;
  };
  expect(context.currency).toMatch(/^[A-Z]{3}$/);
  const fixture = JSON.parse(readFileSync('/web/.e2e/numbered-offering.json', 'utf8')) as {
    id: string;
    currency: string;
  };
  expect(fixture.currency).toBe(context.currency);
  await page.getByRole('tab', { name: 'Specifications', exact: true }).click();
  await page.getByRole('button', { name: 'New specification' }).click();
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
  const offeringResponse = await page.request.patch(`${catalog}/productOffering/${fixture.id}`, {
    headers: { 'X-Bss-Request': '1', 'Content-Type': 'application/merge-patch+json' },
    data: {
      name,
      productSpecification: { id: spec.id, '@type': 'ProductSpecificationRef' },
    },
  });
  expect(offeringResponse.status(), await offeringResponse.text()).toBe(200);
  const offering = (await offeringResponse.json()) as {
    id: string;
    productSpecification: { id: string };
  };
  expect(offering.productSpecification.id).toBe(spec.id);
  const accounts: string[] = [];
  const digits = [`${suffix}0`, `${suffix}1`];
  for (const [index, value] of digits.entries()) {
    const party = await post('/tmf-api/partyManagement/v5/individual', {
      '@type': 'Individual',
      givenName: 'Numbered',
      familyName: `${suffix}-${index}`,
    });
    const customer = await post('/tmf-api/customer/v5/customer', {
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
      // a prepaid line needs a service address (BSS_TAX_REQUIRE_SERVICE_ADDRESS, on by
      // default); the same one the seed gives its prepaid accounts (tests/seed.py)
      contact: [
        {
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
        },
      ],
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
    await post('/tmf-api/resourceInventory/v5/resource', {
      '@type': 'LogicalResource',
      name: `MSISDN ${value}`,
      value,
      administrativeState: 'unlocked',
      resourceSpecification: { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
    });
    accounts.push(account.id);
    await page.goto(`/billing-accounts/${account.id}`);
    await page.getByRole('button', { name: 'Top up', exact: true }).first().click();
    const topup = page.getByRole('dialog', { name: 'Top up' });
    await topup.getByLabel('Amount').fill('20.00');
    await topup.getByRole('button', { name: 'Cash', exact: true }).click();
    await topup.getByRole('button', { name: /^Top up/ }).click();
    await expect(topup).toBeHidden();
    await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
    const add = page.getByRole('dialog', { name: 'Add product' });
    await add.getByRole('radio').filter({ hasText: name }).click();
    const picker = add.getByRole('combobox', { name: 'Subscriber number', exact: true });
    await expect(picker).toBeEnabled();
    await picker.fill(value);
    await add.getByRole('option', { name: value, exact: true }).click();
    const orderResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith('/productOrder') && response.request().method() === 'POST',
    );
    await add.getByRole('button', { name: /^Add / }).click();
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
  await page.goto(`/billing-accounts/${accounts[0]}`);
  await page.getByRole('button', { name: 'Transfer', exact: true }).click();
  const transfer = page.getByRole('dialog', { name: 'Transfer funds' });
  await expect(transfer.getByRole('button', { name: digits[0] ?? '', exact: true })).toBeVisible();
  await transfer.getByLabel('Receiver number').fill(digits[1] ?? '');
  await transfer.getByRole('button', { name: 'Find', exact: true }).click();
  await expect(transfer.getByText(`${name} 1`, { exact: false })).toBeVisible();
  await transfer.getByRole('button', { name: 'Next', exact: true }).click();
  await transfer.getByLabel('Amount', { exact: true }).fill('5.00');
  await transfer.getByLabel('Reason').fill('Numbered transfer acceptance');
  await transfer.getByRole('button', { name: 'Review transfer' }).click();
  // R11 adds an explicit acknowledgement; keep the scenario valid after that slice.
  const acknowledgement = transfer.getByRole('checkbox', {
    name: /I confirmed the amount and the recipient.s number with the subscriber/,
  });
  await acknowledgement.check();
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith('/transferBalance') && response.request().method() === 'POST',
  );
  await transfer.getByRole('button', { name: /^Transfer 5.00 / }).click();
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

  // retire what this run created in the catalog, so the stack does not keep it for sale
  const retire = async (path: string, data: Record<string, unknown>) => {
    const response = await page.request.patch(path, {
      headers: { 'X-Bss-Request': '1', 'Content-Type': 'application/merge-patch+json' },
      data,
    });
    expect(response.status(), await response.text()).toBe(200);
  };
  await retire(`${catalog}/productOffering/${fixture.id}`, {
    lifecycleStatus: 'Retired',
    isSellable: false,
  });
  await retire(`${catalog}/productSpecification/${spec.id}`, { lifecycleStatus: 'Retired' });
});
