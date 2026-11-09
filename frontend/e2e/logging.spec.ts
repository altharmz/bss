import { expect, test, type Page } from '@playwright/test';
import { signIn } from './auth';

// Logging settings: an admin starts a 15-minute debug, sees DEBUG with the time left,
// stops it. (A user's missing Logging entry is checked in smoke.spec.ts, where the user's
// first password change happens.) The base level is left as it was found. The effective
// level can be DEBUG without a temporary debug when LOG_LEVEL=DEBUG, so "off" is the check.

const nav = (page: Page) => page.getByRole('navigation', { name: 'Main' });

test('admin starts and stops a temporary debug', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  await expect(page).toHaveURL(/\/subscribers$/);
  await nav(page).getByRole('link', { name: 'Logging', exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/logging$/);
  await expect(page.getByRole('heading', { name: 'Logging' })).toBeVisible();
  await page.getByRole('button', { name: 'Debug 15 min' }).click();
  await expect(page.getByText(/Debug until \d\d:\d\d:\d\d, 1[45] min left/)).toBeVisible();
  await expect(page.locator('.bss-badge', { hasText: 'DEBUG' })).toBeVisible();
  await page.getByRole('button', { name: 'Stop debug' }).click();
  await expect(page.getByText('off', { exact: true })).toBeVisible();
});
