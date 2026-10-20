import { expect, test, type Page } from '@playwright/test';
import { identity, signIn } from './auth';

// The e2e smoke over the seeded local stack (make seed): the built UI in a real browser,
// the real API behind it. Billing data stays read-only; authentication uses disposable
// local fixture identities, disabled and stripped of sessions after the run.

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
      .getByRole('navigation', { name: 'Operations' })
      .getByRole('link', { name: /Financial accounts/ })
      .locator('.count'),
  ).toHaveText(/^\d[\d,]*$/);
  // New subscriber opens and cancels (read-only: nothing is created)
  await page.getByRole('button', { name: '+ New subscriber' }).click();
  const create = page.getByRole('dialog', { name: 'New subscriber' });
  await expect(create.getByText('Given name')).toBeVisible();
  await create.getByRole('button', { name: 'Cancel' }).click();
  await expect(create).toBeHidden();
  await expect(page.getByRole('banner').getByText(context.businessDate)).toBeVisible();

  const olga = page.getByRole('row').filter({ hasText: 'Olga Kuznetsova' });
  await expect(olga).toContainText('−'); // a negative balance, with the real minus
  await olga.click();
  await expect(page.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeVisible();
  await expect(page.getByText('In arrears')).toBeVisible();
  await expect(page.getByText('Due on bills')).toBeVisible();

  // the seed puts Olga's oldest open bill on hold: its dialog offers the release only.
  // Read-only: nothing is clicked in it.
  const held = page.getByRole('row').filter({ hasText: 'onHold' }).first();
  await held.click();
  const bill = page.getByRole('dialog', { name: /^Bill BL-[0-9A-F]{8}$/ });
  await expect(bill.getByText('History')).toBeVisible();
  await expect(bill.getByRole('button', { name: 'Release' })).toBeVisible();
  await bill.getByRole('button', { name: 'Close' }).click();
  await expect(bill).toBeHidden();

  // Edit loads the subscriber's party, customer and account; cancelled, nothing is saved
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const edit = page.getByRole('dialog', { name: 'Edit subscriber' });
  await expect(edit.getByLabel('Account name')).not.toHaveValue(''); // loaded, any name
  await edit.getByRole('button', { name: 'Cancel' }).click();
  await expect(edit).toBeHidden();

  // the first charge comes from the backend's dry run. Any offering priced today will do:
  // the local catalog is also edited by hand during manual reviews, so no name is assumed
  // (an offering whose price starts later shows "—" instead of "/ mo").
  await page.getByRole('button', { name: '+ Add product' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add product' });
  await dialog.getByRole('radio').filter({ hasText: '/ mo' }).first().click();
  await expect(dialog.getByTestId('first-charge')).toContainText('RUB');
  await expect(dialog.getByText(/in the run of \d{4}-\d{2}-\d{2} · on the bill of/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toBeHidden();

  await page
    .getByRole('navigation', { name: 'Operations' })
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
    .getByRole('navigation', { name: 'Operations' })
    .getByRole('link', { name: /Billing runs/ })
    .click();
  await expect(page).toHaveURL(/\/billing-runs$/);
  await expect(page.getByRole('heading', { name: 'Billing runs' })).toBeVisible();
  const latest = page.getByRole('region', { name: 'Latest run' });
  await expect(latest).toBeVisible();
  await expect(latest.getByText(/^\d{4}-\d{2}-\d{2}$/)).toBeVisible();
  await expect(page.getByText(/^Showing \d+ of \d+$/)).toBeVisible();
  await page.getByRole('row').filter({ hasText: 'latest' }).click();
  const run = page.getByRole('dialog', { name: /^Run for \d{4}-\d{2}-\d{2}$/ });
  await expect(run.getByText(/^Showing \d+ of \d+$/)).toBeVisible();
  await run.getByText('Close', { exact: true }).click(); // the footer button, not the ×
  await expect(run).toBeHidden();

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
  await expect(page.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New user' }).click();
  await expect(page.getByRole('dialog').getByLabel('Initial password')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page
    .getByRole('button', { name: `Edit ${identity('admin').username}`, exact: true })
    .click();
  await expect(page.getByRole('dialog').getByRole('checkbox')).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel' }).click();
});
