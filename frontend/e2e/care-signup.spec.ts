import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
function fixture(name: string, key: string): string {
  try {
    return JSON.parse(readFileSync('/web/.e2e/' + name + '.json', 'utf8'))[key] ?? '';
  } catch {
    return '';
  }
}
test('signup activates a prepaid eSIM at mobile width', async ({ page }) => {
  test.setTimeout(120000);
  await page.setViewportSize({ width: 360, height: 800 });
  await expect.poll(() => fixture('care-plan', 'id'), { timeout: 60000 }).not.toBe('');
  // The customer API uses the disposable DB and the real stub worker. Static assets
  // and the gateway document are served by the same built preview as the admin app.
  await page.route('**/self-care/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://e2e-care:8080' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await page.goto('/care/');
  await page.getByRole('button', { name: 'Sign up for a line' }).click();
  await page.getByLabel('Email', { exact: true }).fill('browser-signup@example.test');
  await page.getByLabel('Plan', { exact: true }).selectOption({ label: 'Plan 10' });
  await page.getByRole('button', { name: 'Send email code' }).click();
  await expect.poll(() => fixture('care-mail', 'code')).toMatch(/^\d{6}$/);
  await page.getByLabel('Email code').fill(fixture('care-mail', 'code'));
  await page.getByRole('button', { name: 'Verify email' }).click();
  for (const [label, value] of Object.entries({
    'First name': 'Grace',
    'Last name': 'Hopper',
    'Date of birth': '1990-12-09',
    'Street address': '1 Main St',
    City: 'Albany',
    'State (two letters)': 'NY',
    'ZIP code': '12207',
  }))
    await page.getByLabel(label).fill(value);
  await page.getByRole('button', { name: 'Verify identity' }).click();
  await page
    .frameLocator('iframe[title="Test payment gateway"]')
    .getByRole('button', { name: 'Use test card' })
    .click();
  await page.getByLabel('Create password').fill('browser-customer-password');
  await page.getByRole('button', { name: 'Pay and activate' }).click();
  await expect(page.getByText(/^LPA:1\$/)).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole('img', { name: 'eSIM activation code' })).toBeVisible();
  writeFileSync('/web/.e2e/care-finished', 'done');
  await expect.poll(() => fixture('care-plan', 'id')).toBe('');
});
