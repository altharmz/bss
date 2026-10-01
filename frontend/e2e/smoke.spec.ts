import { expect, test, type Page } from '@playwright/test';

// The e2e smoke over the seeded local stack (make seed): the built UI in a real browser,
// the real API behind it. Read-only - nothing is ordered or changed - so it is repeatable.

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

test('subscribers, a card with its first-charge preview, the catalog', async ({
  page,
  request,
}) => {
  const problems = watch(page);
  const context = (await (await request.get('/internal/v1/context')).json()) as {
    businessDate: string;
  };

  await page.goto('/');
  await expect(page).toHaveURL(/\/subscribers$/);
  await expect(page.getByRole('heading', { name: 'Subscribers' })).toBeVisible();
  await expect(page.getByRole('banner').getByText(context.businessDate)).toBeVisible();

  const olga = page.getByRole('row').filter({ hasText: 'Olga Kuznetsova' });
  await expect(olga).toContainText('−'); // a negative balance, with the real minus
  await olga.click();
  await expect(page.getByRole('heading', { name: /Olga Kuznetsova/ })).toBeVisible();
  await expect(page.getByText('In arrears')).toBeVisible();

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

  // a deep link is answered by index.html (client-side route)
  await page.goto('/catalog');
  await expect(page.getByRole('heading', { name: 'Product catalog' })).toBeVisible();

  expect(problems).toEqual([]);
});

test('the API is reached on the same origin, as JSON', async ({ request }) => {
  const response = await request.get('/internal/v1/context');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('application/json');
  const list = await request.get('/tmf-api/accountManagement/v5/billingAccount?limit=1');
  expect(list.status()).toBe(200);
  expect(Number(list.headers()['x-total-count'])).toBeGreaterThan(0);
});
