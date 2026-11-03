import { expect, test, type Page } from '@playwright/test';
import { identity, signIn } from './auth';

// The e2e smoke over the seeded local stack (make seed): the built UI in a real browser,
// the real API behind it. Billing data stays read-only; authentication uses disposable
// local fixture identities, disabled and stripped of sessions after the run.
//
// Exception: the prepaid test below changes data (a 10.00 cash top-up and one order for
// Pavel Orlov). That is accepted because this suite runs against a disposable seeded stack
// (make web-e2e seeds it first; make clean && make up && make seed gives a fresh one).

/** Everything that would mean the page is broken although it rendered. */
function watch(page: Page): string[] {
  const problems: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
  page.on('response', (response) => {
    if (response.status() >= 500) problems.push(`${response.status()} ${response.url()}`);
  });
  return problems;
}

test('subscribers, a card with its first-charge preview, the catalog', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  const problems = watch(page);
  await expect(page).toHaveURL(/\/subscribers$/);
  const context = (await (await page.request.get('/internal/v1/context')).json()) as {
    businessDate: string;
  };
  await expect(page.getByRole('heading', { name: 'Subscribers' })).toBeVisible();
  await expect(
    page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: /Subscribers/ })
      .locator('.count'),
  ).toHaveText(/^\d[\d,]*$/);
  await expect(
    page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: 'Accounts', exact: true }),
  ).toBeVisible();
  // New subscriber opens and cancels (read-only: nothing is created)
  await page.getByRole('button', { name: 'New subscriber', exact: true }).click();
  const create = page.getByRole('dialog', { name: 'New subscriber' });
  await expect(create.getByText('Given name')).toBeVisible();
  await create.getByRole('button', { name: 'Cancel' }).click();
  await expect(create).toBeHidden();
  await page.getByRole('link', { name: 'Numbers', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Numbers', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New number', exact: true }).click();
  const numberDialog = page.getByRole('dialog', { name: 'New number' });
  await numberDialog.getByLabel('Number', { exact: true }).fill('+12025550123');
  await expect(numberDialog.getByRole('button', { name: 'Create number' })).toBeDisabled();
  await numberDialog.getByLabel('Number', { exact: true }).fill('12025550123');
  await expect(numberDialog.getByRole('button', { name: 'Create number' })).toBeEnabled();
  await numberDialog.getByRole('button', { name: 'Cancel' }).click();
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: /Subscribers/ })
    .click();
  await expect(page.getByRole('banner').getByText(context.businessDate)).toBeVisible();

  const olga = page.getByRole('row').filter({ hasText: 'Olga Kuznetsova' });
  await expect(olga).toContainText('−'); // a negative balance, with the real minus
  await olga.click();
  await expect(page.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeVisible();
  await expect(page.getByText('In arrears')).toBeVisible();
  await expect(
    page.locator('[data-ui="ui.subscriber.moneyStrip"]').getByText('Due on bills'),
  ).toBeVisible();
  await page.getByRole('tab', { name: /^Payments/ }).click();
  await expect(page.locator('[data-ui="ui.subscriber.refunds"]')).toBeVisible();
  await page.getByRole('tab', { name: /^Bills/ }).click();
  await expect(page.locator('[data-ui="ui.subscriber.billRequests"]')).toBeVisible();
  await page.getByRole('button', { name: 'Bill now', exact: true }).click();
  const demandDialog = page.getByRole('dialog', { name: 'Bill now' });
  await expect(demandDialog.getByText(/from tomorrow onward/)).toBeVisible();
  await demandDialog.getByText('Close', { exact: true }).click();

  // the seed puts Olga's oldest open bill on hold: its dialog offers the release only.
  // Read-only: nothing is clicked in it.
  const held = page.getByRole('row').filter({ hasText: 'On hold' }).first();
  await held.click();
  const bill = page.getByRole('dialog', { name: /^Bill BL-[0-9A-F]{8}$/ });
  await expect(bill.getByText('History')).toBeVisible();
  await expect(bill.getByRole('button', { name: 'Release' })).toBeVisible();
  await bill.getByRole('button', { name: 'Close' }).click();
  await expect(bill).toBeHidden();

  await page.getByRole('tab', { name: 'Overview', exact: true }).click();
  // Edit loads the subscriber's party, customer and account; cancelled, nothing is saved
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const edit = page.getByRole('dialog', { name: 'Edit subscriber' });
  await expect(edit.getByLabel('Account name')).not.toHaveValue(''); // loaded, any name
  await edit.getByRole('button', { name: 'Cancel' }).click();
  await expect(edit).toBeHidden();

  // the first charge comes from the backend's dry run. Any offering priced today will do:
  // the local catalog is also edited by hand during manual reviews, so no name is assumed
  // (an offering whose price starts later shows "—" instead of "/ mo").
  await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add product' });
  await dialog.getByRole('radio').filter({ hasText: '/ mo' }).first().click();
  await expect(dialog.getByTestId('first-charge')).toContainText('USD');
  await expect(dialog.getByText(/in the run of \d{4}-\d{2}-\d{2} · on the bill of/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden();

  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: /Product catalog/ })
    .click();
  await expect(page.getByRole('heading', { name: 'Product catalog' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'MOB-L', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '+ New offering' }).click();
  const newOffering = page.getByRole('dialog', { name: 'New product offering' });
  await expect(newOffering.getByLabel('Recurrence')).toBeEnabled();
  await newOffering.getByLabel('Recurrence').selectOption('weekly');
  await newOffering.getByLabel('Charge every').fill('2');
  await expect(newOffering.getByLabel('Recurrence')).toHaveValue('weekly');
  await expect(newOffering.getByLabel('Charge every')).toHaveValue('2');
  await newOffering.getByRole('button', { name: 'Cancel' }).click();

  // a deep link is answered by index.html (client-side route)
  await page.goto('/catalog');
  await expect(page.getByRole('heading', { name: 'Product catalog' })).toBeVisible();

  // billing runs: the seed billed every day, so there is a latest run and a history.
  // Read-only: nothing is caught up or retried here.
  await page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: /Billing runs/ })
    .click();
  await expect(page).toHaveURL(/\/billing-runs$/);
  await expect(page.getByRole('heading', { name: 'Billing runs' })).toBeVisible();
  const latest = page.getByRole('region', { name: 'Latest run' });
  await expect(latest).toBeVisible();
  await expect(latest.getByText(/^\d{4}-\d{2}-\d{2}$/)).toBeVisible();
  await expect(page.getByText(/^Showing \d+ of \d+$/)).toBeVisible();
  await page.getByRole('row').filter({ hasText: 'latest' }).click();
  const run = page.getByRole('region', { name: /^Run for \d{4}-\d{2}-\d{2}$/ });
  await expect(run.getByText(/^Showing \d+ of \d+$/)).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'latest' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);

  expect(problems).toEqual([]);
});

