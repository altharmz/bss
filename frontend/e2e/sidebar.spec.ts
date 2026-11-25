import { expect, test } from '@playwright/test';
import { signIn } from './auth';

// #414: the seven-section sidebar fits a laptop screen. The nav scrolls, the operator
// footer stays in view, and the last item can be reached and opened.

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
]) {
  test(`sidebar fits at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/billing-accounts');
    await signIn(page);
    const nav = page.getByRole('navigation', { name: 'Main' });
    await expect(nav.locator('.section')).toHaveText([
      'Customers',
      'Catalog',
      'Resources',
      'Billing',
      'Prepaid',
      'Reports',
      'System',
    ]);
    await expect(page.locator('[data-ui="ui.shell.operator"]')).toBeInViewport({ ratio: 1 });
    const logging = nav.getByRole('link', { name: 'Logging', exact: true });
    await logging.scrollIntoViewIfNeeded();
    await expect(logging).toBeInViewport({ ratio: 1 });
    await expect(page.locator('[data-ui="ui.shell.operator"]')).toBeInViewport({ ratio: 1 });
    await logging.click();
    await expect(page).toHaveURL(/\/settings\/logging$/);
  });
}