test('prepaid: Pavel Orlov tops up and orders, Vera Lebedeva waits for funds, commands list', async ({
  page,
}) => {
  await page.goto('/subscribers');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Subscribers', exact: true })).toBeVisible();
  const problems = watch(page);

  // 1. the card: KPIs and balance action rows, one detail
  await page.getByRole('row').filter({ hasText: 'Pavel Orlov' }).click();
  await expect(page.getByRole('heading', { name: /Pavel Orlov/ })).toBeVisible();
  const kpi = (label: string) =>
    page.locator('.money-figure').filter({ has: page.getByText(label, { exact: true }) });
  await expect(kpi('Ledger')).toBeVisible();
  await expect(kpi('Available')).toBeVisible();
  await expect(kpi('Paid through')).toContainText(/\d{4}-\d{2}-\d{2}/);
  await page.getByRole('tab', { name: 'Balance actions', exact: true }).click();
  const history = page.locator('[data-ui="ui.subscriber.prepaid.history"]');
  const actions = history.getByRole('row').filter({ hasText: /\d{4}-\d{2}-\d{2}/ });
  await expect(actions.nth(2)).toBeVisible(); // at least three balance actions
  await actions.first().click();
  const detail = page.getByRole('dialog', { name: 'Balance action' });
  await expect(detail.getByText('Kind', { exact: true })).toBeVisible();
  await detail.getByRole('button', { name: 'Close' }).first().click();
  await expect(detail).toBeHidden();

  // 2. top up 10.00 by cash: the toast, then the ledger moves
  const ledgerBefore = await kpi('Ledger').innerText();
  await page.getByRole('button', { name: 'Top up', exact: true }).first().click();
  const topup = page.getByRole('dialog', { name: 'Top up' });
  await topup.getByLabel('Amount').fill('10.00');
  await topup.getByRole('button', { name: 'cash', exact: true }).click();
  await topup.getByRole('button', { name: 'Top up', exact: true }).click();
  await expect(
    page.getByRole('status').filter({ hasText: /Top up: 10\.00 \w+ recorded/ }),
  ).toBeVisible();
  await expect(kpi('Ledger')).not.toHaveText(ledgerBefore);

  // 3. add a product starting today: the prepaid preview, then the order is accepted
  await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
  const add = page.getByRole('dialog', { name: 'Add product' });
  await add.getByRole('radio').filter({ hasText: '/ mo' }).first().click();
  await expect(add.locator('[data-ui="ui.addProduct.prepaidPreview"]')).toBeVisible();
  await expect(add.getByTestId('prepaid-required')).toContainText(/\d/);
  const shortfall = add.locator('[data-ui="ui.addProduct.topUpShortfall"]');
  if (await shortfall.isVisible()) {
    // the order would be refused for lack of funds: top up the shortfall from here
    await shortfall.click();
    const more = page.getByRole('dialog', { name: 'Top up' });
    await more.getByRole('button', { name: 'cash', exact: true }).click();
    await more.getByRole('button', { name: 'Top up', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Top up' })).toBeHidden();
    await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
    await page
      .getByRole('dialog', { name: 'Add product' })
      .getByRole('radio')
      .filter({ hasText: '/ mo' })
      .first()
      .click();
  }
  await page
    .getByRole('dialog', { name: 'Add product' })
    .getByRole('button', { name: /^Add / })
    .click();
  // accepted: the worker completes it (OrderWatch follows; no worker runs inside the test)
  await expect(
    page.getByText(/ordered: accepted, the billing worker completes it shortly/),
  ).toBeVisible();

  // 4. Vera Lebedeva: funding-suspended product with its Top up menu action
  await page.goto('/subscribers');
  await page.getByRole('row').filter({ hasText: 'Vera Lebedeva' }).click();
  await expect(page.getByRole('heading', { name: /Vera Lebedeva/ })).toBeVisible();
  await expect(page.getByText(/Waiting for funds: short/)).toBeVisible();
  await expect(page.locator('[data-ui="ui.subscriber.prepaid.shortfall"]')).toContainText(
    /renewal needs/,
  );
  const waitingProduct = page
    .locator('.bss-product-row')
    .filter({ hasText: 'Waiting for funds: short' })
    .first();
  await waitingProduct.getByRole('button', { name: 'Actions ▾', exact: true }).click();
  const productTopUp = page.locator('[data-ui="ui.subscriber.topUpProduct"]');
  await expect(productTopUp).toBeVisible();
  await productTopUp.click();
  const renewalTopUp = page.getByRole('dialog', { name: 'Top up', exact: true });
  await expect(renewalTopUp).toBeVisible();
  await renewalTopUp.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(renewalTopUp).toBeHidden();

  // 5. the Prepaid commands list
  await page.goto('/prepaid-commands');
  await expect(page.getByRole('heading', { name: 'Prepaid commands' })).toBeVisible();
  await expect(
    page.locator('[data-ui="ui.prepaidCommands"]').getByRole('row').nth(1),
  ).toBeVisible();
  expect(problems).toEqual([]);
});

test('refund dialog opens on an account with unused credit without recording a payout', async ({
  page,
}) => {
  await page.goto('/subscribers');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Subscribers', exact: true })).toBeVisible();
  const problems = watch(page);
  const response = await page.request.get('/tmf-api/accountManagement/v5/billingAccount?limit=100');
  expect(response.ok()).toBe(true);
  const accounts = (await response.json()) as {
    id: string;
    accountBalance: { amount: { value: number } }[];
  }[];
  let eligible: string | undefined;
  for (const account of accounts) {
    if ((account.accountBalance[0]?.amount.value ?? 0) <= 0) continue;
    const settlement = await page.request.get(
      `/internal/v1/billingAccount/${account.id}/settlement`,
    );
    expect(settlement.ok()).toBe(true);
    const credit = (await settlement.json()) as { unallocatedCredit: { value: number } };
    if (credit.unallocatedCredit.value > 0) {
      eligible = account.id;
      break;
    }
  }
  expect(eligible, 'the seed includes an account with refundable credit').toBeTruthy();
  await page.goto(`/subscribers/${eligible}`);
  await page.getByRole('tab', { name: 'Account', exact: true }).click();
  await page.getByRole('button', { name: 'Refund unused credit' }).click();
  const dialog = page.getByRole('dialog', { name: 'Record refund' });
  await expect(dialog.getByText(/does not send money/)).toBeVisible();
  await expect(dialog.getByLabel('Refund amount')).not.toHaveValue('');
  await expect(dialog.getByRole('button', { name: 'Record refund' })).toBeEnabled();
  await dialog.getByText('Close', { exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(problems).toEqual([]);
});

test('the API is reached on the same origin, as JSON', async ({ request }) => {
  expect((await request.get('/internal/v1/context')).status()).toBe(401);
  const credentials = identity('admin');
  expect(
    (
      await request.post('/internal/v1/session', {
        data: credentials,
        headers: { 'X-Bss-Request': '1' },
      })
    ).status(),
  ).toBe(200);
  const response = await request.get('/internal/v1/context');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('application/json');
  const list = await request.get('/tmf-api/accountManagement/v5/billingAccount?limit=1');
  expect(list.status()).toBe(200);
  expect(Number(list.headers()['x-total-count'])).toBeGreaterThan(0);
});

test('user changes the initial password, signs in again and cannot manage users', async ({
  page,
}) => {
  const user = identity('user');
  await page.goto('/catalog');
  await signIn(page, 'user');
  await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible();
  await page.getByLabel('Current password').fill(user.password);
  await page.getByLabel('New password', { exact: true }).fill(user.password + '-changed');
  await page.getByLabel('Confirm password').fill(user.password + '-changed');
  await page.getByRole('button', { name: 'Change password', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill(user.password + '-changed');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Product catalog' })).toBeVisible();
  await page.goto('/users');
  await expect(page.getByRole('heading', { name: 'Not allowed' })).toBeVisible();
  expect((await page.request.get('/internal/v1/users')).status()).toBe(403);
  await page.locator('.bss-operator summary').click();
  await expect(page.getByRole('link', { name: 'Users', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  expect((await page.request.get('/internal/v1/session')).status()).toBe(401);
});

test('admin opens Users and its forms without changing users', async ({ page }) => {
  await page.goto('/users');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Users', level: 1, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New user' }).click();
  await expect(page.getByRole('dialog').getByLabel('Initial password')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page
    .getByRole('button', { name: `Edit ${identity('admin').username}`, exact: true })
    .click();
  await expect(page.getByRole('dialog').getByRole('checkbox')).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel' }).click();
});
